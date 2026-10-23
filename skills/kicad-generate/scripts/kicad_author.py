#!/usr/bin/env python3
"""
kicad_author.py — generate a complete, valid KiCad project from a JSON design spec.

Emits, into an output directory:
  <project>.kicad_pro
  <project>.kicad_sch        (schematic, embedded lib_symbols)
  <project>.kicad_pcb        (PCB: footprints, nets, traces, optional GND zones)
  <project>.kicad_sym        (project-local custom symbols)
  <project>.pretty/          (project-local custom footprints, one .kicad_mod each)
  sym-lib-table
  fp-lib-table

Standard-library only (Python 3.9+). No third-party deps.

Spec (JSON) — see references/file-format-reference.md and examples/bed_pump_receiver/design.json.

Usage:
  python3 kicad_author.py design.json --out DIR
"""
from __future__ import annotations
import argparse, json, os, re, sys, uuid as _uuid

def uid():
    return str(_uuid.uuid4())

def fmt(v):
    s = f"{v:.4f}".rstrip("0").rstrip(".")
    return s if s else "0"

# ---------------------------------------------------------------------------
# S-expression block extractor: given text and a token like (symbol "NAME" or
# (footprint "NAME", return the balanced top-level block.
# ---------------------------------------------------------------------------
def extract_block(t, head):
    m = re.search(head, t)
    if not m:
        return None
    start = m.start(); depth = 0; in_str = False; esc = False
    for j in range(start, len(t)):
        c = t[j]
        if esc:
            esc = False; continue
        if in_str:
            if c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return t[start:j + 1]
    return None

def balanced(t):
    d = 0; in_str = False; esc = False
    for c in t:
        if esc:
            esc = False; continue
        if in_str:
            if c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == "(":
            d += 1
        elif c == ")":
            d -= 1
    return d

# ---------------------------------------------------------------------------
# Custom symbols  (.kicad_sym)
# ---------------------------------------------------------------------------
def emit_pin(ptype, num, pname, at, length=5.08):
    ax, ay, ang = at
    return "\n".join([
        "\t\t\t(pin %s line" % ptype,
        "\t\t\t\t(at %s %s %s)" % (ax, ay, ang),
        "\t\t\t\t(length %s)" % length,
        '\t\t\t\t(name "%s"' % pname,
        "\t\t\t\t\t(effects",
        "\t\t\t\t\t\t(font",
        "\t\t\t\t\t\t\t(size 1.27 1.27)",
        "\t\t\t\t\t\t)",
        "\t\t\t\t\t)",
        "\t\t\t\t)",
        '\t\t\t\t(number "%s"' % num,
        "\t\t\t\t\t(effects",
        "\t\t\t\t\t\t(font",
        "\t\t\t\t\t\t\t(size 1.27 1.27)",
        "\t\t\t\t\t\t)",
        "\t\t\t\t\t)",
        "\t\t\t\t)",
        "\t\t\t)",
    ])

def emit_body(sym, x0, y0, x1, y1):
    return "\n".join([
        "\t\t(symbol \"%s_0_1\"" % sym,
        "\t\t\t(rectangle",
        "\t\t\t\t(start %s %s)" % (x0, y0),
        "\t\t\t\t(end %s %s)" % (x1, y1),
        "\t\t\t\t(stroke",
        "\t\t\t\t\t(width 0.254)",
        "\t\t\t\t\t(type default)",
        "\t\t\t\t)",
        "\t\t\t\t(fill",
        "\t\t\t\t\t(type background)",
        "\t\t\t\t)",
        "\t\t\t)",
        "\t\t)",
    ])

def emit_pins_unit(sym, pins):
    out = ["\t\t(symbol \"%s_1_1\"" % sym]
    for p in pins:
        out.append(emit_pin(p["type"], str(p["num"]), p.get("name", ""),
                            tuple(p["at"]), p.get("length", 5.08)))
    out.append("\t\t)")
    return "\n".join(out)

def emit_prop(name, val, at, hide=False):
    ax, ay, ang = at
    h = " (hide yes)" if hide else ""
    return "\n".join([
        '\t\t(property "%s" "%s"' % (name, val),
        "\t\t\t(at %s %s %s)" % (ax, ay, ang),
        "\t\t\t(show_name no)",
        "\t\t\t(do_not_autoplace no)",
        "\t\t\t(effects",
        "\t\t\t\t(font",
        "\t\t\t\t\t(size 1.27 1.27)",
        "\t\t\t\t)",
        "\t\t\t)%s" % h,
        "\t\t)",
    ])

def emit_symbol(sym):
    out = [
        "\t(symbol \"%s\"" % sym["name"],
        "\t\t(pin_numbers)",
        "\t\t(pin_names)",
        "\t\t(exclude_from_sim no)",
        "\t\t(in_bom yes)",
        "\t\t(on_board yes)",
        "\t\t(in_pos_files yes)",
        "\t\t(duplicate_pin_numbers_are_jumpers no)",
        emit_prop("Reference", sym.get("ref", "U"), (0, -12.7, 0)),
        emit_prop("Value", sym.get("value", sym["name"]), (0, 12.7, 0)),
        emit_prop("Footprint", sym.get("footprint", ""), (0, 0, 90), hide=True),
        emit_body(sym["name"], *sym["bodybox"]),
        emit_pins_unit(sym["name"], sym["pins"]),
        "\t\t(embedded_fonts no)",
        "\t)",
    ]
    return "\n".join(out)

def synthesize_placeholder(lib_id, comp, pin_tip):
    """Build a minimal symbol block for a lib_id with no resolvable library.

    Guarantees the placed symbol resolves in the schematic editor (a clean
    labeled box with pins) instead of the default question-mark placeholder.
    Used when a spec references a lib_id absent from both custom_symbols and
    the standard libraries (e.g. MountingHole — a footprint-only part).
    """
    name = lib_id.split(":", 1)[1] if ":" in lib_id else lib_id
    tip = pin_tip.get(lib_id, {})
    pin_nums = sorted(comp.get("pins", {}).keys(), key=lambda x: int(x) if str(x).isdigit() else str(x))
    # Place pins on the box edges from their schematic tip offsets so wire
    # endpoints still land on the symbol. Fall back to a left/right split.
    pins = []
    for i, num in enumerate(pin_nums):
        dx, dy = tip.get(num, ([-6.35, 3.81 - i * 2.54] if i % 2 == 0 else [6.35, 3.81 - i * 2.54]))
        ang = 0 if dx < 0 else 180
        pins.append({"type": "passive", "num": num, "name": "",
                     "at": [dx, dy, ang], "length": 2.54})
    # Size the body to enclose the pins (min box, symmetric).
    if pins:
        xs = [abs(p["at"][0]) for p in pins]; ys = [abs(p["at"][1]) for p in pins]
        bx = max(xs) + 1.27; by = max(ys) + 1.27
    else:
        bx, by = 3.81, 2.54
    bodybox = (-bx, -by, bx, by)
    # emit_body/emit_pins_unit name sub-symbols by bare name; re-key header after.
    sym = {"name": name, "ref": comp.get("ref", "U")[:1] or "U",
           "value": comp.get("value", name), "footprint": comp.get("footprint", ""),
           "bodybox": bodybox, "pins": pins}
    block = emit_symbol(sym)
    return rekey_symbol_block(block, lib_id)


