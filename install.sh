#!/usr/bin/env bash
# kicad-happy — global skill installer for macOS and Linux.
#
# Installs all 12 skills into DSH's user-global skill root (~/.dsh/skills)
# as symlinks to this checkout, so they load in every workspace and every
# DSH surface (desktop, web, TUI, headless).
#
# Usage:
#   ./install.sh               install / refresh the global skill links
#   ./install.sh --uninstall   remove the links (nothing else is touched)
#   ./install.sh --target DIR  install into DIR instead of ~/.dsh/skills
#
# Safe to re-run (idempotent). The symlinks track the live checkout, so
# `git pull` here updates the skills in place — no reinstall needed.

set -euo pipefail

SKILLS=(kicad spice emc datasheets bom digikey mouser lcsc element14 jlcpcb pcbway)
# The hub skill lives in skills/hub but is named "kicad-happy" (frontmatter).
HUB_NAME=kicad-happy

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET="${HOME}/.dsh/skills"
UNINSTALL=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --uninstall) UNINSTALL=1 ;;
    --target)
      [[ $# -ge 2 ]] || { echo "error: --target needs a directory" >&2; exit 2; }
      TARGET="$2"; shift ;;
    -h|--help)
      grep '^# ' "$0" | sed 's/^# \{0,1\}//' | tail -n +2
      exit 0 ;;
    *) echo "unknown option: $1 (see --help)" >&2; exit 2 ;;
  esac
  shift
done

link_skill() {  # $1 = source dir, $2 = link name
  local src="$REPO_ROOT/skills/$1" dst="$TARGET/$2"
  if [[ ! -f "$src/SKILL.md" ]]; then
    echo "error: $src/SKILL.md not found — is this a kicad-happy checkout?" >&2
    return 1
  fi
  ln -sfn "$src" "$dst"
  echo "  linked $dst -> $src"
}

if [[ $UNINSTALL -eq 1 ]]; then
  mkdir -p "$TARGET"
  echo "Removing global skill links from $TARGET"
  for skill in "${SKILLS[@]}"; do rm -f "$TARGET/$skill"; done
  rm -f "$TARGET/$HUB_NAME"
  echo "Done."
  exit 0
fi

mkdir -p "$TARGET"
echo "Installing kicad-happy skills into $TARGET"

for skill in "${SKILLS[@]}"; do
  link_skill "$skill" "$skill"
done
link_skill "hub" "$HUB_NAME"

# Verify: every link must resolve to a readable SKILL.md
echo
ok=0
fail=0
for name in "${SKILLS[@]}" "$HUB_NAME"; do
  if [[ -f "$TARGET/$name/SKILL.md" ]]; then
    ok=$((ok + 1))
  else
    echo "  WARNING: $TARGET/$name does not resolve to a SKILL.md" >&2
    fail=$((fail + 1))
  fi
done

echo
echo "Installed $ok/12 skills" $([[ $fail -eq 0 ]] || echo "(with $fail problem)")
echo
echo "Verify: start a DSH session in ANY workspace and ask the agent"
echo "  \"List the kicad-happy skills you can see.\""
echo "It should name 12: kicad-happy, kicad, emc, spice, datasheets, bom,"
echo "digikey, mouser, lcsc, element14, jlcpcb, pcbway."
[[ $fail -eq 0 ]] || exit 1
