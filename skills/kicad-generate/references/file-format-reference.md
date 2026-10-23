# KiCad 10 File-Format Reference (authoring)

Exact structures needed to **write** valid KiCad project files programmatically.
Distilled from generating files that load in the KiCad 10 GUI and pass the
`kicad` skill analyzers. Every file is an S-expression. All coordinates in mm.

The single most important rule that trips up generated files:

> **Library files must be top-level lists.** A `.kicad_sym` is
> `(kicad_symbol_lib … (symbol …) …)` and a `.kicad_pcb-lib` is
> `(footprint_lib … (footprint …) …)`. A bare concatenation of `(symbol …)`
> / `(footprint …)` blocks is **not** a library — KiCad reports the library as
> *"not found"* (footprints) or a parse error (symbols).

---

## Symbol library (`.kicad_sym`)

```
(kicad_symbol_lib (version 20231120) (generator kicad_symbol_editor)

  (symbol "NAME"
    (pin_numbers)
    (pin_names)
    (exclude_from_sim no)
    (in_bom yes)
    (on_board yes)
    (in_pos_files yes)
    (duplicate_pin_numbers_are_jumpers no)
    (property "Reference" "U"
      (at 0 -12.7 0) (show_name no) (do_not_autoplace no)
      (effects (font (size 1.27 1.27))))
    (property "Value" "NAME"
      (at 0 12.7 0) (show_name no) (do_not_autoplace no)
      (effects (font (size 1.27 1.27))))
    (property "Footprint" "Lib:Footprint"
      (at 0 0 90) (show_name no) (do_not_autoplace no) (hide yes)
      (effects (font (size 1.27 1.27))))
    (symbol "NAME_0_1"            ; <- unit 0_1 = GRAPHIC body (no pins)
      (rectangle (start -10.16 10.16) (end 10.16 -10.16)
        (stroke (width 0.254) (type default)) (fill (type background))))
    (symbol "NAME_1_1"            ; <- unit 1_1 = PINS (one unit holds all pins)
      (pin input line
        (at 15.24 -5.08 180) (length 5.08)
        (name "PB5/RST" (effects (font (size 1.27 1.27))))
        (number "1" (effects (font (size 1.27 1.27)))))
      ...more pins in the SAME _1_1 unit...
    )
    (embedded_fonts no)
  )
)
```

Gotchas (each of these breaks loading if wrong):
- Pins live in the **`_1_1` unit**, graphics in the **`_0_1` unit**. They are
  swapped in many hand-written attempts.
- Each pin must be `(pin <type> line (at …) (length …) …)` — the `line`
  pin-shape keyword is required. Types: `input`, `output`, `passive`,
  `power_in`, `power_out`, `bidirectional`, `no_connect`.
- All pins of a symbol share **one** `_1_1` unit; do not open a unit per pin.
- Pin `at` = pin-tip location relative to the symbol origin; `length` = how
  far the pin extends toward the tip (default 5.08 for ICs).

---

## Footprint library (`.pretty` directory)

Custom footprints go in **`<project>.pretty/`**, one standalone `<name>.kicad_mod`
per footprint. A `.kicad_mod` is a **single bare `(footprint …)` block — no
wrapper element**. `fp-lib-table` points at the directory:
`(lib (name "…")(type "KiCad")(uri "${KIPRJMOD}/<proj>.pretty")(options ""))`.
Do **not** use a `<proj>.kicad_pcb-lib` wrapper file — KiCad 10 fails to load it
("Footprint library … not found") even when balanced. The `.kicad_mod` body:

```
(footprint "NAME" (version 20240101) (generator "kicad-footprint-generator")
    (layer "F.Cu")
    (attr through_hole)
    (descr "…")
    (tags "…")
    (fp_text reference "REF**" (at 0 0 0) (layer "F.SilkS")
      (effects (font (size 1 1))) (tstamp "<uuid>"))
    (fp_line (start -4 -4) (end 8.5 -4) (layer "F.CrtYd") (width 0.05))
    (pad "1" thru_hole circle (at 0 0) (size 1.6 1.6) (drill 0.8)
      (layers "*.Cu" "*.Mask"))
    (pad "2" thru_hole circle (at 5.08 0) (size 1.6 1.6) (drill 0.8)
      (layers "*.Cu" "*.Mask"))
  )
```