def build_kicad_sym(custom_symbols, outdir, proj):
    if not custom_symbols:
        return None
    L = ["(kicad_symbol_lib (version 20231120) (generator kicad_symbol_editor)", ""]
    for s in custom_symbols:
        L.append(emit_symbol(s)); L.append("")
    L.append(")")
    path = os.path.join(outdir, proj + ".kicad_sym")
    open(path, "w").write("\n".join(L) + "\n")
    if balanced("\n".join(L)) != 0:
        sys.exit("ERROR: kicad_sym not balanced")
    return path

# ---------------------------------------------------------------------------
# Custom footprints  (one .kicad_mod each in a .pretty dir)
# ---------------------------------------------------------------------------
def emit_footprint(fp):
    out = ['(footprint "%s" (version 20240101) (generator "kicad-footprint-generator") (layer "F.Cu")' % fp["name"],
           "  (attr %s)" % fp.get("attr", "through_hole"),  # smd/through_hole/board_only/
            # connect/exclude_from_bom/exclude_from_pos_files/allow_solder_mask_bridges (NOT "default")
           '  (descr "%s")' % fp.get("descr", fp["name"]),
           '  (tags "%s")' % fp.get("tags", ""),
           '  (fp_text reference "REF**" (at 0 0 0) (layer "F.SilkS") (effects (font (size 1 1))) (tstamp "%s"))' % uid(),
           '  (fp_text value "%s" (at 0 0 0) (layer "F.Fab") (effects (font (size 1 1))) (tstamp "%s"))' % (fp.get("value", fp["name"]), uid()),
          ]
    for line in fp.get("fp_lines", []):
        s, e, layer, w = line
        out.append('  (fp_line (start %s %s) (end %s %s) (layer "%s") (width %s))' % (s[0], s[1], e[0], e[1], layer, w))
    for p in fp["pads"]:
        out.append('  (pad "%s" %s %s (at %s %s) (size %s %s) (drill %s) (layers "*.Cu" "*.Mask"))'
                   % (p["num"], p.get("kind", "thru_hole"), p.get("shape", "circle"),
                      p["x"], p["y"], p.get("sx", 1.6), p.get("sy", 1.6), p.get("drill", 0.8)))
    out.append(")")
    return "\n".join(out)

def build_pretty_dir(custom_footprints, outdir, proj):
    """Write custom footprints to <outdir>/<proj>.pretty/<name>.kicad_mod.

    A .pretty directory is the standard project footprint library — one
    standalone .kicad_mod per footprint (no wrapper element), which KiCad
    loads reliably. Returns the .pretty directory path (or None)."""
    if not custom_footprints:
        return None
    pretty = os.path.join(outdir, proj + ".pretty")
    os.makedirs(pretty, exist_ok=True)
    for fp in custom_footprints:
        blk = emit_footprint(fp)
        path = os.path.join(pretty, fp["name"] + ".kicad_mod")
        open(path, "w").write(blk + "\n")
        if balanced(blk) != 0:
            sys.exit("ERROR: footprint %s not balanced" % fp["name"])
    return pretty

# ---------------------------------------------------------------------------
# Schematic  (.kicad_sch)
# ---------------------------------------------------------------------------
def load_standard_symbol(lib_id, syms_dir):
    # "Device:R" -> <syms_dir>/Device.kicad_sym, symbol "R"
    lib, name = lib_id.split(":")
    p = os.path.join(syms_dir, lib + ".kicad_sym")
    if not os.path.isfile(p):
        return None
    t = open(p).read()
    b = extract_block(t, r'\(symbol "' + re.escape(name) + r'"')
    return b

def derive_symbol_pins(lib_id, syms_dir, _depth=0):
    """Read a symbol's real pin positions from its library, following (extends).

    Returns {pin_number: (x, y)} in symbol-local coordinates, or {} if the
    symbol/library can't be found. This is the authoritative source for where a
    pin's connection point is; the spec's hand-maintained pin_tip table drifts
    and silently breaks routing (wires land off-pin, board is electrically
    dead). Walks the (extends "BASE") chain so derived parts (1N5819, IRLZ44N,
    AMS1117-3.3) resolve their inherited pins.
    """
    if _depth > 4 or ":" not in lib_id:
        return {}
    lib, name = lib_id.split(":", 1)
    p = os.path.join(syms_dir, lib + ".kicad_sym")
    if not os.path.isfile(p):
        return {}
    try:
        t = open(p).read()
    except OSError:
        return {}
    b = extract_block(t, r'\(symbol "' + re.escape(name) + r'"')
    if not b:
        return {}
    m = re.search(r'\(extends "([^"]+)"', b)
    if m:
        return derive_symbol_pins(lib + ":" + m.group(1), syms_dir, _depth + 1)
    pins = {}
    for pm in re.finditer(
            r'\(pin \w+ \w+\s*\(at (-?[\d.]+) (-?[\d.]+) (-?[\d.]+)\)'
            r'.*?\(number "([^"]+)"', b, re.S):
        # KiCad symbol libraries are Y-up but schematics are Y-down: placing a
        # symbol flips Y. Negate the library Y so the schematic pin offset is
        # correct — without this every derived pin lands on the wrong side and
        # labels/wires attach to the wrong pin (silent net shorts).
        pins[pm.group(4)] = (float(pm.group(1)), -float(pm.group(2)))
    return pins

def derive_symbol_pin_types(lib_id, syms_dir, _depth=0):
    """Read a symbol's pin electrical types {pin_number: type} from its library.

    Types are what ERC uses to decide if a power net is driven. Follows the
    (extends) chain like derive_symbol_pins. Returns {} when unknown.
    """
    if _depth > 4 or ":" not in lib_id:
        return {}
    lib, name = lib_id.split(":", 1)
    p = os.path.join(syms_dir, lib + ".kicad_sym")
    if not os.path.isfile(p):
        return {}
    try:
        t = open(p).read()
    except OSError:
        return {}
    b = extract_block(t, r'\(symbol "' + re.escape(name) + r'"')
    if not b:
        return {}
    m = re.search(r'\(extends "([^"]+)"', b)
    if m:
        return derive_symbol_pin_types(lib + ":" + m.group(1), syms_dir, _depth + 1)
    types = {}
    for pm in re.finditer(
            r'\(pin (\w+) \w+\s*\(at (-?[\d.]+) (-?[\d.]+) (-?[\d.]+)\)'
            r'.*?\(number "([^"]+)"', b, re.S):
        types[pm.group(5)] = pm.group(1)
    return types


