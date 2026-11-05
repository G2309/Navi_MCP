# Navi MCP

An [MCP](https://modelcontextprotocol.io) server that lets a local voice assistant control the system.
The goal is to talk to the computer ("Navi, shut down in five minutes") and have a local model
(transcription with LFM3-Omni, reasoning with Qwen via Ollama) call safe system tools through MCP.

This first version only exposes a safe shutdown. More system tools will be built on top of it.

## Tools

| Tool | Description |
|------|-------------|
| `shutdown(confirm, delay_minutes)` | Schedules a power off in 1 to 60 minutes. Does nothing unless `confirm=True`. |
| `cancel_shutdown()` | Cancels a scheduled shutdown. |

Shutdowns are always scheduled, never immediate, so a misheard command can still be cancelled
with `cancel_shutdown` or `shutdown -c`.

## Run

```sh
nix shell --impure --expr '(import <nixpkgs> {}).python3.withPackages (p: [ p.mcp p.pytest ])'
python server.py
```

Inspect the tools with the MCP inspector:

```sh
npx @modelcontextprotocol/inspector python server.py
```

## Test

```sh
pytest
```

## Requirements

- Python 3 with the official [MCP SDK](https://github.com/modelcontextprotocol/python-sdk) 2.x (`MCPServer`, formerly FastMCP).
- A logged-in systemd session, so `shutdown` works without root through polkit.
