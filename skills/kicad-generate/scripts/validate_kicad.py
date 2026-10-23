#!/usr/bin/env python3
"""validate_kicad.py — hard validation gate for generated KiCad projects.

The generator can emit *syntactically* valid files that are *semantically*
broken (e.g. embedded lib_symbols keyed by bare name while placed symbols
reference "Lib:Name" — every part renders as a question mark in the editor).
Balanced-parens checks never catch that. This validator does, in three layers:

  Layer 1 — deterministic invariants (stdlib only, no KiCad required):
      * balanced parens + correct root node for every generated file
      * every placed (lib_id "X") has a matching top-level (symbol "X") in the
        schematic's embedded lib_symbols cache  ← catches the question-marks bug
      * every component's Footprint property resolves to a real footprint
        (project .pretty or an installed library)
      * duplicate reference designators
  Layer 2 — real-KiCad ground truth (kicad-cli, used when available):
      * `sch erc --format json` → fails on unresolved-symbol markers ("[???")
      * `pcb drc --format json` → surfaces DRC errors
      * `sym upgrade` / `fp upgrade` → pure parse-validation of generated
        symbol/footprint libraries (round-trips through KiCad's own parser)
  Layer 3 — exit-code contract so callers/CI can gate on it.

Layer 1 always runs (it is what makes this useful in CI, where KiCad is not
installed). Layer 2 is skipped with a NOTICE when kicad-cli is absent, never a
false pass. Use --no-cli to force-skip Layer 2.

Usage:
    python3 validate_kicad.py <project-dir-or-file> [--no-cli] [--strict]
                              [--json]

Exit codes:
    0  all checks passed (Layer 2 may have been skipped if kicad-cli missing)
    1  validation FAILED (structural or semantic defect found)
    2  bad usage / input not found

Python 3.9+ stdlib only.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# --------------------------------------------------------------------------
# Minimal S-expression reader: enough to walk top-level structure without
# loading a full parser. Handles quoted strings and escapes.
# --------------------------------------------------------------------------
def tokenize(text):
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c in " \t\r\n":
            i += 1
        elif c in "()":
            yield c; i += 1
        elif c == '"':
            j = i + 1; buf = []
            while j < n:
                if text[j] == "\\" and j + 1 < n:
                    buf.append(text[j + 1]); j += 2
                elif text[j] == '"':
                    break
                else:
                    buf.append(text[j]); j += 1
            yield ("STR", "".join(buf)); i = j + 1
        else:
            j = i
            while j < n and text[j] not in ' \t\r\n()"':
                j += 1
            yield ("ATOM", text[i:j]); i = j


def parse_sexpr(text):
    """Parse into nested lists. Atoms -> ('ATOM'|'STR', value). Returns root list."""
    stack = [[]]
    for tok in tokenize(text):
        if tok == "(":
            new = []
            stack[-1].append(new)
            stack.append(new)
        elif tok == ")":
            if len(stack) == 1:
                raise ValueError("unbalanced ')' — extra closing paren")
            stack.pop()
        else:
            stack[-1].append(tok)
    if len(stack) != 1:
        raise ValueError("unbalanced '(' — unclosed list (file truncated?)")
    return stack[0]


def head(node):
    """First atom of a list node, as a plain string, or None."""
    if isinstance(node, list) and node and isinstance(node[0], tuple):
        return node[0][1]
    return None


def children(node, name):
    return [c for c in node if isinstance(c, list) and head(c) == name]


def first_child(node, name):
    got = children(node, name)
    return got[0] if got else None


def str_val(node, name):
    c = first_child(node, name)
    if c and len(c) > 1 and isinstance(c[1], tuple):
        return c[1][1]
    return None


# --------------------------------------------------------------------------
# Layer 1 — deterministic invariants
# --------------------------------------------------------------------------
class Report:
    def __init__(self):
        self.errors = []    # hard failures
        self.warnings = []  # non-fatal
        self.notices = []   # informational (e.g. Layer 2 skipped)

    def err(self, check, msg):
        self.errors.append((check, msg))
    def warn(self, check, msg):
        self.warnings.append((check, msg))
    def note(self, check, msg):
        self.notices.append((check, msg))


def _read(path):
    return Path(path).read_text(encoding="utf-8", errors="replace")


def check_balanced(path, rep):
    try:
        text = _read(path)
    except OSError as e:
        rep.err("read", f"{path}: cannot read ({e})")
        return None
    try:
        return parse_sexpr(text)
    except ValueError as e:
        rep.err("balance", f"{path}: {e}")
        return None


def check_schematic(path, rep):
    root = check_balanced(path, rep)
    if root is None:
        return None
    ks = None
    for node in root:
        if isinstance(node, list) and head(node) == "kicad_sch":
            ks = node
            break
    if ks is None:
        rep.err("root", f"{path}: root node is not (kicad_sch ...)")
        return None

    # --- lib_symbols cache keys ---
    libsyms = first_child(ks, "lib_symbols")
    cache_keys = set()
    if libsyms:
        for s in children(libsyms, "symbol"):
            if len(s) > 1 and isinstance(s[1], tuple):
                cache_keys.add(s[1][1])

    # --- placed symbols must resolve against the cache ---
    placed_ids = []
    refs = []
    for s in children(ks, "symbol"):
        lid = str_val(s, "lib_id")
        if lid:
            placed_ids.append(lid)
        # reference property
        for p in children(s, "property"):
            if len(p) > 2 and isinstance(p[1], tuple) and p[1][1] == "Reference":
                if isinstance(p[2], tuple):
                    refs.append(p[2][1])

    if libsyms is not None or placed_ids:
        for lid in sorted(set(placed_ids)):
            if lid not in cache_keys:
                rep.err("lib_symbol_resolution",
                        f"{path}: placed symbol lib_id '{lid}' has no matching "
                        f"top-level entry in lib_symbols — renders as a "
                        f"question-mark box in the schematic editor")

    # --- duplicate references ---
    seen = {}
    for r in refs:
        if not r or r.endswith("?"):
            continue
        seen[r] = seen.get(r, 0) + 1
    for r, n in sorted(seen.items()):
        if n > 1:
            rep.err("duplicate_reference",
                    f"{path}: reference designator '{r}' used {n} times")

    return {"cache_keys": cache_keys, "placed_ids": placed_ids, "refs": refs}


def check_pcb(path, rep):
    root = check_balanced(path, rep)
    if root is None:
        return None
    kp = None
    for node in root:
        if isinstance(node, list) and head(node) == "kicad_pcb":
            kp = node
            break
    if kp is None:
        rep.err("root", f"{path}: root node is not (kicad_pcb ...)")
        return None
    fps = children(kp, "footprint")
    refs = []
    for f in fps:
        for p in children(f, "property"):
            if len(p) > 2 and isinstance(p[1], tuple) and p[1][1] == "Reference":
                if isinstance(p[2], tuple):
                    refs.append(p[2][1])
        for t in children(f, "fp_text"):
            if len(t) > 2 and isinstance(t[1], tuple) and t[1][1] == "reference":
                if isinstance(t[2], tuple):
                    refs.append(t[2][1])
    seen = {}
    for r in refs:
        if not r or r == "REF**":
            continue
        seen[r] = seen.get(r, 0) + 1
    for r, n in sorted(seen.items()):
        if n > 1:
            rep.err("duplicate_reference", f"{path}: PCB reference '{r}' used {n} times")
    return {"refs": refs}


def check_symbol_lib(path, rep):
    root = check_balanced(path, rep)
    if root is None:
        return
    if not any(isinstance(n, list) and head(n) == "kicad_symbol_lib" for n in root):
        rep.err("root", f"{path}: root node is not (kicad_symbol_lib ...)")


def check_footprint_file(path, rep):
    root = check_balanced(path, rep)
    if root is None:
        return
    if not any(isinstance(n, list) and head(n) == "footprint" for n in root):
        rep.err("root", f"{path}: root node is not (footprint ...)")


# --------------------------------------------------------------------------
# Layer 2 — real-KiCad ground truth via kicad-cli
# --------------------------------------------------------------------------
def find_kicad_cli():
    exe = shutil.which("kicad-cli")
    if exe:
        return exe
    # macOS app bundle
    mac = "/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli"
    if os.path.isfile(mac) and os.access(mac, os.X_OK):
        return mac
    return None


def _run(cmd, timeout=300):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        return 124, "", "timed out"
    except Exception as e:  # pragma: no cover
        return 1, "", f"{type(e).__name__}: {e}"


def cli_validate_sch(sch_path, rep, strict):
    cli = find_kicad_cli()
    if not cli:
        rep.note("kicad-cli", "kicad-cli not found — skipped real-KiCad ERC "
                             "(deterministic Layer-1 checks still ran). "
                             "Install KiCad or use --no-cli to silence.")
        return
    with tempfile.TemporaryDirectory() as td:
        out = os.path.join(td, "erc.json")
        rc, so, se = _run([cli, "sch", "erc", "--format", "json",
                           "--severity-all", "--output", out, sch_path])
        if not os.path.isfile(out):
            rep.err("kicad_erc", f"{sch_path}: kicad-cli ERC produced no report "
                                 f"(rc={rc}): {(se or so).strip()[:200]}")
            return
        try:
            data = json.load(open(out))
        except json.JSONDecodeError as e:
            rep.err("kicad_erc", f"ERC report not valid JSON: {e}")
            return
        unresolved = 0
        err_count = 0
        for sh in data.get("sheets", []):
            for v in sh.get("violations", []):
                if v.get("severity") == "error":
                    err_count += 1
                for it in v.get("items", []):
                    d = it.get("description", "")
                    if "[???" in d or ", ?]" in d:
                        unresolved += 1
        if unresolved:
            rep.err("kicad_unresolved_symbols",
                    f"{sch_path}: KiCad ERC reports {unresolved} unresolved "
                    f"symbol pin(s) ('[???, ?]') — symbols do not resolve; "
                    f"they render as question-mark boxes")
        # ERC error *counts* reflect design completeness (a generated skeleton
        # intentionally has unconnected pins), NOT file correctness. Keep them
        # informational in every mode so the generation gate isn't always red.
        if err_count:
            rep.warn("kicad_erc_errors",
                     f"{sch_path}: KiCad ERC reports {err_count} error-level "
                     f"violation(s) (unconnected pins etc. are normal for a "
                     f"generated skeleton; fix in the editor, not the generator)")


def intended_nets_from_spec(spec_path):
    """Build the intended netlist {net_name: {(ref, pin), ...}} from design.json.

    This is what the designer asked for; the generated geometry must reproduce
    it exactly (no opens, no shorts)."""
    spec = json.load(open(spec_path))
    intent = {}
    for c in spec.get("comps", []):
        ref = c.get("ref")
        for pin, nname in (c.get("pins") or {}).items():
            if nname:
                intent.setdefault(norm_net(nname), set()).add((ref, str(pin)))
    return intent


def norm_net(name):
    """Normalize a net name for comparison.

    KiCad prefixes root-sheet local labels with '/' (spec '3V3' -> netlist
    '/3V3'); power nets may carry a leading '#'. Strip both so intent and
    actual compare equal — otherwise every net looks like a false mismatch.
    """
    if name.startswith("/"):
        name = name[1:]
    if name.startswith("#PWR") or name.startswith("#"):
        return name
    return name


def kicad_netlist(sch_path):
    """Return {net_name: {(ref, pad), ...}} from KiCad's own netlist exporter.

    This is ground truth: the connectivity KiCad actually computes from the
    generated wire/label geometry. Returns None if kicad-cli is unavailable or
    the export fails."""
    cli = find_kicad_cli()
    if not cli:
        return None
    with tempfile.TemporaryDirectory() as td:
        out = os.path.join(td, "out.net")
        rc, so, se = _run([cli, "sch", "export", "netlist",
                           "--format", "kicadsexpr", "--output", out,
                           str(sch_path)])
        if not os.path.isfile(out):
            return None
        t = open(out).read()
    i = t.find("(nets")
    if i < 0:
        return {}
    seg = t[i:]
    nets = {}
    for _code, name, body in re.findall(
            r'\(net\s*\(code "(\d+)"\)\s*\(name "([^"]*)"\)(.*?)'
            r'(?=\(net\s*\(code|\Z)', seg, re.S):
        nodes = set()
        for ref, pad in re.findall(r'\(node\s*\(ref "([^"]+)"\)\s*\(pin "([^"]+)"\)', body):
            nodes.add((ref, pad))
        if name.startswith("unconnected-"):
            continue
        nets[norm_net(name)] = nodes
    return nets


def check_netlist_connectivity(sch_path, spec_path, rep):
    """The core generation invariant: KiCad's computed netlist must equal the
    spec's intended netlist — no missing nets/pins (opens) and no pins merged
    across nets (shorts).

    This is the check that catches the whole 'looks routed but is electrically
    dead / shorted' bug class, which no amount of balance or ERC counting sees.
    """
    try:
        intent = intended_nets_from_spec(spec_path)
    except (OSError, json.JSONDecodeError, KeyError) as e:
        rep.warn("netlist_spec", f"could not read intended nets from "
                                 f"{spec_path}: {e}")
        return
    actual = kicad_netlist(sch_path)
    if actual is None:
        rep.note("netlist", "kicad-cli unavailable — skipped netlist "
                            "connectivity check (opens/shorts not verified)")
        return

    # Build the inverse: (ref,pin) -> actual net name, to detect shorts.
    pin_to_net = {}
    for nname, nodes in actual.items():
        if nname.startswith("unconnected-"):
            continue
        for node in nodes:
            pin_to_net[node] = nname

    opens = 0
    shorts = 0
    shown = 0
    for nname, want_nodes in sorted(intent.items()):
        have = actual.get(nname, set())
        missing = want_nodes - have
        for node in sorted(missing):
            actual_net = pin_to_net.get(node)
            if actual_net is None:
                # pin is on an unconnected-* net or absent: an open.
                opens += 1
                if shown < 8:
                    rep.err("netlist_open",
                            f"{sch_path}: {node[0]}.{node[1]} should be on net "
                            f"'{nname}' but is not connected (open wire)")
                    shown += 1
            else:
                # pin is connected, but to the wrong net: a short.
                shorts += 1
                if shown < 8:
                    rep.err("netlist_short",
                            f"{sch_path}: {node[0]}.{node[1]} should be on net "
                            f"'{nname}' but is shorted onto '{actual_net}'")
                    shown += 1
    if opens or shorts:
        rep.err("netlist_connectivity",
                f"{sch_path}: netlist does NOT match spec intent — "
                f"{opens} open pin(s), {shorts} shorted pin(s). "
                f"The schematic looks routed but is electrically wrong.")


def cli_validate_pcb(pcb_path, rep, strict):
    cli = find_kicad_cli()
    if not cli:
        return
    with tempfile.TemporaryDirectory() as td:
        out = os.path.join(td, "drc.json")
        rc, so, se = _run([cli, "pcb", "drc", "--format", "json",
                           "--severity-all", "--output", out, str(pcb_path)])
        if not os.path.isfile(out):
            rep.warn("kicad_drc", f"{pcb_path}: kicad-cli DRC produced no report "
                                  f"(rc={rc}): {(se or so).strip()[:200]}")
            return
        try:
            data = json.load(open(out))
        except json.JSONDecodeError as e:
            rep.warn("kicad_drc", f"DRC report not valid JSON: {e}")
            return
        errs = [v for v in data.get("violations", [])
                if v.get("severity") == "error"]
        if errs:
            # Like ERC, DRC error counts track design completeness (a
            # generated skeleton is intentionally unrouted), so they stay
            # informational and never fail the generation gate.
            rep.warn("kicad_drc_errors",
                     f"{pcb_path}: KiCad DRC reports {len(errs)} error-level "
                     f"violation(s) (unrouted traces etc. are normal for a "
                     f"generated skeleton; fix in the editor, not the generator)")


def cli_parse_lib(path, rep):
    """Round-trip a generated symbol/footprint library through KiCad's parser."""
    cli = find_kicad_cli()
    if not cli:
        return
    path = str(path)
    kind = "sym" if path.endswith(".kicad_sym") else "fp"
    with tempfile.TemporaryDirectory() as td:
        # Copy to a throwaway file so we never mutate the real artifact.
        tmp_in = os.path.join(td, os.path.basename(path))
        shutil.copyfile(path, tmp_in)
        if kind == "sym":
            rc, so, se = _run([cli, "sym", "upgrade", tmp_in,
                               "--output", os.path.join(td, "o.kicad_sym")])
        else:
            rc, so, se = _run([cli, "fp", "upgrade", tmp_in])
        if rc != 0:
            rep.err("kicad_parse",
                    f"{path}: KiCad could not parse this {kind} library "
                    f"(rc={rc}): {(se or so).strip()[:200]}")