def authoritative_pin_tips(comps, syms_dir, spec_tips):
    """Merge real (library-derived) pin positions over the spec's pin_tip table.

    Library-derived positions win where available (they are ground truth); the
    spec table is kept only as a fallback for custom/placeholder symbols with no
    standard library. Result maps lib_id -> {pin: [dx, dy]} in the shape the
    router expects.

    Both paths yield SCHEMATIC-space offsets (Y already negated). The spec's
    pin_tip stores raw symbol-local Y, so the fallback negates it here to match
    KiCad's Y-flip-at-placement — otherwise custom-symbol labels land on the
    wrong side of the part and short nets.
    """
    tips = {}
    for lid, d in (spec_tips or {}).items():
        tips[lid] = {str(k): [float(v[0]), -float(v[1])] for k, v in d.items()}
    if not syms_dir:
        return tips
    for lid in sorted({c["lib_id"] for c in comps}):
        real = derive_symbol_pins(lid, syms_dir)
        if real:
            tips[lid] = {str(k): [v[0], v[1]] for k, v in real.items()}
    return tips

def rekey_symbol_block(block, lib_id):
    """Re-key an embedded symbol block's TOP-LEVEL name to the full lib_id.

    KiCad keys the schematic's embedded lib_symbols cache by the fully
    library-qualified lib_id (e.g. "Device:R"), and a placed symbol's
    (lib_id "Device:R") resolves against that key. Standard-library and custom
    blocks carry the bare name ("R"), so embedding them verbatim leaves every
    placed part unresolved — rendered in the GUI as a box with a question mark.

    Only the header (top-level) name is rewritten; inner unit sub-symbols
    ("R_0_1", "R_1_1") keep their bare prefix, matching KiCad's own files.
    """
    if not block:
        return block
    m = re.search(r'\(symbol "([^"]*)"', block)
    if not m:
        return block
    # Replace only the first (header) occurrence.
    return block[:m.start()] + '(symbol "%s"' % lib_id + block[m.end():]

def inline_extends(derived_block, base_block, lib_id):
    """Flatten an (extends "...") symbol by inlining the base's unit sub-symbols.

    A derived symbol carries no pins of its own — it inherits them from its base
    via (extends "BASE"). KiCad's resolution of that reference inside a
    schematic's embedded lib_symbols cache is unreliable (the base must be
    present under an exact key), and a symbol whose extends doesn't resolve has
    NO pins, so nothing connecting to it becomes an open. Inlining the base's
    unit sub-symbols and dropping (extends) makes the block self-contained and
    immune to that. Sub-symbol names are re-prefixed to the derived name so the
    units bind to the right parent.
    """
    if not derived_block or "(extends" not in derived_block:
        return derived_block, False
    if not base_block:
        return derived_block, False
    derived_name = lib_id.split(":", 1)[1]
    base_name_m = re.search(r'\(extends "([^"]+)"', derived_block)
    base_name = base_name_m.group(1)
    # Collect the base's unit sub-symbols: (symbol "BASE_0_1" ...) etc.
    unit_blocks = []
    for m in re.finditer(r'\n(\t+)\(symbol "' + re.escape(base_name) + r'_(\d+_\d+)"', base_block):
        start = m.start() + 1
        # walk to the matching close paren of this sub-symbol
        depth = 0; j = start; in_str = False; esc = False
        while j < len(base_block):
            c = base_block[j]
            if esc: esc = False
            elif in_str:
                if c == "\\": esc = True
                elif c == '"': in_str = False
            elif c == '"': in_str = True
            elif c == "(": depth += 1
            elif c == ")":
                depth -= 1
                if depth == 0: break
            j += 1
        unit = base_block[start:j + 1]
        # re-prefix the sub-symbol name to the derived symbol
        unit = unit.replace('"%s_%s"' % (base_name, m.group(2)),
                            '"%s_%s"' % (derived_name, m.group(2)), 1)
        unit_blocks.append(unit)
    if not unit_blocks:
        return derived_block, False
    # Remove the (extends "...") line from the derived block.
    out = re.sub(r'\n\t\t\(extends "[^"]+"\)', "", derived_block, count=1)
    # Insert the inlined units just before the final (embedded_fonts no) \t)
    marker = "\t\t(embedded_fonts no)"
    if marker in out:
        insert_at = out.rindex(marker)
        out = out[:insert_at] + "\n".join(unit_blocks) + "\n" + out[insert_at:]
    else:
        # fall back: insert before the last closing paren of the block
        close = out.rfind("\n\t)")
        out = out[:close] + "\n" + "\n".join(unit_blocks) + out[close:]
    return out, True


