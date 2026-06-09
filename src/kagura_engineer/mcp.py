"""Domain MCP vocabulary kept out of the generic claude-axis harness.

``kagura-claude-harness`` owns the launcher seam (``brain.invoke``) and the
generic ``mcp_args(mcp_config, allowed_tools)`` helper, but deliberately carries
no memory-tool vocabulary. These are *our* tool names — the in-task
recall/remember tools we pre-approve for headless ``claude -p`` sessions — so we
keep them here and pass them to the harness as ``allowed_tools``.
"""
from __future__ import annotations

MEMORY_TOOLS = ("mcp__kagura-memory__recall", "mcp__kagura-memory__remember")
