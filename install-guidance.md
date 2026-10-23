# Installation & Upgrade Guidance (DeepSeek Harness)

This document is the single reference for installing, upgrading, and
troubleshooting kicad-happy in DeepSeek Harness (DSH). It is written for AI
agents but is equally useful for humans.

**If you are an AI agent helping a user install or upgrade kicad-happy, read
this file in full before taking any action.** It covers DSH skill discovery,
frontmatter rules, precedence, Python version issues, and sandbox behavior.

---

## Requirements

| Requirement | Details |
|-------------|---------|
| DeepSeek Harness | Desktop, web, TUI (community `dsh-TUI`), or headless profile. All mount a skill-filesystem provider with default roots |
| Python | 3.10+ (3.8 EOL Oct 2024; CI tests 3.10 and 3.12). DSH bundles Python 3.12 — no install needed on macOS |
| Dependencies | None required. All analysis scripts use Python stdlib only |
| Optional packages | `requests` (HTTP), `playwright` (JS-heavy PDF sites), `pdftotext` (PDF extraction) |
| Optional system tools | `ngspice` / `LTspice` / `Xyce` (SPICE simulation, auto-detected) |
| KiCad | Not required at runtime. Analyzes saved `.kicad_sch` / `.kicad_pcb` files directly |
| KiCad versions | 5, 6, 7, 8, 9, 10 (all supported) |

### Skills included

All 12 skills: the `kicad-happy` hub (`skills/hub/`) plus `kicad`, `spice`,
`emc`, `datasheets`, `bom`, `digikey`, `mouser`, `lcsc`, `element14`,
`jlcpcb`, `pcbway`. When installing manually, include all 12. The `datasheets`
skill is consumed by kicad, emc, spice, and thermal.

### Optional API credentials

These enable component sourcing and datasheet downloading. The skills work
without them but fall back to web search.

| Service | Environment variable(s) | Auth type |
|---------|------------------------|-----------|
| DigiKey | `DIGIKEY_CLIENT_ID`, `DIGIKEY_CLIENT_SECRET` | OAuth 2.0 |
| Mouser | `MOUSER_SEARCH_API_KEY` | API key |
| element14 | `ELEMENT14_API_KEY` | API key |
| LCSC | None required | Free community API |

---

## DSH skill discovery — how it works

DSH discovers skills from these roots (verified in DSH source; lower rank wins
on a name collision):

| Rank | Root | Scope |
|------|------|-------|
| 100 | `<project>/.dsh/skills/` | project — highest priority |
| 200 | `<project>/.agents/skills/` | project |
| 300 | `customSkillDirs` (preset/profile config) | configured |
| 400 | `~/.dsh/skills/` | user-global |
| 500 | `~/.agents/skills/` | user-global |
| 600 | bundled skill dir (`DSH_BUNDLED_SKILL_DIR`) | host-bundled |

- `<project>` is the nearest ancestor of the session's working directory
  that contains a `.git` — i.e. a git checkout root.
- Each skill is a directory containing `SKILL.md`. DSH reads the YAML
  frontmatter for `name` (shown in the catalog) and `description`, and treats
  the skill directory as the **resource base**: relative paths inside the
  skill (e.g. `scripts/…`) resolve against it.
- Frontmatter contract (DSH rejects anything else):
  - `name` — required, kebab-case (`^[a-z0-9]+(-[a-z0-9]+)*$`).
  - `description` — required, non-empty.
  - `whenToUse` — optional trigger guidance.
  - `disable-model-invocation: true` — optional; hides the skill from the
    model (user-invocable only). `user-invocable: false` — inverse.
  - `metadata` — optional free-form object.
  - Legacy keys (`modelInvocable`, `userInvocable`, `disableModelInvocation`)
    are **rejected** — do not use them.
- The repo ships `.dsh/skills → skills` (rank 100), so a workspace install
  works in every DSH profile that mounts a skill-filesystem provider with
  default roots (desktop, web standard/cordis/ptc, dsh-TUI, headless, plain
  profiles). Exception: the web `minimal` agent preset mounts no skill rows
  at all — skills cannot load there by design.
- DSH also auto-loads `AGENTS.md` (and `CLAUDE.md`) from the project root
  into session context; this repo's `AGENTS.md` carries the skill map and
  workflow.
- The file watcher picks up skill changes live (new/changed skills appear in
  the catalog without restarting the session).

## Install

### Mode A — workspace install (recommended)

```bash
git clone git@github.com:MaximGorbunov/kicad-happy-dsh.git
cd kicad-happy-dsh
```

Open that directory as a workspace in DSH and start a session. All 12 skills
appear in the skill catalog automatically (via the shipped `.dsh/skills`
link). Verify by asking the agent to list its skills, or load one with the
`skill` tool.