def build_kicad_sch(spec, outdir, proj):
    syms_dir = spec.get("kicad_syms_dir")
    # Real library pin positions override the spec's hand-maintained pin_tip
    # table (which drifts and silently breaks routing — wires land off-pin).
    pin_tip = authoritative_pin_tips(spec.get("comps", []), syms_dir,
                                     spec.get("pin_tip", {}))
    n_pins = spec.get("n_pins", {})
    comps = spec["comps"]
    custom_names = {s["name"] for s in spec.get("custom_symbols", [])}

    # collect lib_symbols to embed (system + custom)
    lib_blocks, seen = [], set()
    embedded_ids = set()
    placeholder_ids = set()
    for c in comps:
        lib_id = c["lib_id"]
        if ":" in lib_id and lib_id.split(":")[0] == proj:
            name = lib_id.split(":")[1]
            if name in custom_names:
                # custom symbol: emit the canonical block
                csym = next(s for s in spec["custom_symbols"] if s["name"] == name)
                b = emit_symbol(csym)
            else:
                b = None
        else:
            b = load_standard_symbol(lib_id, syms_dir)
        if b is None:
            # No resolvable definition (missing lib, or a footprint-only part
            # like MountingHole). Synthesize a placeholder so the placed symbol
            # still resolves in the editor instead of a question-mark box.
            b = synthesize_placeholder(lib_id, c, pin_tip)
            if b is not None:
                placeholder_ids.add(lib_id)
        # KiCad keys the embedded cache by the full lib_id; re-key bare-name
        # blocks so placed (lib_id "...") symbols resolve in the schematic GUI.
        # Derived (extends) symbols get their base's units inlined first so they
        # are self-contained — an unresolved extends leaves the symbol with no
        # pins and every connection to it an open.
        mb = re.search(r'\(extends "([^"]+)"', b)
        if mb and syms_dir:
            lib = lib_id.split(":", 1)[0]
            bp = os.path.join(syms_dir, lib + ".kicad_sym")
            base = None
            if os.path.isfile(bp):
                base = extract_block(open(bp).read(),
                                     r'\(symbol "' + re.escape(mb.group(1)) + r'"')
            b, _inlined = inline_extends(b, base, lib_id)
        b = rekey_symbol_block(b, lib_id)
        key = re.sub(r'\s+', '', b)
        if key not in seen:
            seen.add(key)
            lib_blocks.append(b)
        embedded_ids.add(lib_id)

    # net graph from pin tips
    nets = {}
    net_pin_dir = {}   # (netname, (px,py)) -> outward unit direction
    def net(n):
        return nets.setdefault(n, [])
    for c in comps:
        tip = pin_tip.get(c["lib_id"], {})
        for pin, nname in c["pins"].items():
            if nname is None:
                continue
            dx, dy = tip[pin]
            px, py = c["x"] + dx, c["y"] + dy
            net(nname).append((px, py))
            # Outward direction = continue away from the symbol body along the
            # pin's own offset axis. Used to draw a short visible stub.
            if abs(dx) >= abs(dy):
                net_pin_dir[(nname, (px, py))] = (1.0 if dx >= 0 else -1.0, 0.0)
            else:
                net_pin_dir[(nname, (px, py))] = (0.0, 1.0 if dy >= 0 else -1.0)

    # Decorative wiring: a short stub on every named pin, with the net label at
    # the stub's far end. Connectivity is carried by the LABELS (guaranteed
    # correct); the stubs make the schematic visually readable — every pin shows
    # a wire and its net name, the standard net-label schematic style. Stubs are
    # short (2.54mm) and point outward from each pin, so they don't collide the
    # way the old bus router did (which shorted nets sharing a median-y row).
    STUB = 2.54
    all_segs, all_junc = [], []
    stub_endpoints = {}   # netname -> list of (x,y) where the label goes
    for nname, pts in nets.items():
        ends = []
        for (px, py) in pts:
            ux, uy = net_pin_dir.get((nname, (px, py)), (0.0, 0.0))
            ex, ey = px + ux * STUB, py + uy * STUB
            all_segs.append(((px, py), (ex, ey)))
            ends.append((ex, ey))
        stub_endpoints[nname] = ends

    # Detect undriven power nets so we can attach a PWR_FLAG to each. KiCad ERC
    # reports "Input Power pin not driven by any Output Power pins" for any net
    # that has power_in pins but no power_out pin driving it. A PWR_FLAG is a
    # 1-pin power_out symbol; placing one on the net satisfies ERC. This is the
    # standard KiCad idiom for externally-powered nets (a connector/regulator
    # input feeds them, not an on-sheet power output).
    undriven_power_nets = []
    if syms_dir:
        pin_types_cache = {}
        for c in comps:
            lid = c["lib_id"]
            if lid not in pin_types_cache:
                pin_types_cache[lid] = derive_symbol_pin_types(lid, syms_dir)
        net_has_power_in = set()
        net_has_power_out = set()
        for c in comps:
            ptypes = pin_types_cache.get(c["lib_id"], {})
            for pin, nname in c["pins"].items():
                if nname is None:
                    continue
                ptype = ptypes.get(str(pin))
                if ptype == "power_in":
                    net_has_power_in.add(nname)
                elif ptype == "power_out":
                    net_has_power_out.add(nname)
        undriven_power_nets = sorted(net_has_power_in - net_has_power_out)

    # Embed the PWR_FLAG definition (and its power:GND-style base) when we will
    # place at least one. PWR_FLAG is a single power_out pin; it satisfies ERC's
    # "power pin not driven" for externally-powered nets.
    if undriven_power_nets:
        pwr = load_standard_symbol("power:PWR_FLAG", syms_dir)
        if pwr:
            pwr = rekey_symbol_block(pwr, "power:PWR_FLAG")
            keyp = re.sub(r'\s+', '', pwr)
            if keyp not in seen:
                seen.add(keyp)
                lib_blocks.append(pwr)

    sheet_uuid = uid()
    title = spec.get("title", proj)
    L = []
    L.append("(kicad_sch")
    L.append("\t(version 20250114)")
    L.append('\t(generator "eeschema")')
    L.append('\t(generator_version "9.0")')
    L.append('\t(uuid "%s")' % sheet_uuid)
    L.append('\t(paper "A3")')
    L.append("\t(title_block")
    L.append('\t\t(title "%s")' % title)
    L.append('\t\t(date "%s")' % spec.get("date", "1970-01-01"))
    L.append('\t\t(rev "%s")' % spec.get("rev", "1.0"))
    L.append("\t)")
    L.append("\t(lib_symbols")
    for b in lib_blocks:
        L.append("\t" + b)
    L.append("\t)")

    # Every placed symbol now resolves (real or synthesized). Report any that
    # fell back to a placeholder so the user knows those parts have no real
    # library symbol behind them (they render as a labeled box, not a
    # question mark, but pin geometry is approximate).
    if placeholder_ids:
        print("NOTE: %d symbol(s) had no library definition and use a "
              "synthesized placeholder (renders as a labeled box; pin geometry "
              "is approximate — add a real symbol for correctness):"
              % len(placeholder_ids), file=sys.stderr)
        for u in sorted(placeholder_ids):
            lib = u.split(":")[0] if ":" in u else u
            sp = os.path.join(syms_dir, lib + ".kicad_sym") if syms_dir else None
            why = ("library file not found: %s" % sp) if sp and not os.path.isfile(sp) \
                  else "symbol not in library (add it to custom_symbols, or fix the lib_id)"
            print("  - %s  (%s)" % (u, why), file=sys.stderr)
    for c in comps:
        tip = pin_tip.get(c["lib_id"], {})
        for pin, nname in c["pins"].items():
            if nname is not None:
                continue
            dx, dy = tip[pin]
            L.append("\t(no_connect")
            L.append("\t\t(at %s %s)" % (fmt(c["x"] + dx), fmt(c["y"] + dy)))
            L.append('\t\t(uuid "%s")' % uid())
            L.append("\t)")
    # Connectivity is by LABEL, not by wire. Placing a label at every named pin
    # makes KiCad join same-named pins into the intended net with zero risk of
    # the collinear-overlap shorts that wire routing produced (nets sharing a
    # bus row / vertical stub column shorted together). This yields a netlist
    # that matches spec intent by construction. The user routes in the GUI.
    # Compute PWR_FLAG positions up front. KiCad's parser is order-sensitive:
    # every (label ...) must precede every placed (symbol ...). So we emit the
    # flag labels in the label block below and the flag symbols in the symbol
    # block, keeping labels-before-symbols ordering intact.
    pwr_flag_spots = [(10.16 + i * 10.16, 10.16, nn)
                      for i, nn in enumerate(undriven_power_nets)]

    for nname, npts in sorted(nets.items()):
        for (lx, ly) in stub_endpoints.get(nname, npts):
            L.append('\t(label "%s"' % nname)
            L.append("\t\t(at %s %s 0)" % (fmt(lx), fmt(ly)))
            L.append("\t\t(effects")
            L.append("\t\t\t(font")
            L.append("\t\t\t\t(size 1.27 1.27)")
            L.append("\t\t\t)")
            L.append("\t\t)")
            # NOTE: no (global yes) — KiCad 10 plain labels are global; the field
            # is rejected by the parser at this position.
            L.append('\t\t(uuid "%s")' % uid())
            L.append("\t)")
    # Emit the decorative wire stubs (before symbols; after labels per KiCad's
    # order-sensitive parser). Each stub runs from a pin outward to its label.
    for (a, b) in all_segs:
        L.append("\t(wire")
        L.append("\t\t(pts")
        L.append("\t\t\t(xy %s %s) (xy %s %s)" % (fmt(a[0]), fmt(a[1]), fmt(b[0]), fmt(b[1])))
        L.append("\t\t)")
        L.append("\t\t(stroke")
        L.append("\t\t\t(width 0)")
        L.append("\t\t\t(type default)")
        L.append("\t\t)")
        L.append('\t\t(uuid "%s")' % uid())
        L.append("\t)")
    # A label on each PWR_FLAG's pin (the flag symbol itself is emitted with the
    # other placed symbols below — labels must all come first).
    for (fx, fy, nname) in pwr_flag_spots:
        L.append('\t(label "%s"' % nname)
        L.append("\t\t(at %s %s 0)" % (fmt(fx), fmt(fy)))
        L.append("\t\t(effects")
        L.append("\t\t\t(font")
        L.append("\t\t\t\t(size 1.27 1.27)")
        L.append("\t\t\t)")
        L.append("\t\t)")
        L.append('\t\t(uuid "%s")' % uid())
        L.append("\t)")
    for c in comps:
        n = n_pins.get(c["lib_id"], len(c["pins"]))
        L.append("\t(symbol")
        L.append('\t\t(lib_id "%s")' % c["lib_id"])
        L.append("\t\t(at %s %s 0)" % (fmt(c["x"]), fmt(c["y"])))
        L.append("\t\t(unit 1)")
        L.append("\t\t(exclude_from_sim no)")
        L.append("\t\t(in_bom yes)")
        L.append("\t\t(on_board yes)")
        L.append("\t\t(dnp no)")
        L.append("\t\t(fields_autoplaced yes)")
        L.append('\t\t(uuid "%s")' % uid())
        L.append('\t\t(property "Reference" "%s"' % c["ref"])
        L.append("\t\t\t(at %s %s 0)" % (fmt(c["x"]), fmt(c["y"] - 12.7)))
        L.append("\t\t\t(effects")
        L.append("\t\t\t\t(font")
        L.append("\t\t\t\t\t(size 1.27 1.27)")
        L.append("\t\t\t\t)")
        L.append("\t\t\t)")
        L.append("\t\t)")
        L.append('\t\t(property "Value" "%s"' % c["value"])
        L.append("\t\t\t(at %s %s 0)" % (fmt(c["x"]), fmt(c["y"] + 12.7)))
        L.append("\t\t\t(effects")
        L.append("\t\t\t\t(font")
        L.append("\t\t\t\t\t(size 1.27 1.27)")
        L.append("\t\t\t\t)")
        L.append("\t\t\t)")
        L.append("\t\t)")
        L.append('\t\t(property "Footprint" "%s"' % c["footprint"])
        L.append("\t\t\t(at %s %s 0)" % (fmt(c["x"]), fmt(c["y"])))
        L.append("\t\t\t(effects")
        L.append("\t\t\t\t(font")
        L.append("\t\t\t\t\t(size 1.27 1.27)")
        L.append("\t\t\t\t)")
        L.append("\t\t\t(hide yes)")
        L.append("\t\t\t)")
        L.append("\t\t)")
        for pin in range(1, n + 1):
            L.append('\t\t(pin "%s"' % pin)
            L.append('\t\t\t(uuid "%s")' % uid())
            L.append("\t\t)")
        L.append("\t\t(instances")
        L.append('\t\t\t(project ""')
        L.append('\t\t\t\t(path "/%s"' % sheet_uuid)
        L.append('\t\t\t\t\t(reference "%s")' % c["ref"])
        L.append("\t\t\t\t\t(unit 1)")
        L.append("\t\t\t\t)")
        L.append("\t\t\t)")
        L.append("\t\t)")
        L.append("\t)")
    # Emit the PWR_FLAG symbols (their labels were emitted above, before all
    # symbols, to satisfy KiCad's order-sensitive parser). Each flag drives one
    # previously-undriven power net, clearing ERC "power pin not driven".
    for idx, (fx, fy, _nname) in enumerate(pwr_flag_spots):
        L.append("\t(symbol")
        L.append('\t\t(lib_id "power:PWR_FLAG")')
        L.append("\t\t(at %s %s 0)" % (fmt(fx), fmt(fy)))
        L.append("\t\t(unit 1)")
        L.append("\t\t(exclude_from_sim no)")
        L.append("\t\t(in_bom yes)")
        L.append("\t\t(on_board yes)")
        L.append("\t\t(dnp no)")
        L.append('\t\t(uuid "%s")' % uid())
        L.append('\t\t(property "Reference" "#FLG%02d"' % (idx + 1))
        L.append("\t\t\t(at %s %s 0)" % (fmt(fx), fmt(fy - 3.81)))
        L.append("\t\t\t(effects")
        L.append("\t\t\t\t(font")
        L.append("\t\t\t\t\t(size 1.27 1.27)")
        L.append("\t\t\t\t)")
        L.append("\t\t\t\t(hide yes)")
        L.append("\t\t\t)")
        L.append("\t\t)")
        L.append('\t\t(property "Value" "PWR_FLAG"')
        L.append("\t\t\t(at %s %s 0)" % (fmt(fx), fmt(fy + 3.81)))
        L.append("\t\t\t(effects")
        L.append("\t\t\t\t(font")
        L.append("\t\t\t\t\t(size 1.27 1.27)")
        L.append("\t\t\t\t)")
        L.append("\t\t\t)")
        L.append("\t\t)")
        L.append('\t\t(pin "1"')
        L.append('\t\t\t(uuid "%s")' % uid())
        L.append("\t\t)")
        L.append("\t\t(instances")
        L.append('\t\t\t(project ""')
        L.append('\t\t\t\t(path "/%s"' % sheet_uuid)
        L.append('\t\t\t\t\t(reference "#FLG%02d")' % (idx + 1))
        L.append("\t\t\t\t\t(unit 1)")
        L.append("\t\t\t\t)")
        L.append("\t\t\t)")
        L.append("\t\t)")
        L.append("\t)")
    L.append("\t(sheet_instances")
    L.append('\t\t(path "/"')
    L.append('\t\t\t(page "1")')
    L.append("\t\t)")
    L.append("\t)")
    L.append("\t(embedded_fonts no)")
    L.append(")")
    path = os.path.join(outdir, proj + ".kicad_sch")
    open(path, "w").write("\n".join(L) + "\n")
    # Report pin-label count in the "wires" slot so the summary stays honest
    # (connectivity is by label now, not routed wires).
    n_labels = sum(len(pts) for pts in nets.values())
    return path, len(all_segs), n_labels, len(nets)

