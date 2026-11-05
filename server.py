import subprocess

from mcp.server.mcpserver import MCPServer

mcp = MCPServer("navi")


def run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["shutdown", *args], capture_output=True, text=True)


@mcp.tool()
def shutdown(confirm: bool = False, delay_minutes: int = 1) -> str:
    """Power off the computer. Only use confirm=True after the user explicitly confirmed.
    The shutdown is scheduled (1 to 60 minutes) so it can still be cancelled."""
    if not confirm:
        return "Shutdown not scheduled: ask the user to confirm first."
    delay = max(1, min(delay_minutes, 60))
    result = run("-h", f"+{delay}")
    if result.returncode != 0:
        return f"Shutdown failed: {result.stderr.strip()}"
    return f"Shutdown scheduled in {delay} minute(s). Use cancel_shutdown to abort."


@mcp.tool()
def cancel_shutdown() -> str:
    """Cancel a scheduled shutdown."""
    result = run("-c")
    if result.returncode != 0:
        return f"Could not cancel: {result.stderr.strip()}"
    return "Scheduled shutdown cancelled."


mcp.run()
