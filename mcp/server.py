#!/usr/bin/env python3
"""kicad-happy MCP server — exposes the suite's deterministic tools over MCP.

Stdio JSON-RPC (newline-delimited) implementation of the MCP protocol,
stdlib-only (Python 3.10+). Intended to be wired into a DeepSeek Harness
profile as a stdio MCP server:

    - id: mcp-kicad-happy
      name: '@deepseek-ai/dsh-mcp-client'
      config:
        serverName: kicad-happy
        transport: stdio
        command: python3 /abs/path/to/kicad-happy-dsh/mcp/server.py

Each tool wraps one of the skill scripts in `skills/*/scripts/` as a
subprocess and returns the script's JSON output (truncated to a size cap)
or a structured error. Tools:

    analyze_schematic  skills/kicad/scripts/analyze_schematic.py
    analyze_pcb        skills/kicad/scripts/analyze_pcb.py
    analyze_emc        skills/emc/scripts/analyze_emc.py
    analyze_thermal    skills/kicad/scripts/analyze_thermal.py
    cross_analysis     skills/kicad/scripts/cross_analysis.py
    bom_analyze        skills/bom/scripts/bom_manager.py analyze
    bom_export         skills/bom/scripts/bom_manager.py export
    search_lcsc        skills/lcsc/scripts/search_lcsc.py
    datasheet_status   datasheet cache status for a project

No third-party imports, no network access from this process (distributor
search happens inside the wrapped LCSC script; DigiKey/Mouser/element14
search stay agent-driven per their skills).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PROTOCOL_VERSION = "2025-03-26"
SERVER_NAME = "kicad-happy"
SERVER_VERSION = "3.0.0"
MAX_OUTPUT_CHARS = 200_000
SUBPROCESS_TIMEOUT_S = 600


# ---------------------------------------------------------------------------
# Tool registry
# ---------------------------------------------------------------------------

class Tool:
    """One MCP tool: a fixed script plus a params->argv mapping."""

    def __init__(self, name: str, description: str, script: Path,
                 schema: dict, build_argv):
        self.name = name
        self.description = description
        self.script = script
        self.schema = schema
        self.build_argv = build_argv  # (args: dict) -> list[str]


TOOLS: list[Tool] = [
    Tool(
        name="analyze_schematic",
        description=(
            "Run the deterministic schematic analyzer on a .kicad_sch file. "
            "Returns the analyzer JSON (findings, power tree, nets, BOM data, "
            "trust_summary). Optional --output writes the JSON to a file; the "
            "response then carries output_path plus a preview."
        ),
        script=REPO_ROOT / "skills/kicad/scripts/analyze_schematic.py",
        schema={
            "type": "object",
            "properties": {
                "schematic": {"type": "string", "description": "Path to the .kicad_sch file"},
                "output": {"type": "string", "description": "Optional output JSON path"},
                "no_hierarchy": {"type": "boolean"},
                "lifecycle": {"type": "boolean", "description": "Enable lifecycle (EOL/NRND) audit"},
                "stage": {"type": "string", "enum": ["schematic", "layout", "pre_fab", "bring_up"]},
                "audience": {"type": "string", "enum": ["designer", "reviewer", "manager"]},
                "text": {"type": "boolean", "description": "Human-readable report instead of JSON"},
            },
            "required": ["schematic"],
            "additionalProperties": False,
        },
        build_argv=lambda a: _build([
            a["schematic"],
            ("--output", a.get("output")),
            ("--no-hierarchy", a.get("no_hierarchy")),
            ("--lifecycle", a.get("lifecycle")),
            ("--stage", a.get("stage")),
            ("--audience", a.get("audience")),
            ("--text", a.get("text")),
        ]),
    ),
    Tool(
        name="analyze_pcb",
        description=(
            "Run the deterministic PCB analyzer on a .kicad_pcb file. "
            "Pass 'full' for the complete analysis (footprints, tracks, vias, "
            "zones, DFM, assembly checks, connectivity graph). Point "
            "'schematic' at a schematic analyzer JSON to classify power rails."
        ),
        script=REPO_ROOT / "skills/kicad/scripts/analyze_pcb.py",
        schema={
            "type": "object",
            "properties": {
                "pcb": {"type": "string", "description": "Path to the .kicad_pcb file"},
                "full": {"type": "boolean", "description": "Run the full analysis (recommended)"},
                "schematic": {"type": "string", "description": "Schematic analyzer JSON for rail classification"},
                "power_rails": {"type": "string", "description": "Comma-separated declared power rails"},
                "proximity": {"type": "boolean"},
                "output": {"type": "string"},
                "stage": {"type": "string", "enum": ["schematic", "layout", "pre_fab", "bring_up"]},
                "audience": {"type": "string", "enum": ["designer", "reviewer", "manager"]},
                "text": {"type": "boolean"},
            },
            "required": ["pcb"],
            "additionalProperties": False,
        },
        build_argv=lambda a: _build([
            a["pcb"],
            ("--full", a.get("full")),
            ("--schematic", a.get("schematic")),
            ("--power-rails", a.get("power_rails")),
            ("--proximity", a.get("proximity")),
            ("--output", a.get("output")),
            ("--stage", a.get("stage")),
            ("--audience", a.get("audience")),
            ("--text", a.get("text")),
        ]),
    ),
    Tool(
        name="analyze_emc",
        description=(
            "Run EMC pre-compliance analysis (44 rules, 18 categories) over "
            "schematic + PCB analyzer JSON. 'standard' selects the emission "
            "standard (fcc-class-b, cispr-32, cispr-25, mil-std-461g). "
            "'spice_enhanced' enables SPICE-enhanced PDN/filter checks when a "
            "simulator is installed."
        ),
        script=REPO_ROOT / "skills/emc/scripts/analyze_emc.py",
        schema={
            "type": "object",
            "properties": {
                "schematic": {"type": "string", "description": "Schematic analyzer JSON path"},
                "pcb": {"type": "string", "description": "PCB analyzer JSON path"},
                "severity": {"type": "string", "enum": ["all", "info", "warning", "error"]},
                "standard": {"type": "string", "enum": ["fcc-class-b", "cispr-32", "cispr-25", "mil-std-461g"]},
                "spice_enhanced": {"type": "boolean"},
                "output": {"type": "string"},
                "text": {"type": "boolean"},
            },
            "required": ["schematic", "pcb"],
            "additionalProperties": False,
        },
        build_argv=lambda a: _build([
            ("--schematic", a["schematic"]),
            ("--pcb", a["pcb"]),
            ("--severity", a.get("severity")),
            ("--standard", a.get("standard")),
            ("--spice-enhanced", a.get("spice_enhanced")),
            ("--output", a.get("output")),
            ("--text", a.get("text")),
        ]),
    ),
    Tool(
        name="analyze_thermal",
        description=(
            "Run thermal hotspot analysis over schematic + PCB analyzer JSON "
            "(junction temperatures for regulators and high-power parts)."
        ),
        script=REPO_ROOT / "skills/kicad/scripts/analyze_thermal.py",
        schema={
            "type": "object",
            "properties": {
                "schematic": {"type": "string", "description": "Schematic analyzer JSON path"},
                "pcb": {"type": "string", "description": "PCB analyzer JSON path"},
                "ambient": {"type": "number", "description": "Ambient temperature in C (default 25)"},
                "datasheets": {"type": "string", "description": "Path to datasheets/ directory for Rtheta data"},
                "only_deterministic": {"type": "boolean"},
                "output": {"type": "string"},
                "text": {"type": "boolean"},
            },
            "required": ["schematic", "pcb"],
            "additionalProperties": False,
        },
        build_argv=lambda a: _build([
            ("--schematic", a["schematic"]),
            ("--pcb", a["pcb"]),
            ("--ambient", a.get("ambient")),
            ("--datasheets", a.get("datasheets")),
            ("--only-deterministic", a.get("only_deterministic")),
            ("--output", a.get("output")),
            ("--text", a.get("text")),
        ]),
    ),
    Tool(
        name="cross_analysis",
        description=(
            "Run cross-domain checks between schematic and PCB analyzer JSON "
            "(connector current, ESD gaps, decoupling adequacy, 3-way "
            "schematic/PCB cross-validation)."
        ),
        script=REPO_ROOT / "skills/kicad/scripts/cross_analysis.py",
        schema={
            "type": "object",
            "properties": {
                "schematic": {"type": "string", "description": "Schematic analyzer JSON path"},
                "pcb": {"type": "string", "description": "PCB analyzer JSON path"},
                "output": {"type": "string"},
                "text": {"type": "boolean"},
            },
            "required": ["schematic", "pcb"],
            "additionalProperties": False,
        },
        build_argv=lambda a: _build([
            ("--schematic", a["schematic"]),
            ("--pcb", a["pcb"]),
            ("--output", a.get("output")),
            ("--text", a.get("text")),
        ]),
    ),
    Tool(
        name="bom_analyze",
        description=(
            "Analyze a schematic's BOM (bom_manager analyze): part inventory, "
            "sourcing gaps (missing MPN/package/quantity). 'gaps_only' shows "
            "only incomplete lines."
        ),
        script=REPO_ROOT / "skills/bom/scripts/bom_manager.py",
        schema={
            "type": "object",
            "properties": {
                "schematic": {"type": "string", "description": "Path to the .kicad_sch file"},
                "gaps_only": {"type": "boolean"},
                "recursive": {"type": "boolean", "description": "Include hierarchical sub-sheets"},
            },
            "required": ["schematic"],
            "additionalProperties": False,
        },
        build_argv=lambda a: _build([
            "analyze",
            a["schematic"],
            "--json",
            ("--gaps-only", a.get("gaps_only")),
            ("--recursive", a.get("recursive")),
        ]),
    ),
    Tool(
        name="bom_export",
        description=(
            "Export a BOM tracking CSV from a schematic (bom_manager export). "
            "'add_distributor' appends distributor columns per name."
        ),
        script=REPO_ROOT / "skills/bom/scripts/bom_manager.py",
        schema={
            "type": "object",
            "properties": {
                "schematic": {"type": "string", "description": "Path to the .kicad_sch file"},
                "output": {"type": "string", "description": "Output CSV path"},
                "recursive": {"type": "boolean"},
                "add_distributor": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["schematic", "output"],
            "additionalProperties": False,
        },
        build_argv=lambda a: _build([
            "export",
            a["schematic"],
            ("--output", a["output"]),
            ("--recursive", a.get("recursive")),
            *[f"--add-distributor={d}" for d in (a.get("add_distributor") or [])],
        ]),
    ),
    Tool(
        name="search_lcsc",
        description=(
            "Search LCSC/JLCPCB parts by MPN, LCSC code (Cxxxxx) or keywords "
            "(no API key needed). Returns matches with price/stock."
        ),
        script=REPO_ROOT / "skills/lcsc/scripts/search_lcsc.py",
        schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "MPN, LCSC code (Cxxxxx), or keywords"},
                "limit": {"type": "integer", "default": 10},
                "package": {"type": "string", "description": "Footprint filter (e.g. 0805, SOT-23)"},
                "basic": {"type": "boolean", "description": "Only JLCPCB Basic parts"},
                "in_stock": {"type": "boolean"},
                "min_stock": {"type": "integer"},
                "category": {"type": "string", "description": "Parametric category (resistors, capacitors, microcontrollers, voltage_regulators)"},
                "sort": {"type": "string", "enum": ["relevance", "price", "stock"]},
                "details": {"type": "boolean", "description": "Enrich results from LCSC detail endpoint (slower)"},
                "json": {"type": "boolean", "description": "Raw JSON output"},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        build_argv=lambda a: _build([
            a["query"],
            ("--limit", a.get("limit")),
            ("--package", a.get("package")),
            ("--basic", a.get("basic")),
            ("--in-stock", a.get("in_stock")),
            ("--min-stock", a.get("min_stock")),
            ("--category", a.get("category")),
            ("--sort", a.get("sort")),
            ("--details", a.get("details")),
            ("--json", a.get("json")),
        ]),
    ),
    Tool(
        name="datasheet_status",
        description=(
            "Report the datasheet extraction cache status for a project: "
            "which MPNs have extracted specs, their quality scores, and "
            "staleness (PDF hash vs manifest). Reads <project>/datasheets/ "
            "(or 'cache_dir' if given)."
        ),
        script=None,  # handled inline
        schema={
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": "Project directory containing datasheets/"},
                "cache_dir": {"type": "string", "description": "Explicit cache directory (overrides <project>/datasheets/extracted)"},
            },
            "required": ["project"],
            "additionalProperties": False,
        },
        build_argv=None,
    ),
]


def _build(entries) -> list[str]:
    """Build an argv from positional strings and (flag, value) pairs."""
    argv: list[str] = []
    for entry in entries:
        if entry is None:
            continue
        if isinstance(entry, str):
            argv.append(entry)
            continue
        flag, value = entry
        if value is None:
            continue
        if isinstance(value, bool):
            if value:
                argv.append(flag)
        else:
            argv.extend([flag, str(value)])
    return argv


# ---------------------------------------------------------------------------
# Tool execution
# ---------------------------------------------------------------------------

def _run_script(script: Path, argv: list[str]) -> dict:
    """Run a wrapped skill script; return a tool-result content dict."""
    cmd = [sys.executable, str(script), *argv]
    cwd = os.environ.get("KICAD_HAPPY_MCP_CWD") or os.getcwd()
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_TIMEOUT_S,
            cwd=cwd,
        )
    except subprocess.TimeoutExpired:
        return {"content": [{"type": "text", "text":
                             f"ERROR: script timed out after {SUBPROCESS_TIMEOUT_S}s: {script.name}"}],
                "isError": True}

    if proc.returncode != 0:
        stderr_tail = (proc.stderr or "")[-2000:]
        return {"content": [{"type": "text", "text":
                             f"ERROR: {script.name} exited {proc.returncode}\n{stderr_tail}"}],
                "isError": True}

    stdout = proc.stdout or ""
    output_path = None
    for i, a in enumerate(argv):
        if a in ("--output", "-o") and i + 1 < len(argv):
            output_path = argv[i + 1]
            break

    text = stdout
    truncated = False
    if len(text) > MAX_OUTPUT_CHARS:
        truncated = True
        text = text[:MAX_OUTPUT_CHARS] + f"\n… [truncated at {MAX_OUTPUT_CHARS} chars]"

    extra = ""
    if output_path:
        p = Path(output_path)
        size = p.stat().st_size if p.exists() else None
        extra = f"\n[output written to {output_path}]" + (f" ({size} bytes)" if size is not None else "")

    return {"content": [{"type": "text", "text": text.strip() + extra}],
            "isError": False,
            "structured": {"truncated": truncated, "output_path": output_path}}


def _datasheet_status(args: dict) -> dict:
    """Inline tool: report the project's datasheet cache state."""
    project = Path(args["project"])
    cache_dir = Path(args.get("cache_dir") or (project / "datasheets/extracted"))
    manifest = Path(args.get("cache_dir") or (project / "datasheets")) / "manifest.json"

    report: dict = {"cache_dir": str(cache_dir), "extracted_mpns": [], "manifest": None}
    if manifest.exists():
        try:
            report["manifest"] = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            report["manifest"] = f"unparseable: {exc}"
    if cache_dir.is_dir():
        for f in sorted(cache_dir.glob("*.json")):
            if f.name.endswith(".result.json"):
                continue
            entry = {"mpn": f.stem, "bytes": f.stat().st_size}
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    entry["score"] = data.get("quality_score") or data.get("score")
                    entry["status"] = data.get("status")
            except (OSError, json.JSONDecodeError):
                entry["parse_error"] = True
            report["extracted_mpns"].append(entry)
    if not report["extracted_mpns"] and report["manifest"] is None:
        report["note"] = (f"No datasheet cache found under {project}/datasheets. "
                          "Run a distributor sync (datasheets skill) to populate it.")
    return {"content": [{"type": "text", "text": json.dumps(report, indent=2)}], "isError": False}