Gotchas:
- Pads are `(pad "N" <thru_hole|smd> <circle|roundrect|rect> (at …) (size …)
  (drill …) (layers …))`. The pad-type keyword is `thru_hole` or `smd` —
  **not** `connect`.
- Through-hole pads use `(layers "*.Cu" "*.Mask")` (no paste layer).
- The `(attr …)` token must be a valid value: `through_hole`, `smd`, `board_only`,
  `connect`, `exclude_from_bom`, `exclude_from_pos_files`, or
  `allow_solder_mask_bridges`. Through-hole parts use `attr through_hole`
  (**not** `default` — that fails to parse).
- `REF**` in the reference text is the placeholder KiCad auto-replaces; when
  placing a footprint into a `.kicad_pcb`, replace `REF**` with the actual ref
  in both the `property "Reference"` and the `fp_text reference`.

---

## Schematic (`.kicad_sch`)

```
(kicad_sch
  (version 20250114)
  (generator "eeschema")
  (generator_version "9.0")
  (uuid "<sheet-uuid>")
  (paper "A3")
  (title_block (title "…") (date "YYYY-MM-DD") (rev "1.0"))
  (lib_symbols
    (symbol "Lib:Name" …full embedded symbol block… )
    ...one per used symbol...
  )
  (junction (at x y) (diameter 0) (uuid "<uuid>"))
  (no_connect (at x y) (uuid "<uuid>"))
  (wire (pts (xy x1 y1) (xy x2 y2))
    (stroke (width 0) (type default)) (uuid "<uuid>"))
  (label "NET" (at x y 0) (effects (font (size 1.27 1.27))) (uuid "<uuid>"))
  (symbol
    (lib_id "Lib:Name")
    (at x y 0) (unit 1)
    (exclude_from_sim no) (in_bom yes) (on_board yes) (dnp no)
    (fields_autoplaced yes) (uuid "<uuid>")
    (property "Reference" "U1" (at …) (effects (font (size 1.27 1.27))))
    (property "Value" "…"  (at …) (effects (font (size 1.27 1.27))))
    (property "Footprint" "Lib:FP" (at …) (effects (font (size 1.27 1.27))) (hide yes))
    (pin "1" (uuid "<uuid>"))
    (pin "2" (uuid "<uuid>"))
    (instances (project "" (path "/<sheet-uuid>") (reference "U1") (unit 1)))
  )
  (sheet_instances (path "/" (page "1")))
  (embedded_fonts no)
)
```

Gotchas (verified against KiCad 10.0.7):
- Every symbol instance needs a **per-pin** `(pin "N" (uuid …))` for each pin
  and an `(instances (project "" (path "/<sheet-uuid>") (reference …) (unit 1)))`.
- **Labels have NO `(global yes)` field** — the KiCad 10 parser rejects it at
  that position. A plain `(label "NET" …)` is global by default (it still
  names the net across the whole sheet). Do not add `(global yes)`.
- **A label must sit exactly on a pin tip (or on a wire touching a pin).**
  A label placed at an offset from the pin dangles → ERC reports
  `label_dangling` / `pin_not_connected`. Place it at an actual pin-tip
  coordinate.
- Wires need `(stroke (width 0) (type default))`.
- `lib_symbols` embeds a full copy of each used symbol for rendering. It is
  not strictly required to *load* (symbols also resolve from the installed
  libraries), but keep it so the sheet is self-contained.
- Net names come from `(label …)` elements; the net graph is built from wire
  endpoints + label positions + pin tips.

---

## PCB layout (`.kicad_pcb`) — verified against KiCad 10.0.7

All of the following was confirmed by iterating with `kicad-cli pcb export
gerbers` (the load oracle). Deviating from any line makes the board fail to
load.

```
(kicad_pcb (version 20241229) (generator "pcbnew") (generator_version "9.0")
  (general (thickness 1.6))
  (paper "A3")
  (layers
    (0 "F.Cu" signal) (31 "B.Cu" signal)
  )
  (gr_poly (pts (xy 0 0) (xy W 0) (xy W H) (xy 0 H))
    (layer "Edge.Cuts") (width 0.1) (tstamp "<uuid>"))
  (net 1 "NETNAME")
  (segment (start X1 Y1) (end X2 Y2) (width 0.25) (layer "F.Cu")
    (net 1) (uuid "<uuid>"))
  (via (at X Y) (size 0.6) (drill 0.3) (layers "F.Cu" "B.Cu") (net 1) (uuid "<uuid>"))
  (footprint "FP_NAME" (version 20241229) (layer "F.Cu") (at X Y ROT)
    …full footprint…
    (pad "1" … (net "NETNAME"))
  )
  (zone (net 1) (net_name "GND") (layer "F.Cu")
    (connect_pads yes) (fill yes)
    (polygon (pts (xy 0 0) (xy W 0) (xy W H) (xy 0 H)))
    (uuid "<uuid>"))
)
```