### Mode B — global install (every workspace)

macOS / Linux:

```bash
REPO=<path-to-this-repo>
mkdir -p ~/.dsh/skills
for skill in kicad spice emc datasheets bom digikey mouser lcsc element14 jlcpcb pcbway; do
  ln -sf "$REPO/skills/$skill" ~/.dsh/skills/$skill
done
ln -sf "$REPO/skills/hub" ~/.dsh/skills/kicad-happy
```

Windows (PowerShell 7+, Developer Mode — or use junctions; see
[Windows symlink issues](#windows-symlink-issues)):

```powershell
$repo = (Get-Location).Path
New-Item -ItemType Directory -Force "$HOME\.dsh\skills" | Out-Null
"hub","kicad","spice","emc","datasheets","bom","digikey","mouser","lcsc","element14","jlcpcb","pcbway" | ForEach-Object {
  New-Item -ItemType SymbolicLink -Path "$HOME\.dsh\skills\$_" -Target "$repo\skills\$_" -Force | Out-Null
}
```

### Mode C — pinned profile config

Add a `skill-filesystem` override to your DSH profile patch
(`~/.dsh/profiles/desktop/cordis.patch.yml` for the desktop profile; the
equivalent profile dir for other profiles):

```yaml
- id: skill-filesystem
  name: '@deepseek-ai/dsh-skill-filesystem'
  config:
    customSkillDirs:
      - /absolute/path/to/kicad-happy-dsh/skills
```

### Optional: MCP server (global, tool-level access)

The repo ships `mcp/server.py`, a stdio MCP server wrapping the analyzers and
distributor search. Wire it into a DSH profile patch to expose the tools in
any workspace:

```yaml
- insert:
    - id: mcp-kicad-happy
      name: '@deepseek-ai/dsh-mcp-client'
      config:
        serverName: kicad-happy
        transport: stdio
        command: python3 /absolute/path/to/kicad-happy-dsh/mcp/server.py
```

---

## Upgrade

- **Workspace install:** `git pull` in the checkout. The `.dsh/skills` link
  and the watcher follow the live tree; changed skills re-appear in the
  catalog automatically.
- **Global symlinks:** `git pull` in the cloned repo — symlinks track the
  live checkout.
- **Pinned config / MCP:** no action; both read the live tree.

---

## Troubleshooting

### Skills not appearing in the catalog

1. Confirm the session's workspace root actually contains a `.git` directory
   (DSH finds the project root by walking up from the session cwd). A session
   started in a subfolder of a non-git directory won't see project skills.
2. Confirm `.dsh/skills` exists in the project root and resolves (it is a
   relative symlink to `../skills`).
3. Check each skill directory has a `SKILL.md` with valid frontmatter
   (`name` kebab-case, non-empty `description`, no legacy invocation keys).
   Run the conformance checker:
   ```bash
   python3 .github/scripts/check_skill_metadata.py
   ```
4. Name collision: if a skill with the same `name` exists in a higher-priority
   root (e.g. a bundled or project-level skill), the lower-priority one is
   hidden, not duplicated. Rename or remove the duplicate.
5. Web `minimal` preset: skills are unavailable by design (no skill rows in
   that preset). Use a standard preset.
6. New DSH version: run the quick check — start a session in the repo and
   confirm all 12 skills in the catalog.

### Python version errors

If you see `SyntaxError` on `str | None` or `dict[str, int]`:

- Your Python is below 3.10. Check with `python3 --version`.
- **On DSH:** use the bundled runtime Python (3.12) returned by the
  `load_workspace_dependencies` tool — it ships with numpy, pandas,
  python-docx, python-pptx, openpyxl, Pillow, lxml, XlsxWriter.
- Outside DSH: install Python 3.10+ (macOS: `brew install python@3.12`;
  older Debian/RHEL: backports/modules).

### DSH sandbox / approval behavior

- The file sandbox (this deployment: `workspace-write`) confines agent
  writes to the session workspace. All kicad-happy scripts already write
  inside the project (`.kicad-happy/…` artifacts, caches) — nothing changes.
- First network call to a distributor API (DigiKey/Mouser/element14/LCSC)
  may trigger DSH's approval flow depending on your policy (this
  deployment: `ask`). Approving once is per-session; nothing is cached
  outside the workspace.

### Script import errors

All scripts add their own directory to `sys.path`. If you see import errors
when running from a non-standard working directory, use absolute paths:

```bash
python3 /path/to/kicad-happy-dsh/skills/kicad/scripts/analyze_schematic.py board.kicad_sch
```

### API credential issues

- DigiKey OAuth requires a registered app at [developer.digikey.com](https://developer.digikey.com/).
  The first auth flow opens a browser for consent. Subsequent calls use a refresh token.
- Mouser and element14 keys are simple API keys — set the env var and go.
- LCSC needs no credentials (uses the jlcsearch community API).
- If no API keys are set, distributor skills fall back to web search guidance.

---

## Windows symlink issues

Windows symlinks have platform-specific friction:

| Scenario | Works? | Notes |
|----------|--------|-------|
| PowerShell 7+ with Developer Mode | Yes | Recommended approach |
| PowerShell 5.1 (ships with Windows) | No | Does not pass `SYMBOLIC_LINK_FLAG_ALLOW_UNPRIVILEGED_CREATE` even with Developer Mode |
| cmd.exe `mklink /D` with Developer Mode | Yes | Alternative to PowerShell 7 |
| Any shell, elevated (Run as Administrator) | Yes | Works but not ideal for daily use |

**Fallback: use junctions instead of symlinks.** Junctions work for
directories without requiring Developer Mode or elevation:

```powershell
"hub","kicad","spice","emc","datasheets","bom","digikey","mouser","lcsc","element14","jlcpcb","pcbway" | ForEach-Object {
  New-Item -ItemType Junction -Path "$HOME\.dsh\skills\$_" -Target "$(Get-Location)\skills\$_" -Force | Out-Null
}
```

**Fallback: copy instead of linking.** If symlinks and junctions both fail,
copy the skill directories. Upgrades then require re-copying after `git pull`.

---

## GitHub Action

Available as a GitHub Action for automated PR reviews. Every push or PR that
touches KiCad files gets a commit status check and a structured review comment.
See [github-action.md](github-action.md) for the full guide. The action is
deterministic (pure Python, no agent needed) and works on any runner.

For **AI-driven reviews inside DSH**, see the `dsh headless` example in
[github-action.md](github-action.md) — for self-hosted runners with DeepSeek
Harness installed, `dsh headless "<review task>"` runs the same skills and
prints the result.

### Basic workflow

Create `.github/workflows/kicad-review.yml`:

```yaml
name: KiCad Design Review
on:
  push:
    paths: ['**/*.kicad_sch', '**/*.kicad_pcb']
  pull_request:
    paths: ['**/*.kicad_sch', '**/*.kicad_pcb']

permissions:
  contents: read
  pull-requests: write
  statuses: write

jobs:
  review:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v6
      - run: sudo apt-get install -y ngspice poppler-utils
      - uses: MaximGorbunov/kicad-happy-dsh@v1
        id: analysis
      - uses: thollander/actions-comment-pull-request@v3
        if: github.event_name == 'pull_request'
        with:
          file-path: ${{ steps.analysis.outputs.report-path }}
          comment-tag: kicad-happy-review
          mode: upsert
```

### Diff-based PR reviews

Enable `diff-base: true` to show only what changed between the PR and the
base branch — component additions/removals, signal parameter shifts,
new/resolved findings:

```yaml
      - uses: MaximGorbunov/kicad-happy-dsh@v1
        id: analysis
        with:
          diff-base: true
```

### Secrets setup

| Secret | Where to get it | Required for |
|--------|----------------|-------------|
| `DIGIKEY_CLIENT_ID` / `DIGIKEY_CLIENT_SECRET` | [developer.digikey.com](https://developer.digikey.com/) | Datasheet download in CI |
| `MOUSER_SEARCH_API_KEY` | My Mouser → APIs | Datasheet download in CI |
| `ELEMENT14_API_KEY` | element14/Newark API portal | Datasheet download in CI |

Add secrets in your repo: Settings → Secrets and variables → Actions → New
repository secret.

### Action outputs

The `MaximGorbunov/kicad-happy-dsh@v1` step exposes these outputs for
downstream steps:

| Output | Description |
|--------|-------------|
| `report-path` | Path to the markdown report file |
| `schematic-json` | Path to schematic analysis JSON |
| `pcb-json` | Path to PCB analysis JSON (if PCB exists) |
| `emc-json` | Path to EMC analysis JSON (if both schematic and PCB exist) |
| `status` | `pass`, `warn`, or `fail` |
| `findings-count` | Total number of findings |
| `critical-count` | Number of critical/error findings |

---

## Standalone (no agent)

The scripts work without any agent:

```bash
python3 skills/kicad/scripts/analyze_schematic.py board.kicad_sch --output sch.json
python3 skills/kicad/scripts/analyze_pcb.py board.kicad_pcb --full --output pcb.json
python3 skills/emc/scripts/analyze_emc.py -s sch.json -p pcb.json --output emc.json
```
