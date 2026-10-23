#!/usr/bin/env python3
"""Validate SKILL.md frontmatter against the DeepSeek Harness (DSH) contract.

Mirrors the shipped DSH skill-filesystem parser (parseSkillFile):
  - YAML frontmatter is required (first line `---`, closed by `---`);
    invalid YAML causes DSH to ignore the skill with a warning.
  - `name` is required, non-empty, and must match DSH's skill-name grammar:
      ^[a-z0-9]+(?:-[a-z0-9]+)*$   (kebab-case, lowercase)
  - `description` is required and non-empty (rendered verbatim in the
    session skill catalog; DSH has no hard cap, but this checker warns
    above WARN_LENGTH).
  - Legacy invocation keys are rejected by DSH (skill ignored):
      disableModelInvocation, modelInvocable, userInvocable
  - Canonical invocation keys (optional, boolean):
      disable-model-invocation, user-invocable
  - `whenToUse` (optional string) and `metadata` (optional object) are
    accepted; other unknown keys pass through harmlessly in DSH — this
    checker warns about them so typos don't hide.

Usage:
    python3 .github/scripts/check_skill_metadata.py        # all skills
    python3 .github/scripts/check_skill_metadata.py skills/bom/SKILL.md
    python3 .github/scripts/check_skill_metadata.py --warn-length 900

Exit codes:
    0  All skills conform to the DSH contract.
    1  One or more skills would be rejected by DSH (or have warnings).
    2  Usage error (bad path, missing file, etc.)

Wire as a local pre-commit hook:
    ln -sf ../../.github/scripts/check_skill_metadata.py .git/hooks/pre-commit

This script is also invoked by CI (see .github/workflows/ci.yml).

Zero dependencies — Python 3.10+ stdlib only.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

SKILL_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
LEGACY_KEYS = {"disableModelInvocation", "modelInvocable", "userInvocable"}
KNOWN_KEYS = {
    "name",
    "description",
    "whenToUse",
    "disable-model-invocation",
    "user-invocable",
    "metadata",
}
WARN_LENGTH = 900  # DSH has no cap; warn when description gets long
DEFAULT_WARN_LENGTH = WARN_LENGTH

_KEY_RE = re.compile(r"^([A-Za-z_][\w-]*):(.*)$")
_BLOCK_RE = re.compile(r"^[|>][+-]?\s*$")
_BOOL_TRUE = {"true", "yes", "on", "1"}
_BOOL_FALSE = {"false", "no", "off", "0"}


def _collapse(value: str) -> str:
    """Collapse folded/indented scalar whitespace into a single string."""
    return " ".join(value.split())


def parse_frontmatter(content: str) -> tuple[dict | None, str | None]:
    """Parse the YAML frontmatter of a SKILL.md into (data, error).

    Supports the subset DSH skill files use: top-level `key: value` pairs,
    block scalars (| and > with optional -/+ chomping), quoted values, and
    simple inline values. Nested structures are captured as raw indented
    text (only their presence is checked for `metadata`).
    """
    lines = content.split("\n")
    if not lines or lines[0].rstrip("\r") != "---":
        return None, "no YAML frontmatter (first line must be ---)"

    closing = None
    for i in range(1, len(lines)):
        if lines[i].rstrip("\r") == "---":
            closing = i
            break
    if closing is None:
        return None, "unterminated YAML frontmatter (no closing ---)"

    fm = lines[1:closing]
    data: dict[str, str] = {}
    i = 0
    while i < len(fm):
        line = fm[i]
        if not line.strip() or line.lstrip().startswith("#"):
            i += 1
            continue
        if line[0] in " \t":
            # Indented continuation not consumed by a block scalar → invalid.
            return None, f"unexpected indented line in frontmatter: {line.strip()!r}"
        m = _KEY_RE.match(line)
        if not m:
            return None, f"invalid frontmatter line: {line.strip()!r}"
        key, rest = m.group(1), m.group(2).strip()
        i += 1
        if rest in ("|", "|-", "|+", ">", ">-", ">+"):
            block: list[str] = []
            while i < len(fm) and (not fm[i].strip() or fm[i][0] in " \t"):
                block.append(fm[i].strip())
                i += 1
            joiner = " " if rest[0] == ">" else "\n"
            data[key] = joiner.join(b for b in block if b)
        elif rest == "" or rest == "|-" or rest == ">-":
            # Value on following indented lines (nested map/list, e.g. metadata).
            block = []
            while i < len(fm) and (not fm[i].strip() or fm[i][0] in " \t"):
                block.append(fm[i].strip())
                i += 1
            data[key] = " ".join(block)
        elif rest[0] in "'\"":
            quote = rest[0]
            value = rest[1:]
            if value.endswith(quote):
                value = value[:-1]
            i0 = i
            # Multiline quoted value: keep consuming until closing quote line.
            while i < len(fm) and quote not in value:
                value += " " + fm[i].strip()
                i += 1
            i = i0
            data[key] = value
        else:
            # Strip trailing comment on plain scalars (space + #).
            comment_idx = rest.find(" #")
            if comment_idx >= 0:
                rest = rest[:comment_idx].rstrip()
            data[key] = rest
    return data, None


def check_one(skill_md: Path, warn_length: int) -> tuple[str, list[str]]:
    """Return (skill_name, list of problem strings). Empty list = conforming."""
    try:
        content = skill_md.read_text(encoding="utf-8")
    except OSError as exc:
        return skill_md.parent.name, [f"could not read: {exc}"]

    data, err = parse_frontmatter(content)
    if data is None:
        return skill_md.parent.name, [f"DSH would ignore this skill: {err}"]

    problems: list[str] = []

    name = data.get("name", "").strip()
    if not name:
        problems.append("missing or empty `name` (required by DSH)")
    elif not SKILL_NAME_RE.match(name):
        problems.append(f"invalid skill name {name!r} — must be kebab-case "
                        f"matching ^[a-z0-9]+(?:-[a-z0-9]+)*$")

    description = _collapse(data.get("description", ""))
    if not description:
        problems.append("missing or empty `description` (required by DSH)")
    elif len(description) > warn_length:
        problems.append(
            f"description is {len(description)} chars (warn threshold "
            f"{warn_length} — DSH has no hard cap, but the catalog shows it "
            f"verbatim in every session)"
        )

    for legacy in sorted(LEGACY_KEYS & set(data)):
        canonical = (
            "disable-model-invocation"
            if legacy != "userInvocable"
            else "user-invocable"
        )
        problems.append(f"legacy key {legacy!r} rejected by DSH — use {canonical!r}")

    for key, value in data.items():
        if key in ("disable-model-invocation", "user-invocable"):
            v = value.strip().lower()
            if v not in _BOOL_TRUE and v not in _BOOL_FALSE:
                problems.append(f"{key!r} must be a boolean (true/yes/on/1 or "
                                f"false/no/off/0), got {value!r}")
        elif key not in KNOWN_KEYS:
            problems.append(f"unknown frontmatter key {key!r} (DSH tolerates it, "
                            f"but it looks like a typo)")

    return skill_md.parent.name, problems


def find_skill_mds(targets: list[str]) -> list[Path]:
    """Resolve targets to a list of SKILL.md files."""
    if not targets:
        repo_root = Path(__file__).resolve().parents[2]
        return sorted(repo_root.glob("skills/*/SKILL.md"))

    paths: list[Path] = []
    for t in targets:
        p = Path(t)
        if not p.exists():
            print(f"error: {t} does not exist", file=sys.stderr)
            sys.exit(2)
        if p.is_dir():
            paths.extend(sorted(p.glob("**/SKILL.md")))
        else:
            paths.append(p)
    return paths


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "targets",
        nargs="*",
        help="SKILL.md files or directories to check (default: all skills/)",
    )
    ap.add_argument(
        "--warn-length",
        type=int,
        default=DEFAULT_WARN_LENGTH,
        help=f"warn when description exceeds N chars (default: {DEFAULT_WARN_LENGTH})",
    )
    ap.add_argument(
        "--quiet",
        action="store_true",
        help="suppress per-skill OK lines (only print warnings/errors)",
    )
    args = ap.parse_args()

    skill_mds = find_skill_mds(args.targets)
    if not skill_mds:
        print("error: no SKILL.md files found", file=sys.stderr)
        return 2

    exit_code = 0
    rows: list[tuple[str, list[str]]] = []

    for skill_md in skill_mds:
        name, problems = check_one(skill_md, args.warn_length)
        if problems:
            exit_code = max(exit_code, 1)
        rows.append((name, problems))

    rows.sort(key=lambda r: r[0])

    if not args.quiet or exit_code != 0:
        print(f"\n{'Skill':<14}  Status")
        print(f"{'-' * 14}  {'-' * 30}")
        for name, problems in rows:
            if problems:
                print(f"{name:<14}  FAIL")
                for p in problems:
                    print(f"{'':<14}    - {p}", file=sys.stderr)
            else:
                print(f"{name:<14}  OK")
        print()

    if exit_code != 0:
        bad = sum(1 for _, p in rows if p)
        print(f"FAIL: {bad} skill(s) do not conform to the DSH frontmatter contract.",
              file=sys.stderr)

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
