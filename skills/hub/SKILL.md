---
name: kicad-happy
description: Full-scope KiCad design review hub — orchestrates the kicad, emc, spice, datasheets, bom, digikey, mouser, lcsc, element14, jlcpcb, and pcbway skills into one workflow. Use for "review my board", "full design review", "ready to order?", multi-board or multi-sheet audits, and any task that spans analysis + sourcing + fabrication (BOM with pricing, order files, fab readiness). Loads the right domain skills and coordinates parallel subagents, workflows, report delivery, and recurring BOM checks.
whenToUse: Use whenever a task spans more than one domain skill — a complete review, board-to-board comparison, or a sourcing-to-fabrication pipeline. For a single narrow job (e.g. "search LCSC for C1234"), load that domain skill directly instead.
---

# kicad-happy — review hub

This is the orchestrator for the suite. It does not parse KiCad files itself —
it loads the right domain skill(s), runs their deterministic scripts, and
coordinates the DSH primitives (subagents, workflows, present, schedules)
around them.

## Skill map

| Task shape | Load |
|---|---|
| Parse / review a schematic or PCB | `kicad` |
| EMC risk / pre-compliance | `emc` (consumes kicad analyzer JSON) |
| Simulate an analog subcircuit | `spice` (consumes kicad analyzer JSON) |
| Extract / verify datasheet specs | `datasheets` (feeds `kicad`, `emc`, `spice`) |
| BOM: extract, price, export, order | `bom` |
| Find / price parts at a distributor | `digikey`, `mouser`, `lcsc`, `element14` |
| Fab prep (BOM/CPL, DFM, ordering rules) | `jlcpcb`, `pcbway` |

## Standard full review

1. **Load `kicad`.** Run the analyzers (paths relative to the repo root):
   ```bash
   python3 skills/kicad/scripts/analyze_schematic.py <f>.kicad_sch --output <out>/sch.json
   python3 skills/kicad/scripts/analyze_pcb.py <f>.kicad_pcb --full --output <out>/pcb.json
   ```
   Artifacts go under the project (e.g. `.kicad-happy/analysis/`).
2. **Datasheet cross-check:** for ICs with findings, consult the
   `datasheets` skill cache (`<project>/datasheets/extracted/<MPN>.json`).
   On a miss, run its extraction pipeline (see `datasheets` skill) — dispatch
   scout + per-task subagents per `skills/datasheets/references/dispatch-dsh.md`.
3. **EMC:** `python3 skills/emc/scripts/analyze_emc.py -s <out>/sch.json -p <out>/pcb.json`.
   **Thermal:** `python3 skills/kicad/scripts/analyze_thermal.py -s <out>/sch.json -p <out>/pcb.json`.
4. **SPICE:** for detected analog subcircuits, load `spice` and run its
   simulator auto-detection (ngspice/LTspice/Xyce). Long batch runs:
   background bash job + `job_output`.
5. **BOM & sourcing** (when requested): load `bom`; it coordinates the
   distributor skills. Supplier/ordering decisions → `ask_user_question`.
6. **Fab prep** (when requested): load `jlcpcb` or `pcbway`; generate
   BOM/CPL and run their DFM checks.
7. **Report:** write one markdown review (findings ranked by severity,
   evidence + confidence per finding, power tree, next steps) and deliver it
   with the `present` tool.

## Multi-unit patterns (DSH)

- **Independent sheets / boards / MPNs:** one `subagent` call per unit,
  launched in a single message. Each subagent runs the same script sequence
  for its unit and returns a compact result (findings + JSON paths).
- **Large fan-out** (whole-board BOM pricing, bulk datasheet extraction,
  many boards): `workflow` script — phases per stage, `agent()` per unit,
  structured results.
- **Decisions** (supplier choice, order confirmation, which revisions to
  review): `ask_user_question`.
- **Recurring work** (re-check BOM stock/pricing weekly, re-run review on
  each new push the user asks about): `schedule_create`.
- **Deliverables** (report, BOM CSV, order file, gerbers): `present`.

## Rules

- Deterministic scripts first; the model reasons over their JSON, never over
  raw S-expressions.
- All writes stay inside the session workspace (DSH sandbox).
- Python: scripts need 3.10+ stdlib only. If the system `python3` is older,
  use the DSH-bundled Python from `load_workspace_dependencies`.
- Every reported finding carries the analyzer's confidence label and
  evidence source — don't invent findings the JSON doesn't support.