# ---------------------------------------------------------------------------
# PCB  (.kicad_pcb)
# ---------------------------------------------------------------------------
def load_standard_footprint(fp_id, fps_dir, proj_pretty_dir, proj):
    lib, name = fp_id.split(":")
    if lib == proj:
        # custom footprint lives in the project .pretty dir as <name>.kicad_mod
        if proj_pretty_dir:
            p = os.path.join(proj_pretty_dir, name + ".kicad_mod")
            if os.path.isfile(p):
                return extract_block(open(p).read(), r'\(footprint "' + re.escape(name) + r'"')
        return None
    cand_dirs = set()
    # guess pretty dir from lib name; also try common dirs
    cands = [lib]
    for d in cands:
        p = os.path.join(fps_dir, d + ".pretty")
        if os.path.isdir(p):
            for f in os.listdir(p):
                if f.endswith(".kicad_mod"):
                    t = open(os.path.join(p, f)).read()
                    if ('(footprint "' + name + '"') in t:
                        return extract_block(t, r'\(footprint "' + re.escape(name) + r'"')
    # fallback: search all pretty dirs (bounded)
    if os.path.isdir(fps_dir):
        for d in os.listdir(fps_dir):
            p = os.path.join(fps_dir, d)
            if not os.path.isdir(p):
                continue
            for f in os.listdir(p):
                if f.endswith(".kicad_mod") and f.startswith(name):
                    t = open(os.path.join(p, f)).read()
                    if ('(footprint "' + name + '"') in t:
                        return extract_block(t, r'\(footprint "' + re.escape(name) + r'"')
    return None

