"""Domain MCP vocabulary kept out of the generic claude-axis harness.

``kagura-brain`` owns the launcher seam (the ``claude`` adapter's ``invoke``) and
the generic ``mcp_args(mcp_config, allowed_tools)`` helper, but deliberately carries
no memory-tool vocabulary. These are *our* tool names — the in-task
recall/remember tools we pre-approve for headless ``claude -p`` sessions — so we
keep them here and pass them to the harness as ``allowed_tools``.
"""
from __future__ import annotations

MEMORY_TOOLS = ("mcp__kagura-memory__recall", "mcp__kagura-memory__remember")
# Codex registers MCP tools under normalized identifiers (server name
# hyphen -> underscore, verified on codex-cli 0.133.0), so the same tools
# surface there as mcp__kagura_memory__* — the claude-style ids above do not
# exist in a codex session.
CODEX_MEMORY_TOOLS = ("mcp__kagura_memory__recall", "mcp__kagura_memory__remember")


def memory_tool_ids(backend: str) -> tuple[str, str]:
    """The (recall, remember) tool ids as the given backend's runtime names them."""
    return CODEX_MEMORY_TOOLS if backend == "codex" else MEMORY_TOOLS