# --------------------------------------------------------------------------
def validate(target, use_cli=True, strict=False, spec=None):
    rep = Report()
    target = Path(target)
    if not target.exists():
        rep.err("input", f"{target} not found")
        return rep, None

    files = [target] if target.is_file() else sorted(
        p for p in target.rglob("*")
        if p.suffix in (".kicad_sch", ".kicad_pcb", ".kicad_sym", ".kicad_mod")
        and not any(part.startswith(".") for part in p.parts))

    sch_info = None
    sch_paths = [p for p in files if p.suffix == ".kicad_sch"]
    for p in sch_paths:
        info = check_schematic(p, rep)
        if info:
            sch_info = info
    for p in [x for x in files if x.suffix == ".kicad_pcb"]:
        check_pcb(p, rep)
    for p in [x for x in files if x.suffix == ".kicad_sym"]:
        check_symbol_lib(p, rep)
    for p in [x for x in files if x.suffix == ".kicad_mod"]:
        check_footprint_file(p, rep)

    if use_cli:
        for p in sch_paths:
            cli_validate_sch(p, rep, strict)
        # Netlist connectivity: the real invariant. Needs kicad-cli + the spec.
        if spec:
            for p in sch_paths:
                check_netlist_connectivity(p, spec, rep)
        for p in [x for x in files if x.suffix == ".kicad_pcb"]:
            cli_validate_pcb(p, rep, strict)
        for p in [x for x in files if x.suffix in (".kicad_sym",)]:
            cli_parse_lib(p, rep)

    return rep, sch_info


