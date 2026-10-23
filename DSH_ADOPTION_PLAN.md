# kicad-happy → DeepSeek Harness (DSH) adoption plan

**Goal:** make this repository a first-class, DSH-only skill suite. Every integration
surface is verified against the installed DSH build (desktop `0.2.1-rc.2`,
runtime payload `723702…`) by reading the shipped source (`app.asar` →
`@deepseek-ai/dsh-skill*`, `dsh-web-app` presets, `dsh-home-paths`,
`dsh-desktop-host`, `dsh` CLI) and by observing the running process.

Nothing in this plan requires pushing or committing; it is a work order to be
executed on your fork.

---

## 1. Current state (verified today)

- The repo is a multi-agent skill suite: 11 skills under `skills/`
  (`bom`, `datasheets`, `digikey`, `element14`, `emc`, `jlcpcb`, `kicad`,
  `lcsc`, `mouser`, `pcbway`, `spice`), each `SKILL.md` + deterministic
  Python 3.10+ stdlib-only scripts + references/prompts/schemas.
- Per-agent manifests exist for Claude Code (`.claude-plugin/`), Codex
  (`.codex-plugin/`), Cursor (`.cursor/`), opencode (`.opencode/`),
  Antigravity (root `plugin.json`), Gemini (`GEMINI.md`), and a cross-agent
  marketplace (`.agents/plugins/marketplace.json`), plus `llms.txt`,
  `install-guidance.md`, and a deterministic GitHub Action (`action.yml`).
- **DSH already loads all 11 skills.** In this session (workspace = this
  repo) every skill appears in the DSH skill catalog, and the running DSH
  process has `skills/*/SKILL.md` of all 11 skills open for reading
  (verified via `lsof` on the process serving the GUI). So the SKILL.md
  convention, frontmatter, and relative script paths are already
  DSH-compatible.
- The scripts' "deterministic Python → JSON → model reasoning" split maps
  directly onto DSH's model (auditable tool output + agent reasoning).

## 2. Verified DSH integration surface

### 2.1 Skill discovery (from `@deepseek-ai/dsh-skill-filesystem`)

Scanned roots per session, lower rank wins on name collision:

| Rank | Root | Scope |
|------|------|-------|
| 100 | `<project>/.dsh/skills/` | project (highest priority) |
| 200 | `<project>/.agents/skills/` | project |
| 300 | `customSkillDirs` (preset/profile config) | configured |
| 400 | `~/.dsh/skills/` | user-global |
| 500 | `~/.agents/skills/` | user-global |
| 600 | bundled dir (`DSH_BUNDLED_SKILL_DIR`) | host-bundled (office skills) |

- `<project>` = nearest ancestor of the session cwd containing `.git`.
- The user profile (`~/.dsh/profiles/desktop/cordis.patch.yml`) currently has
  **no** skill-filesystem row, and `~/.dsh/skills`, `~/.agents/skills`,
  `<project>/.dsh/skills`, `<project>/.agents/skills` do not exist — yet the
  running desktop session scans `<workspace>/skills/`. Conclusion: the
  desktop's managed agent preset supplies a `customSkillDirs` entry for the
  workspace `skills/` dir. That is a *deployment-specific* behavior, not a
  documented DSH default — the plan below removes that dependency.

### 2.1a Cross-profile / cross-surface matrix (verified in 0.2.1-rc.2 source)

