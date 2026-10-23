# kicad-happy MCP server

A stdio [MCP](https://modelcontextprotocol.io) server (JSON-RPC 2.0,
newline-delimited) that exposes the suite's deterministic tools to any MCP
client — DeepSeek Harness included. Stdlib-only Python 3.10+; no network
access of its own (distributor search happens inside the wrapped LCSC
script).

## Tools

| Tool | Wraps | Purpose |
|---|---|---|
| `analyze_schematic` | `skills/kicad/scripts/analyze_schematic.py` | Schematic analysis (findings, power tree, nets, BOM data) |
| `analyze_pcb` | `skills/kicad/scripts/analyze_pcb.py` | PCB layout analysis (use `full: true`) |
| `analyze_emc` | `skills/emc/scripts/analyze_emc.py` | EMC pre-compliance (44 rules / 18 categories) |
| `analyze_thermal` | `skills/kicad/scripts/analyze_thermal.py` | Thermal hotspot analysis |
| `cross_analysis` | `skills/kicad/scripts/cross_analysis.py` | Cross-domain schematic↔PCB checks |
| `bom_analyze` | `skills/bom/scripts/bom_manager.py analyze` | BOM inventory + sourcing gaps |
| `bom_export` | `skills/bom/scripts/bom_manager.py export` | BOM tracking CSV export |
| `search_lcsc` | `skills/lcsc/scripts/search_lcsc.py` | LCSC/JLCPCB part search (no API key) |
| `datasheet_status` | — (inline) | Datasheet cache status for a project |

DigiKey/Mouser/element14 search remain agent-driven (their skills document
the API calls); only the no-auth LCSC search is wrapped here.

## Wiring into DeepSeek Harness

Profile patch (`~/.dsh/profiles/<profile>/cordis.patch.yml`):

```yaml
- insert:
    - id: mcp-kicad-happy
      name: '@deepseek-ai/dsh-mcp-client'
      config:
        serverName: kicad-happy
        transport: stdio
        command: python3 /absolute/path/to/kicad-happy-dsh/mcp/server.py
        # args: []            # not needed; the script locates skills/ relative to itself
        # env:                # e.g. KICAD_HAPPY_MCP_CWD: /path/to/your/project
        # cwd: ""             # working directory for the server (and wrapped scripts)
        # toolCallTimeoutMs: 600000
        # failOnStartupError: false
```

Notes:

- The server resolves the `skills/` tree **relative to its own location**,
  so the install path works from any checkout — no `args` needed.
- Relative paths in tool arguments resolve against the server's working
  directory (`cwd` in the row, or `KICAD_HAPPY_MCP_CWD` if set). Prefer
  absolute paths in tool calls, or pin `cwd` to the KiCad project.
- `toolCallTimeoutMs` bounds a single tool call (server-side subprocess
  timeout is 600 s; the client deadline applies first).

## Quick test without DSH

```bash
python3 - <<'PY'
import json, subprocess, sys
msgs = [
    {"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"t","version":"0"}}},
    {"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}},
]
p = subprocess.run([sys.executable, "mcp/server.py"],
    input="\n".join(json.dumps(m) for m in msgs) + "\n",
    capture_output=True, text=True)
print([t["name"] for t in json.loads(p.stdout.splitlines()[-1])["result"]["tools"]])
PY
```