Gotchas (each one caused a hard load failure):
- **Layers: list ONLY copper** — `(0 "F.Cu" signal) (31 "B.Cu" signal)`.
  Do **not** hand-list the user layers (`User.Drawings`, `Edge.Cuts`, masks,
  silks, paste). KiCad fills them automatically; hand-listing them puts names
  "not in fixed layer hash" and the parse aborts.
- **No `(setup …)` block.** Omit it entirely. A hand-written stackup
  (`(dielectric (name …))`, `soldermask`, …) is malformed and desyncs the
  parser.
- **Net declarations have NO tstamp**: `(net 1 "NETNAME")`. A trailing
  `(tstamp …)` gives "Expecting ')'".
- **Copper wires are `segment`, not `track`**, with bare coordinates:
  `(segment (start X1 Y1) (end X2 Y2) (width W) (layer "F.Cu") (net N)
  (uuid …))`. There is no `(track …)`, no `(pts (xy …))`, no `(at (xy …))`,
  and tracks use `uuid` (not `tstamp`).
- **Pads reference the net by NAME, not number**: `(net "NETNAME")`.
  `(net 1)` in a pad gives "Expecting net name". (Contrast: `segment`/`via`
  and the top-level `(net …)` use the number.)
- **Zone fields** that parse: `net`, `net_name`, `layer`, `connect_pads yes`
  (not `(default yes)`), `fill yes`, `polygon (pts …)` (not `path`), `uuid`.
  Do **not** include `thickness`, `min_width`, or `hatch` — they are rejected.
- Placed footprints carry `(at X Y ROT)` right after `(layer "F.Cu")`.
- The board outline is a `gr_poly` on `Edge.Cuts` (correct layer name).
- **Zones need a real `(polygon …)`** here; `fill yes` alone is not enough.
  Re-run the fill in the GUI (**B**) after editing — `kicad-cli` won't
  repour a zone for you.

**Routing (avoiding shorts):** the authoring tool routes copper with a
collision-aware Manhattan router, not naive straight lines:
- Pads are dilated on a ~0.1 mm grid (a keepout ring); every routed trace is
  then marked occupied, so a later net can never share a coordinate with an
  earlier one → **no same-layer shorts** between different nets.
- Each net connects its pads to a hub via A\*; the start/goal pad rings are
  temporarily freed so the search can step out of a pad (a pad's own ring
  otherwise blocks all four neighbours of its centre).
- Verify with a same-layer/overlapping-segment scan (the DRC oracle) — the
  board must show **0 overlapping different-net segment pairs** before it is
  electrically sound. (`kicad-cli pcb drc` is available but its report
  formats are fiddly; a coordinate-overlap scan is the reliable check.)

---

## Project file (`.kicad_pro`) and library tables

- `.kicad_pro` is **JSON**. Must include `board.board_constraints.width/height`
  and a `sheets` list with the schematic `path`.
- `sym-lib-table` and `fp-lib-table` each register the project library:
  ```
  (sym_lib_table (version 7)
    (lib (name "PROJ")(type "KiCad")(uri "${KIPRJMOD}/PROJ.kicad_sym")(options "")))
  ```
  `${KIPRJMOD}` resolves to the directory containing the project file.

---

## Validation (no GUI required)

1. **Balance check** — every file must paren-balance to 0 (string-aware).
2. **Skill analyzers** —
   - `analyze_schematic.py <f>.kicad_sch` → components, nets, wires.
   - `analyze_pcb.py <f>.kicad_pcb` → footprints, nets, courtyard overlaps.
3. Courtyard overlaps reported by `analyze_pcb.py` are **layout quality** issues,
   not format errors — auto-placement is a coarse starting point. Run **DRC**
   in the KiCad GUI to resolve them, then press **B** to fill zones.