def _tool_result(tool: Tool, args: dict) -> dict:
    if tool.name == "datasheet_status":
        return _datasheet_status(args)
    argv = tool.build_argv(args)
    return _run_script(tool.script, argv)


# ---------------------------------------------------------------------------
# MCP protocol (newline-delimited JSON-RPC over stdio)
# ---------------------------------------------------------------------------

def _reply(rid, result: dict | None):
    if rid is None:
        return
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": rid, "result": result}) + "\n")
    sys.stdout.flush()


def _error(rid, code: int, message: str):
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": rid,
                                 "error": {"code": code, "message": message}}) + "\n")
    sys.stdout.flush()


def handle(msg: dict) -> None:
    method = msg.get("method")
    rid = msg.get("id")
    params = msg.get("params") or {}

    if method is None:
        return  # response to our request (shouldn't happen); ignore

    if method == "initialize":
        _reply(rid, {
            "protocolVersion": params.get("protocolVersion") or PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        })
    elif method == "tools/list":
        _reply(rid, {"tools": [
            {
                "name": t.name,
                "description": t.description,
                "inputSchema": t.schema,
            }
            for t in TOOLS
        ]})
    elif method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        tool = next((t for t in TOOLS if t.name == name), None)
        if tool is None:
            _error(rid, -32602, f"unknown tool {name!r}")
            return
        try:
            result = _tool_result(tool, args)
        except Exception as exc:  # defensive: never crash the server loop
            result = {"content": [{"type": "text", "text": f"ERROR: {exc}"}], "isError": True}
        _reply(rid, result)
    elif method == "ping":
        _reply(rid, {})
    else:
        # notifications (initialized, cancelled, …) and unknown methods: no reply
        if rid is not None:
            _error(rid, -32601, f"method not found: {method}")


def main() -> int:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        try:
            handle(msg)
        except Exception as exc:
            if msg.get("id") is not None:
                _error(msg["id"], -32603, f"internal error: {exc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
