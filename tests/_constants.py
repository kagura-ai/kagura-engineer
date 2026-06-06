"""Canonical test constants for kagura-engineer.

These values represent a minimal valid `Config` shape, used as fixtures
across all test files. Centralizing them means that adding a new
required field to `Config` is a one-line change in `conftest.py` plus
a one-line update here, not a hunt-and-replace across every test.

Kept in a plain module (rather than inside `conftest.py`) so it can be
imported via `from tests._constants import X` from any test file
without needing `tests/` to be a package.
"""
from __future__ import annotations

VALID_PROFILE = "coding"
VALID_MEMORY_URL = "https://memory.kagura-ai.com"
VALID_WORKSPACE = "ws-coding-dev"
VALID_CONTEXT_UUID = "550e8400-e29b-41d4-a716-446655440000"
