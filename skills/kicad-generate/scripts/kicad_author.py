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

def build_kicad_sch(spec, outdir, proj):
    syms_dir = spec.get("kicad_syms_dir")
    pin_tip = spec.get("pin_tip", {})
    n_pins = spec.get("n_pins", {})
    comps = spec["comps"]
    custom_names = {s["name"] for s in spec.get("custom_symbols", [])}

    # collect lib_symbols to embed (system + custom)
    lib_blocks, seen = [], set()
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
            continue
        key = re.sub(r'\s+', '', b)
        if key in seen:
            continue
        seen.add(key)
        lib_blocks.append(b)
        # handle (extends ...) base symbols
        mb = re.search(r'\(extends "([^"]+)"', b)
        if mb:
            base = None
            lib, nm = lib_id.split(":")
            bp = os.path.join(syms_dir, lib + ".kicad_sym")
            if os.path.isfile(bp):
                base = extract_block(open(bp).read(), r'\(symbol "' + re.escape(mb.group(1)) + r'"')
            if base:
                keyb = re.sub(r'\s+', '', base)
                if keyb not in seen:
                    seen.add(keyb); lib_blocks.append(base)

    # net graph from pin tips
    nets = {}
    def net(n):
        return nets.setdefault(n, [])
    for c in comps:
        tip = pin_tip.get(c["lib_id"], {})
        for pin, nname in c["pins"].items():
            if nname is None:
                continue
            dx, dy = tip[pin]
            net(nname).append((c["x"] + dx, c["y"] + dy))

    def route_net(pts):
        if len(pts) <= 1:
            return [], []
        xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
        mx = min(xs); my = sorted(ys)[len(ys) // 2]
        junctions = set(); segs = []
        for (px, py) in pts:
            if abs(px - mx) < 1e-6 and abs(py - my) < 1e-6:
                junctions.add((round(mx, 3), round(my, 3))); continue
            if abs(px - mx) < 1e-6:
                if py != my:
                    segs.append(((px, py), (mx, my)))
                    junctions.add((round(mx, 3), round(my, 3)))
            else:
                segs.append(((px, py), (px, my)))
                segs.append(((px, my), (mx, my)))
                junctions.add((round(px, 3), round(my, 3)))
                junctions.add((round(mx, 3), round(my, 3)))
        merged = []
        for a, b in segs:
            if merged and (a[0] == merged[-1][1][0] and a[1] == merged[-1][1][1]):
                pa, pb = merged[-1]
                if (pa[0] == pb[0] and a[0] == b[0]) or (pa[1] == pb[1] and a[1] == b[1]):
                    merged[-1] = (pa, b); continue
            merged.append((a, b))
        return merged, sorted(junctions)

    all_segs, all_junc = [], []
    for nname, npts in nets.items():
        s, j = route_net(npts)
        all_segs.extend(s); all_junc.extend(j)

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
    for (jx, jy) in set(all_junc):
        L.append("\t(junction")
        L.append("\t\t(at %s %s)" % (fmt(jx), fmt(jy)))
        L.append("\t\t(diameter 0)")
        L.append('\t\t(uuid "%s")' % uid())
        L.append("\t)")
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
    for nname, npts in nets.items():
        # Place the label exactly on an actual pin tip so it electrically
        # connects to that pin (an offset label would dangle / be unconnected).
        lx, ly = npts[0]
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
    L.append("\t(sheet_instances")
    L.append('\t\t(path "/"')
    L.append('\t\t\t(page "1")')
    L.append("\t\t)")
    L.append("\t)")
    L.append("\t(embedded_fonts no)")
    L.append(")")
    path = os.path.join(outdir, proj + ".kicad_sch")
    open(path, "w").write("\n".join(L) + "\n")
    return path, len(all_segs), len(set(all_junc)), len(nets)

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
    print("  schematic: %s symbols, %s wires, %s junctions, %s nets" % (len(spec["comps"]), sw, sj, sn))
    print("  pcb: %s footprints, %s traces, %s nets" % (pf, pt, pn))

if __name__ == "__main__":
    main()