def build_kicad_pcb(spec, outdir, proj, proj_pretty_dir):
    fps_dir = spec.get("kicad_fps_dir")
    board = spec.get("board", {})
    W = board.get("w", 100); H = board.get("h", 60); TH = board.get("thickness", 1.6)
    pad_off = spec.get("pad_off", {})
    comps = spec["comps"]
    gnd_zone = spec.get("gnd_zone", True)

    # pcb placement: use pbx/pby/prot if present, else x/y/0
    placed = {}
    for c in comps:
        px = c.get("pbx", c["x"]); py = c.get("pby", c["y"]); prot = c.get("prot", 0)
        placed[c["ref"]] = (c["footprint"], px, py, prot, c["pins"])

    # net index (0 = unconnected)
    net_index = {0: 0}; net_name = {0: "Unconnected"}; nxt = [1]
    def netno(n):
        if n not in net_index:
            net_index[n] = nxt[0]; net_name[nxt[0]] = n; nxt[0] += 1
        return net_index[n]
    for ref, (fp, x, y, rot, pmap) in placed.items():
        for pin, nname in pmap.items():
            if nname is not None:
                netno(nname)

    # pad abs positions
    pad_abs = {}
    for ref, (fp, x, y, rot, pmap) in placed.items():
        off = pad_off.get(fp, {})
        for pin, nname in pmap.items():
            if nname is None:
                continue
            if pin not in off:
                continue
            dx, dy = off[pin]
            if rot == 180:
                dx, dy = -dx, -dy
            elif rot == 90:
                dx, dy = -dy, dx
            elif rot == 270:
                dx, dy = dy, -dx
            pad_abs[(ref, pin)] = (nname, x + dx, y + dy)

    # ---- collision-aware Manhattan routing (grid A*) ----
    # Every net is routed only through FREE grid cells; all routed copper is
    # then marked occupied, so a later net can never share a coordinate with an
    # earlier one -> no same-layer shorts.
    import heapq
    G = 0.1                                   # grid resolution (mm)
    # size the grid to the pad bounding box + margin (much smaller than board)
    allp = list(pad_abs.values())
    minx = min(v[1] for v in allp); maxx = max(v[1] for v in allp)
    miny = min(v[2] for v in allp); maxy = max(v[2] for v in allp)
    MARG = 12.0
    bx0 = minx - MARG; bx1 = maxx + MARG
    by0 = miny - MARG; by1 = maxy + MARG
    gw = int(round((bx1 - bx0) / G)) + 2
    gh = int(round((by1 - by0) / G)) + 2
    occ = [[0] * gh for _ in range(gw)]

    def gcell(v):                             # mm -> grid index
        return int(round((v - bx0) / G))
    def mm(c):                                # grid index -> mm
        return bx0 + c * G

    rclear = int(round(0.55 / G))             # keepout ring around each pad
    for (ref, pin), (nn, px, py) in pad_abs.items():
        cx, cy = gcell(px), gcell(py)
        for a in range(cx - rclear, cx + rclear + 1):
            for b in range(cy - rclear, cy + rclear + 1):
                if 0 <= a < gw and 0 <= b < gh:
                    occ[a][b] = 1
    for a in range(gw):                       # border keepout
        occ[a][0] = 1; occ[a][gh - 1] = 1
    for b in range(gh):
        occ[0][b] = 1; occ[gw - 1][b] = 1

    def ring_cells(cell):
        cx, cy = cell
        out = []
        for a in range(cx - rclear, cx + rclear + 1):
            for b in range(cy - rclear, cy + rclear + 1):
                if 0 <= a < gw and 0 <= b < gh:
                    out.append((a, b))
        return out

    def astar(start, goal, cap=300000):
        if start == goal:
            return [start]
        h = lambda p: abs(p[0] - goal[0]) + abs(p[1] - goal[1])
        pq = [(h(start), 0, start)]
        came = {}; cost = {start: 0}
        closed = set(); expanded = 0
        while pq:
            f, _, node = heapq.heappop(pq)
            if node in closed:
                continue
            closed.add(node); expanded += 1
            if expanded > cap:
                return None
            if node == goal:
                path = [node]
                while node in came:
                    node = came[node]; path.append(node)
                path.reverse(); return path
            nx, ny = node
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nb = (nx + dx, ny + dy)
                ax, ay = nb
                if not (0 <= ax < gw and 0 <= ay < gh) or occ[ax][ay] or nb in closed:
                    continue
                nc = cost[node] + 1
                if nc < cost.get(nb, 1 << 60):
                    cost[nb] = nc; came[nb] = node
                    heapq.heappush(pq, (nc + h(nb), nc, nb))
        return None

    def route_pair(a_mm, b_mm):
        ca = (gcell(a_mm[0]), gcell(a_mm[1]))
        cb = (gcell(b_mm[0]), gcell(b_mm[1]))
        # temporarily free the start/goal pad rings so A* can step out of them
        # (a pad's own ring otherwise blocks all 4 neighbours of its centre)
        sa = [(a, b, occ[a][b]) for (a, b) in ring_cells(ca)]
        for (a, b, _) in sa:
            occ[a][b] = 0
        sb = [(a, b, occ[a][b]) for (a, b) in ring_cells(cb)]
        for (a, b, _) in sb:
            occ[a][b] = 0
        path = astar(ca, cb)
        for (a, b, v) in sa:
            occ[a][b] = v
        for (a, b, v) in sb:
            occ[a][b] = v
        if not path:
            return None
        # exact pad centers as the endpoints; A* cells in between
        return [a_mm] + [(mm(a), mm(b)) for a, b in path[1:-1]] + [b_mm]

    def merge_collinear(pts):
        out = []
        for i in range(1, len(pts)):
            a, b = pts[i - 1], pts[i]
            if out:
                pa, pb = out[-1]
                if (pa[0] == pb[0] and pb[0] == b[0]) or (pa[1] == pb[1] and pb[1] == b[1]):
                    out[-1] = (pa, b); continue
            out.append((a, b))
        return out

    net_pts = {}
    for (ref, pin), (nn, px, py) in pad_abs.items():
        net_pts.setdefault(nn, []).append((px, py))
    segs = []; failed = 0
    # route the most-pinful nets first (they are the most constrained)
    for nn, pts in sorted(net_pts.items(), key=lambda kv: len(kv[1]), reverse=True):
        n = netno(nn)
        if len(pts) <= 1:
            continue
        hub = pts[0]
        net_cells = set()
        for target in pts[1:]:
            path = route_pair(hub, target)
            if path is None:
                failed += 1
                continue
            for (a, b) in merge_collinear(path):
                segs.append((a, b, n))
            for (cx2, cy2) in path:
                net_cells.add((gcell(cx2), gcell(cy2)))
        # mark this net's copper occupied so later nets route around it
        for (a, b) in net_cells:
            if 0 <= a < gw and 0 <= b < gh:
                occ[a][b] = 1
    if failed:
        print("WARNING: %s routed connection(s) could not be placed" % failed, file=sys.stderr)

    L = []
    L.append('(kicad_pcb (version 20241229) (generator "pcbnew") (generator_version "9.0")')
    L.append("  (general")
    L.append("    (thickness %s)" % TH)
    L.append("  )")
    L.append('  (paper "A3")')
    L.append("  (layers")
    L.append('    (0 "F.Cu" signal)')
    L.append('    (31 "B.Cu" signal)')
    L.append("  )")
    L.append('  (gr_poly (pts (xy 0 0) (xy %s 0) (xy %s %s) (xy 0 %s)) (layer "Edge.Cuts") (width 0.1) (tstamp "zzzzzzzz-0000-0000-0000-000000000001"))' % (W, W, H, H))
    for i in range(1, max(net_index.values()) + 1):
        L.append('  (net %s "%s")' % (i, net_name[i]))
    for (a, b, n) in segs:
        L.append('  (segment (start %s %s) (end %s %s) (width 0.25) (layer "F.Cu") (net %s) (uuid "%s"))'
                 % (fmt(a[0]), fmt(a[1]), fmt(b[0]), fmt(b[1]), n, uid()))
    # footprints
    placed_count = 0
    for ref, (fp, x, y, rot, pmap) in placed.items():
        fpb = load_standard_footprint(fp, fps_dir, proj_pretty_dir, proj)
        if fpb is None:
            print("WARNING: missing footprint for", fp, file=sys.stderr)
            continue
        fpb = fpb.replace('(layer "F.Cu")', '(layer "F.Cu") (at %s %s %s)' % (fmt(x), fmt(y), rot), 1)
        for pin, nname in pmap.items():
            if nname is None:
                continue
            n = netno(nname)
            # robust: matches single-line (custom) and multi-line (system) pads,
            # SMD and through-hole, with or without rotation.
            # robust: matches SMD and through-hole, single-line (custom) and
            # multi-line (system), allowing (drill ...) etc. between size/layers.
            fpb = re.sub(r'(\(pad "' + str(pin) + r'" [^()]*\(\s*at [^()]*\)\s*\(size [^()]*\)(?:\s*\([^\n()]*\))*\s*\(layers [^()]*\))',
                        lambda m: m.group(1) + '\n\t\t(net "%s")' % nname, fpb, count=1)
        fpb = re.sub(r'\(property "Reference" "REF\*\*"\n\t+\(at [^)]*\)\n\t+\(layer "[^)]*"\)',
                     '(property "Reference" "%s"\n\t\t(at 0 0 0)\n\t\t(layer "F.SilkS")' % ref, fpb, count=1)
        fpb = re.sub(r'\(fp_text reference "REF\*\*"(\s*\(at [^)]*\))', r'(fp_text reference "%s"\1' % ref, fpb, count=1)
        L.append(fpb)
        placed_count += 1
    if gnd_zone and "GND" in net_index:
        gn = netno("GND")
        for layer, ts in [("F.Cu", "zzzzzzzz-0000-0000-0000-000000000001"), ("B.Cu", "zzzzzzzz-0000-0000-0000-000000000002")]:
            # KiCad 10 zone: net, net_name, layer, connect_pads <yes|no|clearance>,
            # fill, polygon (outline), uuid. (no thickness/min_width/path.)
            L.append('  (zone (net %s) (net_name "GND") (layer "%s")' % (gn, layer))
            L.append("    (connect_pads yes)")
            L.append("    (fill yes)")
            L.append('    (polygon (pts (xy 0 0) (xy %s 0) (xy %s %s) (xy 0 %s)))' % (W, W, H, H))
            L.append('    (uuid "%s")' % ts)
            L.append("  )")
    L.append(")")
    path = os.path.join(outdir, proj + ".kicad_pcb")
    open(path, "w").write("\n".join(L) + "\n")
    return path, placed_count, len(segs), len(net_index) - 1

