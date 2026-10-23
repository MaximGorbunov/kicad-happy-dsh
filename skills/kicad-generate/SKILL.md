---
name: kicad-generate
description: >-
  Author (create) KiCad projects from scratch — generate valid .kicad_pro,
  .kicad_sch, .kicad_pcb, .kicad_sym, and a .pretty footprint directory from a
  JSON design spec.
  Use whenever the user wants to CREATE, generate, scaffold, or write a KiCad
  schematic/PCB (not just analyze an existing one), or to add custom symbols or
  footprints to a project. Covers the exact KiCad 10 file structures that load in
  the GUI and pass the kicad skill analyzers, plus verified pinouts for common
  parts. Pairs with the kicad skill: kicad-generate makes the files, kicad reviews
  them. Also for "make me a PCB", "generate the schematic", "new KiCad project",
  "build the board files", "write the footprint library", or turning a netlist /
  BOM / pin map into KiCad files.
---

# KiCad Authoring Skill

Generate a complete, valid KiCad project from a JSON design spec. This is the
**creation** complement to the `kicad` skill (which analyzes existing files).

Run all commands from the repository root.

**Requirements:** Python 3.10+ stdlib only. If `python3 --version` is below 3.10 (macOS ships 3.9), use the DSH-bundled Python returned by `load_workspace_dependencies`.

## Related skills

| Skill | Purpose |
|---|---|
| `kicad` | Review/analyze the generated files (ERC/DRC-style, nets, DFM) |
| `bom` | Enrich the generated BOM with MPNs/pricing |
| `digikey`/`lcsc`/`mouser`/`element14` | Sourcing for BOM parts |

## Workflow

1. **Gather the design**: component list (refs, values, libs, footprints,
   pins→nets), placements, board size, and any custom symbols/footprints.
   Reuse `references/verified-pinouts.md` for ATtiny85 / IRLZ44N / nRF24L01+
   pin names so custom symbols are correct the first time.
2. **Write the spec** as `design.json` (schema below). Model it on
   `skills/kicad-generate/examples/bed_pump_receiver/design.json`.
3. **Generate**:
   ```
   python3 skills/kicad-generate/scripts/kicad_author.py design.json --out <project-dir>
   ```
   Emits `.kicad_pro`, `.kicad_sch`, `.kicad_pcb`, `.kicad_sym`,
   a `<project>.pretty/` directory, `sym-lib-table`, `fp-lib-table`.
4. **Validate** (no GUI needed):
   - Balance-check every file (string-aware paren count = 0).
   - `kicad` skill: `python3 skills/kicad/scripts/analyze_schematic.py <f>.kicad_sch --output sch.json` and `python3 skills/kicad/scripts/analyze_pcb.py <f>.kicad_pcb --full --output pcb.json`.
   - Fix any format findings. Courtyard-overlap findings are **layout** issues —
     resolve in the GUI.
5. **Hand to the GUI**: open the project, run **ERC**, run **DRC** and tidy the
   auto-layout, press **B** to fill the GND zones.

## The one rule that matters most

Symbols are a **top-level list**: `.kicad_sym` =
`(kicad_symbol_lib … (symbol …) …)`. Custom **footprints** are a
**`.pretty` directory** — one bare `(footprint …)` block per `<name>.kicad_mod`,
**no wrapper element** (a wrapped `.kicad_pcb-lib` file fails to load:
*"Footprint library not found"*). Full field-by-field
structures are in `references/file-format-reference.md` — read it before
hand-writing any of these files.

## Spec schema (`design.json`)

