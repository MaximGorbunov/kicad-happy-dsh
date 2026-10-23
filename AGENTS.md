# kicad-happy — DSH-native KiCad design-review suite

This workspace is a DeepSeek Harness (DSH) skill suite for KiCad 5–10
electronics design. All 12 skills in `skills/` (linked from `.dsh/skills/`)
are loaded into your skill catalog automatically.

## Skills

| Skill | Use for |
|---|---|
| `kicad-happy` | **Hub — start here.** Orchestrates the others for full reviews and multi-unit work |
| `kicad` | Schematic/PCB/Gerber/PDF analysis, subcircuit detection, design review |
| `emc` | EMC pre-compliance (rule checks over analyzer JSON) |
| `spice` | SPICE simulation of detected subcircuits (ngspice/LTspice/Xyce) |
| `datasheets` | Structured spec extraction from datasheet PDFs (schema-driven) |
| `bom` | BOM lifecycle: extract, enrich, price, order files |
| `digikey` / `mouser` / `lcsc` / `element14` | Distributor search, pricing, datasheet sync |
| `jlcpcb` / `pcbway` | Manufacturing prep: BOM/CPL, DFM, ordering rules |

## Standard review workflow

1. Load the `kicad` skill; run its analyzers on the project files:
   - `python3 skills/kicad/scripts/analyze_schematic.py <f>.kicad_sch --output sch.json`
   - `python3 skills/kicad/scripts/analyze_pcb.py <f>.kicad_pcb --full --output pcb.json`
2. Cross-check findings against datasheets (`datasheets` skill cache, if present).
3. `emc` and thermal analysis over the analyzer JSON when relevant.
4. `spice` for analog subcircuits when a simulator is installed.
5. Write the review report as markdown in the workspace and deliver it with
   the `present` tool.

## Environment notes

- Scripts are **Python 3.10+ stdlib only** — no pip install. If the system
  `python3` is below 3.10 (e.g. macOS default 3.9), use the DSH-bundled
  Python (3.12) returned by `load_workspace_dependencies`.
- All writes stay inside this workspace (DSH file sandbox). Analysis
  artifacts and caches go under the project directory (`.kicad-happy/…`).
- Long-running analysis (SPICE batch, big boards): run as a background bash
  job and collect with `job_output`.

## Multi-unit work

- Several independent sheets/MPNs/boards → one `subagent` call per unit in a
  single message (parallel, independent contexts).
- Large fan-out (whole-board BOM pricing, bulk datasheet extraction) →
  `workflow` script with phases and structured results.
- Supplier/ordering decisions → `ask_user_question`; recurring BOM
  re-checks → `schedule_create`.

## Docs

- `README.md` — install modes (workspace / global / pinned profile config)
- `install-guidance.md` — DSH troubleshooting (catalog, Python, sandbox)
- `how-it-works.md` — analyzer internals and data flow
- `VALIDATION.md` — test methodology and corpus
