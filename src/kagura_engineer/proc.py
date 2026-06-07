"""Small subprocess helpers shared by the run/ and review/ subprocess wrappers."""
from __future__ import annotations


def as_text(value: bytes | str | None) -> str:
    """Normalize subprocess stdout/stderr to ``str``.

    ``subprocess.TimeoutExpired`` carries the *raw bytes* captured before the
    kill even when the process was launched with ``text=True`` — so a timeout
    with partial output yields ``bytes``, not ``str``. Decode bytes (replacing
    undecodable sequences); map ``None``/empty to ``""``.
    """
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return value or ""