```jsonc
{
  "project": "bed_pump_receiver",
  "title": "…", "date": "YYYY-MM-DD", "rev": "1.0",
  "board": { "w": 85, "h": 55, "thickness": 1.6 },
  "kicad_syms_dir": "/Applications/KiCad/…/symbols",   // system symbol libs
  "kicad_fps_dir":  "/Applications/KiCad/…/footprints", // system footprint libs
  "gnd_zone": true,

  "custom_symbols": [
    { "name": "ATtiny85", "ref": "U", "value": "ATtiny85",
      "footprint": "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm",
      "bodybox": [-10.16, 10.16, 10.16, -10.16],
      "pins": [
        { "type": "input", "num": 1, "name": "PB5/RST", "at": [15.24, -5.08, 180] }
        // type: input|output|passive|power_in|power_out|bidirectional|no_connect
        // at: [x, y, angle] pin tip; optional "length" (default 5.08)
      ] }
  ],

  "custom_footprints": [
    { "name": "nRF24L01P_Socket_8x1.27", "attr": "default",
      "pads": [ { "num": 1, "x": 0, "y": 0, "sx": 2, "sy": 2, "drill": 1 } ] }
  ],

  "pin_tip": { "Device:R": { "1": [0, 3.81], "2": [0, -3.81] }, /* lib_id -> pin -> [dx,dy] */ },
  "n_pins":  { "Device:R": 2, "bed_pump_receiver:ATtiny85": 8 /* lib_id -> count */ },
  "pad_off": { "Package_SO:SOIC-8…": { "1": [-2.475, -1.905] /* footprint -> pin -> [dx,dy] */ }, },

  "comps": [
    { "ref": "U3", "value": "ATtiny85",
      "lib_id": "bed_pump_receiver:ATtiny85",
      "footprint": "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm",
      "x": 100, "y": 100,          // schematic position
      "pbx": 18, "pby": 32, "prot": 0,  // PCB position (optional; defaults to x,y,0)
      "pins": { "1": "RST", "2": "CSN_MCU", "4": "GND", "8": null }  // pin -> net (null = NC)
    }
  ]
}
```

Notes:
- `lib_id` is `Library:Symbol`. `Library` = a system lib (`Device`, `Diode`,
  `Transistor_FET`, `Regulator_Linear`, …) or the project (`<project>`) for
  custom symbols. `footprint` is `Library:Footprint` from the system
  `footprints/` tree or the project library.
- Nets are derived from the `pins`→net mapping; no separate net list needed.
- `pin_tip`/`pad_off` are the mm offsets from the symbol/footprint origin to
  each pin/pad. `n_pins` is how many pins to declare per lib_id.

## Validation without a GUI

**Always validate after generating.** `validate_kicad.py` is the hard gate — it
catches the whole class of "opens in KiCad as question marks / won't load"
bugs that a bare balance check misses:

```bash
# Full validation (deterministic invariants + real KiCad ERC/parse if
# kicad-cli is installed). Exit 0 = good, 1 = defect.
python3 skills/kicad-generate/scripts/validate_kicad.py <project-dir-or-file>

# CI / no-KiCad environments: deterministic checks only (still catches the
# question-marks bug class). --strict makes any defect a hard failure.
python3 skills/kicad-generate/scripts/validate_kicad.py <project-dir> --no-cli --strict
```

What it checks, in layers:
- **Layer 1 (always, stdlib-only):** balanced parens, correct root node
  (`kicad_sch`/`kicad_pcb`/`kicad_symbol_lib`/`footprint`), every placed
  `lib_id` has a matching `lib_symbols` cache entry (the question-marks bug),
  no duplicate reference designators.
- **Layer 2 (when `kicad-cli` present):** real KiCad `sch erc --format json`
  (fails on unresolved-symbol `[???` markers), `pcb drc`, and `sym upgrade`/
  `fp upgrade` as parse-validators for generated symbol/footprint libraries.

`kicad_author.py` runs this automatically after every generation (add
`--no-validate` to skip, `--strict` to fail the build on any defect).

Manual fallback (only if you can't run the script above):

```
# balance (string-aware)
python3 -c "import sys; t=open(sys.argv[1]).read(); d=0;ist=False;esc=False
for c in t:
    if esc: esc=False; continue
    if ist: ist= c!='\"'
    elif c=='\"': ist=True
    elif c=='(': d+=1
    elif c==')': d-=1
print(d)" <file>

# analyzers
python3 skills/kicad/scripts/analyze_schematic.py <f>.kicad_sch
python3 skills/kicad/scripts/analyze_pcb.py <f>.kicad_pcb
```

## Limitations

- Placement is **orthogonal bus routing** — a coarse starting point, not a
  production layout. Expect courtyard overlaps and trace crossings; run DRC in
  the GUI and tidy.
- Custom symbol/footprint graphics are minimal (a bounding box + pins). Add
  silk/3D by editing in the GUI.
- `kicad-cli` in this environment cannot fill zones or reliably load large
  hand-built files — do zone fill (press **B**) and final DRC/ERC in the GUI.
