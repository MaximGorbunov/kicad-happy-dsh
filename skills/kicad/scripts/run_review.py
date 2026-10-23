#!/usr/bin/env python3
"""run_review.py — one-command design review for a KiCad project.

Collapses the multi-step analyzer chain (schematic → PCB → cross → EMC →
thermal) into a single command that auto-discovers the project files, runs
every applicable analyzer in dependency order, and prints a COMPACT digest to
stdout while writing full JSON to an analysis directory.

Why this exists:
  * A full review otherwise requires chaining ~5-8 analyzer commands with the
    correct flags and ordering — a common failure point, especially for smaller
    models that follow instructions literally.
  * Full analyzer JSON is large (a 40-component board is ~380 KB). This tool
    prints a severity-ranked digest (~a few KB) so the agent reasons over
    findings, not raw S-expressions, and only dips into the JSON for detail.

It never fabricates results: each analyzer's real output is summarized, skipped
steps are named explicitly, and any analyzer that fails is reported as a gap.

Usage:
    python3 run_review.py <project-dir-or-file> [--out DIR] [--no-pcb]
                          [--standard fcc-class-b] [--top N] [--json]
                          [--pcb FILE] [--schematic FILE]

Exit codes:
    0  review completed (findings may still exist — see digest)
    2  bad input (project/file not found, or schematic failed to parse)
    3  an analyzer crashed unexpectedly (reported as a review gap)

Python 3.9+ stdlib only.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

# Make sibling analyzers importable regardless of cwd.
_HERE = Path(__file__).resolve().parent
_SKILL = _HERE.parent                      # .../skills/kicad
_REPO = _SKILL.parent.parent               # .../repo root
_EMC = _REPO / "skills" / "emc" / "scripts"

SEV_ORDER = {"error": 0, "critical": 0, "high": 1, "warning": 2, "medium": 3,
             "info": 4, "low": 5, "skip": 6}


def _banner(title: str) -> str:
    return f"\n=== {title} ==="


def _valid_root(path: Path, expect: str) -> bool:
    """True if the file's root S-expression node is `expect` (kicad_sch/pcb).

    Lets discovery skip junk/truncated files that sort first alphabetically
    instead of picking them and failing the whole review.
    """
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            # Read a bounded prefix; the root token is the first token.
            head = fh.read(4096)
    except OSError:
        return False
    stripped = head.lstrip()
    return stripped.startswith("(" + expect)


def find_project_files(target: Path, schematic: str | None, pcb: str | None):
    """Resolve the root schematic and PCB for a project dir or file."""
    sch = Path(schematic) if schematic else None
    pc = Path(pcb) if pcb else None

    if target.is_file():
        if target.suffix == ".kicad_sch" and sch is None:
            sch = target
        elif target.suffix == ".kicad_pcb" and pc is None:
            pc = target
        elif target.suffix == ".kicad_pro":
            stem = target.with_suffix("")
            if sch is None and stem.with_suffix(".kicad_sch").is_file():
                sch = stem.with_suffix(".kicad_sch")
            if pc is None and stem.with_suffix(".kicad_pcb").is_file():
                pc = stem.with_suffix(".kicad_pcb")
        directory = target.parent
    else:
        directory = target

    if directory and directory.is_dir():
        if sch is None:
            cands = [c for c in sorted(directory.glob("*.kicad_sch"))
                     if _valid_root(c, "kicad_sch")]
            # Prefer a schematic whose stem matches the directory or a .kicad_pro.
            if cands:
                sch = _prefer(cands, directory)
        if pc is None:
            cands = [c for c in sorted(directory.glob("*.kicad_pcb"))
                     if _valid_root(c, "kicad_pcb")]
            if cands:
                pc = _prefer(cands, directory)
    return sch, pc, directory


def _prefer(cands: list, directory: Path) -> Path:
    """Pick the most likely root file: stem matching the dir name or a project."""
    dname = directory.name.lower()
    pro_stems = {p.stem.lower() for p in directory.glob("*.kicad_pro")}
    for c in cands:
        stem = c.stem.lower()
        if stem == dname or stem in pro_stems:
            return c
    return cands[0]


def run(cmd: list[str]) -> tuple[int, str, str]:
    """Run a subprocess, capturing returncode/stdout/stderr. Never raises."""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
        return p.returncode, p.stdout, p.stderr
    except FileNotFoundError as e:
        return 127, "", f"interpreter not found: {e}"
    except subprocess.TimeoutExpired:
        return 124, "", "timed out after 900s"
    except Exception as e:  # pragma: no cover - defensive
        return 1, "", f"{type(e).__name__}: {e}"


def _sev_counts(findings: list) -> dict:
    counts: dict = {}
    for f in findings:
        s = (f.get("severity") or "info").lower()
        counts[s] = counts.get(s, 0) + 1
    return counts


def _rollup(label: str, ok: bool, data: dict | None, err: str, gaps: list,
            note: str = "") -> None:
    if not ok:
        gaps.append(f"{label}: {err.strip().splitlines()[0] if err.strip() else 'failed'}")
        print(f"  [SKIP] {label} — {err.strip().splitlines()[0] if err.strip() else 'failed'}")
        return
    if data is None:
        gaps.append(f"{label}: no output")
        print(f"  [SKIP] {label} — no output")
        return
    findings = data.get("findings", [])
    counts = _sev_counts(findings)
    errs = counts.get("error", 0) + counts.get("critical", 0)
    warns = counts.get("warning", 0) + counts.get("medium", 0)
    score = data.get("emc_score", data.get("score"))
    score_s = f", score {score}/100" if isinstance(score, (int, float)) else ""
    print(f"  [OK]   {label}: {len(findings)} findings "
          f"({errs} error, {warns} warning){score_s}" + (f" — {note}" if note else ""))


def _print_top(findings: list, top: int, source: str) -> None:
    ranked = sorted(findings,
                    key=lambda f: SEV_ORDER.get((f.get("severity") or "info").lower(), 9))
    shown = [f for f in ranked if (f.get("severity") or "info").lower() in
             ("error", "critical", "high", "warning", "medium")][:top]
    if not shown:
        return
    print(_banner(f"Top findings — {source}"))
    for f in shown:
        sev = (f.get("severity") or "info").upper()
        rid = f.get("rule_id") or f.get("detector") or ""
        summ = f.get("summary") or f.get("title") or ""
        comp = f.get("component") or f.get("ref") or ""
        loc = f" [{comp}]" if comp else ""
        print(f"  {sev:8} {rid}{loc}: {summ}")


def _text_digest(py: str, script: str, args: list[str]) -> str:
    """Ask an analyzer for its --text digest (compact, grouped)."""
    rc, out, _ = run([py, str(script)] + args + ["--text"])
    return out.strip() if rc == 0 and out.strip() else ""


def main() -> int:
    ap = argparse.ArgumentParser(
        description="One-command KiCad design review (schematic + PCB + cross + EMC + thermal).")
    ap.add_argument("project", help="Project directory, or a .kicad_sch/.kicad_pcb/.kicad_pro file")
    ap.add_argument("--out", default=None,
                    help="Output directory for full JSON (default: <project>/.kicad-happy/review)")
    ap.add_argument("--schematic", help="Explicit root schematic path (overrides discovery)")
    ap.add_argument("--pcb", help="Explicit PCB path (overrides discovery)")
    ap.add_argument("--no-pcb", action="store_true", help="Skip PCB-dependent analyzers")
    ap.add_argument("--standard", default="fcc-class-b",
                    help="EMC standard (default: fcc-class-b)")
    ap.add_argument("--top", type=int, default=15,
                    help="Number of top findings to show per analyzer (default: 15)")
    ap.add_argument("--json", action="store_true",
                    help="Also print the combined digest as JSON to stdout (for programmatic use)")
    args = ap.parse_args()

    py = sys.executable
    target = Path(args.project).expanduser().resolve()
    if not target.exists():
        print(f"Error: {args.project!r} not found. Pass a project directory or a "
              f".kicad_sch/.kicad_pcb/.kicad_pro file.", file=sys.stderr)
        return 2

    sch, pc, directory = find_project_files(target, args.schematic, args.pcb)
    if sch is None and pc is None:
        print(f"Error: no .kicad_sch or .kicad_pcb found under {target}. "
              f"Use --schematic/--pcb to point at them explicitly.", file=sys.stderr)
        return 2

    out_dir = Path(args.out) if args.out else (directory or target.parent) / ".kicad-happy" / "review"
    out_dir.mkdir(parents=True, exist_ok=True)

    gaps: list[str] = []
    results: dict = {}
    sch_json = out_dir / "schematic.json"
    pcb_json = out_dir / "pcb.json"
    cross_json = out_dir / "cross.json"
    emc_json = out_dir / "emc.json"
    th_json = out_dir / "thermal.json"

    print(_banner("kicad-happy review"))
    print(f"  schematic: {sch if sch else '(none — PCB-only)'}")
    print(f"  pcb:       {pc if pc and not args.no_pcb else '(none — schematic-only)'}")
    print(f"  outputs:   {out_dir}")
    print(_banner("Analyzers"))

    # 1. Schematic -----------------------------------------------------------
    if sch:
        rc, out, err = run([py, str(_HERE / "analyze_schematic.py"), str(sch),
                            "--output", str(sch_json)])
        data = None
        if rc == 0 and sch_json.is_file():
            try:
                data = json.loads(sch_json.read_text())
            except json.JSONDecodeError:
                err = "output is not valid JSON"
        elif rc == 2:
            # Input error (parse failure / wrong file type) — surface cleanly.
            print(f"  [FAIL] schematic: {err.strip()}", file=sys.stderr)
            return 2
        _rollup("schematic", rc == 0 and data is not None, data, err, gaps)
        results["schematic"] = data
    else:
        gaps.append("schematic: no .kicad_sch found")

    # 2. PCB -----------------------------------------------------------------
    if pc and not args.no_pcb:
        cmd = [py, str(_HERE / "analyze_pcb.py"), str(pc), "--full", "--output", str(pcb_json)]
        if sch and sch_json.is_file():
            cmd += ["--schematic", str(sch_json)]
        rc, out, err = run(cmd)
        data = None
        if rc == 0 and pcb_json.is_file():
            try:
                data = json.loads(pcb_json.read_text())
            except json.JSONDecodeError:
                err = "output is not valid JSON"
        elif rc == 2:
            print(f"  [FAIL] pcb: {err.strip()}", file=sys.stderr)
            return 2
        _rollup("pcb", rc == 0 and data is not None, data, err, gaps)
        results["pcb"] = data
    elif not pc:
        gaps.append("pcb: no .kicad_pcb found (skipped PCB, cross, layout EMC checks)")

    # 3. Cross-analysis ------------------------------------------------------
    if sch_json.is_file() and pcb_json.is_file():
        rc, out, err = run([py, str(_HERE / "cross_analysis.py"),
                            "-s", str(sch_json), "-p", str(pcb_json),
                            "-o", str(cross_json)])
        data = None
        if rc == 0 and cross_json.is_file():
            try:
                data = json.loads(cross_json.read_text())
            except json.JSONDecodeError:
                err = "output is not valid JSON"
        _rollup("cross-analysis", rc == 0 and data is not None, data, err, gaps)
        results["cross"] = data

    # 4. EMC -----------------------------------------------------------------
    emc_inputs = []
    if sch_json.is_file():
        emc_inputs += ["-s", str(sch_json)]
    if pcb_json.is_file():
        emc_inputs += ["-p", str(pcb_json)]
    if emc_inputs:
        rc, out, err = run([py, str(_EMC / "analyze_emc.py")] + emc_inputs +
                           ["--standard", args.standard, "-o", str(emc_json)])
        data = None
        if rc == 0 and emc_json.is_file():
            try:
                data = json.loads(emc_json.read_text())
            except json.JSONDecodeError:
                err = "output is not valid JSON"
        _rollup("emc", rc == 0 and data is not None, data, err, gaps,
                note=f"standard {args.standard}")
        results["emc"] = data

    # 5. Thermal -------------------------------------------------------------
    if sch_json.is_file() and pcb_json.is_file():
        rc, out, err = run([py, str(_HERE / "analyze_thermal.py"),
                            "-s", str(sch_json), "-p", str(pcb_json),
                            "-o", str(th_json)])
        data = None
        if rc == 0 and th_json.is_file():
            try:
                data = json.loads(th_json.read_text())
            except json.JSONDecodeError:
                err = "output is not valid JSON"
        _rollup("thermal", rc == 0 and data is not None, data, err, gaps)

    # Digest -----------------------------------------------------------------
    for label, key, src in [("Schematic", "schematic", sch),
                            ("PCB", "pcb", pc),
                            ("Cross-analysis", "cross", None),
                            ("EMC", "emc", None)]:
        data = results.get(key)
        if data and data.get("findings"):
            _print_top(data["findings"], args.top, label)

    # Review gaps — never silently omitted.
    if gaps:
        print(_banner("Review gaps (not analyzed — investigate or state in report)"))
        for g in gaps:
            print(f"  - {g}")

    # Deeper detail: point the agent at the full JSON rather than dumping it.
    print(_banner("Full data (dip in only as needed)"))
    for name, p in [("schematic", sch_json), ("pcb", pcb_json),
                    ("cross", cross_json), ("emc", emc_json), ("thermal", th_json)]:
        if p.is_file():
            print(f"  {name:10} {p}  ({p.stat().st_size} bytes)")
    print("\nTo read a specific section without loading the whole file:")
    print("  python3 -c \"import json;d=json.load(open('<file>'));"
          "print(json.dumps(d['<key>'],indent=2))\"")
    print("For a compact per-analyzer digest: add --text to any analyzer command.")

    if args.json:
        compact = {}
        for k, v in results.items():
            if v:
                compact[k] = {"summary": v.get("summary"),
                              "findings": v.get("findings", [])}
        compact["gaps"] = gaps
        print(json.dumps(compact, indent=2))

    return 0


if __name__ == "__main__":
    sys.exit(main())