# ---------------------------------------------------------------------------
# lib tables + .kicad_pro
# ---------------------------------------------------------------------------
def build_lib_tables(outdir, proj):
    sp = os.path.join(outdir, "sym-lib-table")
    open(sp, "w").write(
        "(sym_lib_table\n  (version 7)\n  (lib (name \"%s\")(type \"KiCad\")(uri \"${KIPRJMOD}/%s.kicad_sym\")(options \"\"))\n)\n" % (proj, proj))
    fp = os.path.join(outdir, "fp-lib-table")
    open(fp, "w").write(
        "(fp_lib_table\n  (version 7)\n  (lib (name \"%s\")(type \"KiCad\")(uri \"${KIPRJMOD}/%s.pretty\")(options \"\"))\n)\n" % (proj, proj))

def build_kicad_pro(outdir, proj, board, title):
    sheet = uid()
    W = board.get("w", 100); H = board.get("h", 60)
    pro = """{
	"board": {
		"design_settings": {
			"default_line_width": 0.2,
			"default_trace_width": 0.25,
			"min_line_width": 0.1,
			"min_trace_width": 0.1
		},
		"layer_setup": [
			{ "is_invisible": false, "name": "F.Cu", "thickness": 0.035, "type": "copper" },
			{ "is_invisible": false, "name": "B.Cu", "thickness": 0.035, "type": "copper" }
		],
		"layer_constraints": [],
		"board_constraints": { "width": %s, "height": %s },
		"stackup": [],
		"zone_design_rules": { "min_line_width": 0.0, "min_via_annular_width": 0.0 },
		"zone_connect_rules": { "type": 0 }
	},
	"cvpcb": { "rule_severities": {} },
	"boards": [],
	"general": {
		"3dviewers": [],
		"plot": { "layer_priority": [] },
		"autobackup": true
	},
	"git": { "last_seen": "" },
	"meta": {
		"filename": "%s.kicad_pro",
		"version": 1
	},
	"net_settings": {
		"drc_exclude_from_drc_check": [],
		"rules": {
			"allow_holes_in_copper": true,
			"max_error": 0.005,
			"min_clearance": 0.1,
			"min_line": 0.1,
			"min_resistor_pad_gap": 0.0,
			"min_silk_clearance": 0.0,
			"min_silk_line_width": 0.1,
			"min_silk_text_width": 0.1,
			"min_through_hole_pad_hole": 0.4,
			"min_through_hole_pad_size": 0.5,
			"min_track_width": 0.1,
			"min_via_annular_width": 0.1,
			"min_via_diameter": 0.3
		},
		"setup": {
			"bus_width": 0.2,
			"diff_pair_gap_width": 0.0,
			"diff_pair_via_gap": 0.0,
			"line_width": 0.2,
			"min_clearance": 0.1,
			"min_line_width": 0.1,
			"min_silk_width": 0.1,
			"min_text_size": 1.0,
			"min_track_width": 0.1,
			"min_via_annular_width": 0.1,
			"min_via_diameter": 0.3
		}
	},
	"pcbnew": {
		"last_modifications": { "board": { "B.Cu": false, "F.Cu": false } }
	},
	"fields": { "default": [] },
	"sheetname": "",
	"sheets": [
		{ "name": "%s", "path": "/%s" }
	],
	"symbol_libs": [
		{ "type": "KiCad", "uri": "sym-lib-table" },
		{ "type": "KiCad", "uri": "fp-lib-table" }
	],
	"text_variables": {}
}
""" % (W, H, proj, title, sheet)
    open(os.path.join(outdir, proj + ".kicad_pro"), "w").write(pro)

# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("spec", help="path to design.json")
    ap.add_argument("--out", required=True, help="output directory")
    ap.add_argument("--no-validate", action="store_true",
                    help="skip the post-generation validation gate")
    ap.add_argument("--strict", action="store_true",
                    help="fail generation if validation finds errors "
                         "(default: warn and continue)")
    a = ap.parse_args()
    spec = json.load(open(a.spec))
    proj = spec["project"]
    os.makedirs(a.out, exist_ok=True)

    pretty_dir = build_pretty_dir(spec.get("custom_footprints", []), a.out, proj)
    build_kicad_sym(spec.get("custom_symbols", []), a.out, proj)
    sch = build_kicad_sch(spec, a.out, proj)
    pcb = build_kicad_pcb(spec, a.out, proj, pretty_dir)
    build_lib_tables(a.out, proj)
    build_kicad_pro(a.out, proj, spec.get("board", {}), spec.get("title", proj))

    _, sw, sj, sn = sch
    _, pf, pt, pn = pcb
    print("wrote %s project:" % proj)
    print("  schematic: %s symbols, %s wire stubs, %s nets" % (len(spec["comps"]), sw, sn))
    print("  pcb: %s footprints, %s traces, %s nets" % (pf, pt, pn))

    # ---- validation gate -------------------------------------------------
    # Syntactically-valid output can still be semantically broken (e.g. the
    # question-marks bug). Always validate what we just wrote; --strict turns
    # errors into a non-zero exit for CI.
    if not a.no_validate:
        try:
            from validate_kicad import validate
            # Pass the spec so the netlist-connectivity check (opens + shorts
            # vs intent) runs too — the invariant that catches electrically-wrong
            # but syntactically-valid output.
            rep, _ = validate(a.out, use_cli=True, strict=a.strict, spec=a.spec)
            n_err = len(rep.errors)
            for c, m in rep.errors:
                print("  VALIDATION ERROR   [%s] %s" % (c, m), file=sys.stderr)
            for c, m in rep.warnings:
                print("  validation warning [%s] %s" % (c, m), file=sys.stderr)
            for c, m in rep.notices:
                print("  validation NOTICE  [%s] %s" % (c, m), file=sys.stderr)
            if n_err:
                print("validation: %d error(s)" % n_err, file=sys.stderr)
                if a.strict:
                    sys.exit("ERROR: generated project failed validation "
                             "(--strict); see messages above")
            else:
                print("validation: PASS")
        except ImportError:
            print("validation: skipped (validate_kicad.py not found)",
                  file=sys.stderr)

if __name__ == "__main__":
    main()