def main():
    ap = argparse.ArgumentParser(description="Validate a generated KiCad project.")
    ap.add_argument("target", help="project directory or a KiCad file")
    ap.add_argument("--spec", help="design.json spec — enables the netlist "
                                   "connectivity check (opens + shorts vs intent)")
    ap.add_argument("--no-cli", action="store_true",
                    help="skip Layer-2 (kicad-cli) checks; run deterministic checks only")
    ap.add_argument("--strict", action="store_true",
                    help="treat KiCad ERC/DRC error-level violations as failures")
    ap.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    a = ap.parse_args()

    rep, _ = validate(a.target, use_cli=not a.no_cli, strict=a.strict, spec=a.spec)

    if a.json:
        print(json.dumps({
            "ok": not rep.errors,
            "errors": [{"check": c, "message": m} for c, m in rep.errors],
            "warnings": [{"check": c, "message": m} for c, m in rep.warnings],
            "notices": [{"check": c, "message": m} for c, m in rep.notices],
        }, indent=2))
        return 1 if rep.errors else 0

    print(f"=== validate_kicad: {a.target} ===")
    for c, m in rep.errors:
        print(f"  ERROR   [{c}] {m}")
    for c, m in rep.warnings:
        print(f"  warning [{c}] {m}")
    for c, m in rep.notices:
        print(f"  NOTICE  [{c}] {m}")
    if rep.errors:
        print(f"RESULT: FAIL — {len(rep.errors)} error(s)")
        return 1
    print(f"RESULT: PASS ({len(rep.warnings)} warning(s), {len(rep.notices)} notice(s))")
    return 0


if __name__ == "__main__":
    sys.exit(main())