Profiles shipped by `dsh-app-boot`: `desktop` (Electron-managed), `web`
(dsh-base + dsh-web-app), `headless` (dsh-base + dsh-headless), `acp`,
`sdk`, `sdk-minimal`, plus any custom profile (default: dsh-base only).
"dsh tui" is **not** a shipped profile — it is the community TUI
([ccch1mneyyy/dsh-TUI](https://github.com/ccch1mneyyy/dsh-TUI)), which boots
its own profile (`dsh-base` → TUI cordis patch → **agent preset** → DSH
services), i.e. the same preset-owned discovery architecture as web.

| Surface | skill-filesystem row | Default roots (incl. `<project>/.dsh/skills`)? |
|---|---|---|
| Desktop GUI | managed agent preset row (runtime-verified to also scan `<workspace>/skills`) | yes |
| Web — `standard` / `cordis` / `ptc` presets | preset row, default config | yes |
| Web — `minimal` preset | **no skill rows at all** (stripped kernel: persona + fs + shell) | no (by design) |
| dsh-TUI profile (`dsh-tui`) | preset row, default config | yes |
| headless / acp / sdk / plain custom (dsh-base only) | base host row (web-app *disables* it only because presets own discovery) | yes |

Consequence: `<project>/.dsh/skills` (Phase 1) is the stable, version- and
profile-independent discovery anchor. The only surface that will not show the
skills is the intentionally stripped `minimal` preset, which is not a
supported target for this suite.

### 2.2 SKILL.md contract (from `parseSkillFile`)

Frontmatter (YAML) requirements:

- `name` — required, must match `^[a-z0-9]+(?:-[a-z0-9]+)*$` (kebab-case).
- `description` — required, non-empty (this is what the catalog shows).
- `whenToUse` — optional string (extra trigger guidance).
- `disable-model-invocation: true` — optional; hides skill from the model
  (user-invocable only).
- `user-invocable: false` — optional; the inverse.
- `metadata` — optional free-form object (carried to the model-facing
  summary).
- Legacy keys (`modelInvocable`, `userInvocable`, `disableModelInvocation`)
  are rejected — do not use them.
- Body: everything after the closing `---`. On load, DSH renders
  `<skill_content>` and tells the model the **skill directory is the resource
  base** — relative paths (`scripts/…`, `references/…`) resolve against it.
  This matches the repo's existing relative references.

### 2.3 Configuration & extension points

- Profile patches: `~/.dsh/profiles/desktop/cordis.patch.yml` (id-targeted
  config overrides / `insert` rows). This is where users wire per-machine
  config (model providers, MCP servers, tool config).
- **MCP servers**: registered as `@deepseek-ai/dsh-mcp-client` rows
  (verified shape: `serverName`, `transport: streamable-http`, `url`,
  `headers`, `toolCallTimeoutMs`, `failOnStartupError`). The GUI exposes them
  via `list_mcp_resources` / `read_mcp_resource` / tool calls.
- Profile plugins: `dsh plugin --profile <name> add <package>` — pnpm
  packages that patch the composition (services/tools/UI). Heavier than
  needed here; not required for this adoption.
- CLI: `dsh --profile <name> …`, `dsh headless "<task>"` (one-shot agent run,
  prints result, exits) — the hook for scripted/CI agent execution.

### 2.4 Runtime & agent capabilities the skills can rely on

- Bundled runtimes exposed by `load_workspace_dependencies`: Node 24, pnpm,
  **Python 3.12** with numpy/pandas/python-docx/python-pptx/openpyxl/Pillow/
  lxml/XlsxWriter. The repo's stdlib-only scripts run on this unmodified.
- File sandbox: this deployment runs `workspace-write` — agent writes are
  confined to the session workspace; anything outside prompts (approval
  policy: ask). All repo scripts already write inside the project, so they
  are sandbox-clean.
- First-class agent tools for deep workflows: `skill` (load),
  `subagent` / `subagent_fork` (independent context per child; parallel
  launches in one message), `workflow` (scripted fan-out at scale),
  `schedule_create` (recurring reminders), `present` (deliver files as
  cards), `ask_user_question` (decisions), `todo_write`, plan mode
  (`exit_plan_mode`), background jobs (`bash run_in_background` +
  `job_output`/`job_kill`).
- Caveat: macOS system `python3` is 3.9 (below the 3.10 minimum noted in
  `install-guidance.md`). On DSH, the fix is the bundled runtime Python —
  this must be documented in the skills (see Phase 2).

## 3. Work plan

### Phase 0 — fork + baseline (you) ✅

1. Fork the repo.
2. Baseline check (5 min, in DSH): open the fork as a workspace, start a
   session, confirm all 11 skills appear in the catalog (they do today on
   upstream; re-verify post-refactor in Phase 5).

### Phase 1 — DSH-native repo layout ✅ (done; committed `a64728a`)

**Add**

- `.dsh/skills` → `../skills` (symlink). Pins discovery to DSH's verified
  top-priority project root (rank 100), independent of whatever preset the
  desktop currently uses. `skills/` stays the single source of truth.
- `AGENTS.md` (project-level DSH onboarding doc, replaces `GEMINI.md`'s role):
  what the repo is, the 3 install modes, the end-to-end workflow, sandbox
  notes, Python-notes.

**Remove (other-agent throwaway — no verified DSH consumer)**

- `.claude-plugin/` (marketplace.json, plugin.json)
- `.codex-plugin/`
- `.cursor/`
- `.opencode/`
- `.agents/` (entire dir — decision: delete; no DSH repo-plugin loader found
  in the shipped build; `.agents/skills` *is* a DSH root but this repo doesn't
  need it once `.dsh/skills` exists)
- `GEMINI.md`
- `plugin.json` (root, Antigravity schema)
- `llms.txt` content is replaced (see "rewrite" below; keep or delete the
  file — recommend keeping a DSH-only 1-page summary)

**Rewrite**

- `README.md` — DSH-first:
  - Install mode A (workspace): clone, open as a DSH workspace → skills
    auto-discovered via `.dsh/skills`.
  - Install mode B (global): `ln -s <repo>/skills ~/.dsh/skills` (or symlink
    per skill) → available in every workspace.
  - Install mode C (pinned): add a `skill-filesystem` config override with
    `customSkillDirs: [<absolute repo>/skills]` to the profile patch (show
    the exact YAML row).
  - Capability table (11 skills), quick-start example, CI/Action link,
    validation summary link.
- `install-guidance.md` → DSH troubleshooting: skill not in catalog (no
  `.git` ancestor / wrong cwd; frontmatter errors; name rank collisions —
  e.g. a bundled skill shadowing a project skill), Python < 3.10 on macOS
  → use DSH bundled runtime via `load_workspace_dependencies`, SPICE
  simulators, sandbox/approval behavior on first network call, how to
  verify a skill load (`skill` tool result shows `<skill_content>` +
  resource base).
- `CONTRIBUTING.md` — "add a skill" section restated in DSH terms:
  frontmatter contract from §2.2, kebab-case naming, rank/precedence,
  keep scripts stdlib-only, run the metadata checker + regression suite.
- `.github/scripts/check_skill_metadata.py` — replace the Codex-1024-cap
  logic with a DSH-conformance check mirroring `parseSkillFile`: name
  regex, non-empty description, no legacy invocation keys, at most the
  known keys. Keep the CI job; it becomes the "DSH loadability" gate.

### Phase 2 — DSH-native skill content ✅

- **New hub skill `skills/hub/SKILL.md`** (name: `kicad-happy`):
  the orchestrator that ties the 10 skills together using DSH primitives:
  - multi-sheet / multi-MPN work → one `subagent` call per unit, launched
    in a single message (parallel, independent contexts);
  - bulk work (whole-board BOM pricing, multi-datasheet extraction,
    multi-board audits) → `workflow` script with phases + structured
    results;
  - final deliverables (report md, gerbers, BOM csv) → `present`;
  - supplier/ordering decisions → `ask_user_question`;
  - recurring stock/price re-checks on a BOM → `schedule_create`;
  - long SPICE runs → background bash job + `job_output`.
- **Rewrite `skills/datasheets/references/dispatch-claude-code.md` →
  `dispatch-dsh.md`**: same plan/scout/task pipeline, dispatched with DSH
  `subagent` (one tool call per pending task in one message; orchestrator
  validates each JSON result against the task schema and writes cache
  files). For very large plans, point to the `workflow` tool instead.
- **`dispatcher-contract.md`**: drop Claude-specific fields
  (`model_id: claude-…` → generic DSH model id; `model_tier` → optional
  generic), keep the contract agent-neutral.
- **Script string fixes**: `plan_extraction.py` (2 places referencing
  `dispatch-claude-code.md`), and any `model_id claude-*` examples in
  `datasheet_*` scripts → generic/DSH.
- **Per-skill touch-ups** (11 × small edits):
  - body/references: replace "Claude Code" / `Agent` tool / claude-specific
    workflow phrasing with DSH tool names (`subagent`, `todo_write`, plan
    mode via `exit_plan_mode`, `present`).
  - add a short "DSH runtime notes" block to the top-level skills:
    Python interpreter selection (system `python3` may be < 3.10 on macOS →
    DSH bundled Python via `load_workspace_dependencies`), all writes stay
    in the session workspace (sandbox), cache dirs under the project
    (`.kicad-happy/…`).
- **Docs refresh**: `how-it-works.md`, `github-action.md`,
  `datasheet-extraction.md`, `emc-precompliance.md`, `spice-integration.md`,
  `example-report-*.md` — strip multi-agent framing, keep content.
- `CHANGELOG.md` / `release-notes.md`: `3.0.0` entry — "DSH-native release".

### Phase 3 — deep integration: MCP server (in first release) ✅

Ship a stdio MCP server (single Python file, stdlib-only) that wraps the
core analyzers and distributors as MCP tools: `analyze_schematic`,
`analyze_pcb`, `analyze_emc`, `extract_bom`, `search_part` (per
distributor), `datasheet_status`. Then document the profile wiring:

```yaml
- insert:
    - id: mcp-kicad-happy
      name: '@deepseek-ai/dsh-mcp-client'
      config:
        serverName: kicad-happy
        transport: stdio
        command: python3 <repo>/mcp/server.py
```

Value: any DSH workspace (not just this repo's sessions) gets the analyzer
capabilities as tools, mirroring how this deployment already runs the
Home Assistant MCP. Skills remain the in-repo experience; MCP is the
global one. **Decision: include in the first release.**

MCP server implementation notes (keep the stdlib-only promise):
- `mcp/server.py` — single file, `jsonrpc` over stdin/stdout, no deps.
- Tools: `analyze_schematic`, `analyze_pcb`, `analyze_emc`,
  `analyze_thermal`, `extract_bom`, `search_part` (per distributor:
  digikey/mouser/lcsc/element14), `datasheet_status`.
- Each tool shells out to the existing `skills/*/scripts/*.py` — the MCP
  server is a thin adapter; all logic stays in the tested scripts.
- Paths are always resolved inside the caller's workspace (sandbox-clean).
- Document both wiring shapes in `AGENTS.md`: stdio row (above) and, where a
  long-lived server is preferred, `streamable-http` with `url`/`headers`
  (the shape your Home Assistant row already uses).

### Phase 4 — GitHub Action ✅

- Keep `action.yml` + `action/` as-is: it is deterministic Python (no LLM)
  and agent-agnostic; it keeps PR review working without any agent
  installation.
- Add `action/examples/dsh-headless.yml` (documented, opt-in): for
  self-hosted runners that have DSH installed, run
  `dsh headless "Analyze this KiCad project using the kicad skill…"` and
  post the rendered report. *Verify `dsh headless` is available outside the
  macOS app before advertising this; the CLI currently boots from the
  desktop app bundle.*
- Keep `.github/workflows/ci.yml` Python tests unchanged (the 2.7M-assertion
  corpus is the real regression net) + the new DSH frontmatter gate from
  Phase 1.

### Phase 5 — verification checklist (on the fork, per surface)

Core (run once):

1. Fresh session with workspace = fork → all 12 skills (11 + hub) in the
   catalog; no frontmatter warnings.
2. `skill` tool: load each skill; confirm `<skill_content>` renders and the
   resource base path is the skill directory; relative `scripts/…` paths
   resolve.
3. End-to-end review: sample KiCad project → schematic + PCB analysis →
   EMC → report → `present` card.
4. Datasheets pipeline: scout → plan → parallel `subagent` dispatch →
   schema validation → cache round-trip.
5. Hub workflow: one `workflow` run fanning out ≥ 3 independent units.
6. MCP: from a session whose workspace is *not* the repo: one MCP tool
   call succeeds.
7. CI: frontmatter gate + full Python suite green.

Cross-surface matrix (each item: open the fork, confirm the 12-skill
catalog, load one skill, run one analyzer script):

| Surface | How to verify |
|---|---|
| Desktop GUI | current deployment; fresh session in the fork |
| Web profile | `dsh --profile web` (template: dsh-base + dsh-web-app), standard preset |
| dsh-TUI | `dsh-tui` (profile `dsh-tui`; dsh-base + TUI patch + agent preset) |
| Headless | `dsh --profile headless "load the kicad skill and list its scripts"` |
| Plain custom profile | `dsh --profile <fresh>` (dsh-base only → base host skill row) |

Not supported by design: web `minimal` preset (no skill rows — stripped
kernel). Documented as a limitation, not a bug.

## 4. Explicit throwaway list

| Path / file | Reason |
|---|---|
| `.claude-plugin/` | Claude Code only |
| `.codex-plugin/` | Codex only |
| `.cursor/` | Cursor only |
| `.opencode/` | opencode only |
| `GEMINI.md` | Gemini/Antigravity only (replaced by `AGENTS.md`, shipped) |
| `plugin.json` (root) | Antigravity plugin schema |
| `.agents/plugins/marketplace.json` | Cross-agent marketplace; no DSH consumer verified |
| `install-guidance.md` (multi-agent sections) | Replaced by DSH-specific doc |
| `llms.txt` (multi-agent content) | Replaced by DSH-only summary |
| `dispatch-claude-code.md` | Replaced by `dispatch-dsh.md` |
| Codex description-cap logic in `check_skill_metadata.py` | Replaced by DSH conformance checks |

Kept: `skills/` (rewritten per Phase 2), `action/` + `action.yml`,
`.github/` (CI + new gate), `LICENSE`, `CHANGELOG.md`, `VALIDATION.md`,
example reports and long-form docs (refreshed).

## 5. Decisions (locked) & remaining risks

**Locked decisions:**

1. MCP server: **included in the first release** (Phase 3).
2. `.agents/`: **deleted entirely**.
3. Distribution: **fork-only** — the fork becomes the DSH edition; no PR
   back to upstream.
4. Name: stays **kicad-happy**.
5. Compatibility target: **all DSH surfaces** (desktop, web, dsh-TUI,
   headless, plain profiles) via the `.dsh/skills` anchor — see matrix in
   §2.1a. Exception: web `minimal` preset (no skills by design).

**Version scope, stated honestly:** every finding in this plan is verified
against the installed build (desktop `0.2.1-rc.2`, runtime payload
`723702…`). "All versions" is guaranteed structurally, not by testing every
release: the integration surface is the shipped `dsh-skill-filesystem`
contract (roots + frontmatter + resource base), which is profile- and
preset-level and has no per-version fork in the shipped code; profiles that
mount no preset fall back to the base host row with the same defaults.
Anything running a different DSH version is covered by the Phase 5
per-surface checklist (2-minute catalog check) rather than by assumption.

**Remaining risks:**

- **Discovery dependency:** today's catalog works via the managed desktop
  preset's `customSkillDirs`; after Phase 1 the `.dsh/skills` symlink makes
  discovery explicit and reproducible (verified rank-100 root). Re-test in a
  *fresh* DSH profile after refactoring (Phase 5 matrix).
- **Python floor:** macOS system Python 3.9 < 3.10. DSH's bundled 3.12
  runtime fixes this; skills must say so (Phase 2).
- **`dsh headless` in CI:** availability outside the desktop app needs
  verification before promising DSH-driven CI reviews (Phase 4 fallback:
  keep deterministic action).
- **Sandbox prompts:** first network call per distributor API will trigger
  the approval flow on first use; document in `AGENTS.md`.
- **dsh-TUI third-party:** its profile/preset composition evolves upstream
  (it targets 0.2.1-rc.2 and keeps older hosts on compatibility paths). If a
  future TUI version switches presets, the Phase 5 TUI row catches it; the
  repo ships no TUI-specific code, so drift cost is a checklist re-run.
