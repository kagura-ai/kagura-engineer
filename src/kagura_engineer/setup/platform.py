"""Platform detection: OS, package manager, WSL flag.

All other setup steps import from this module to pick the right
install strategy. The detection is intentionally narrow:

  - Linux distro -> apt | dnf | pacman
  - macOS       -> brew
  - Windows     -> winget (best-effort; Plan 2 v1 only hints at Windows)

Anything we cannot classify falls into `OTHER` and the caller is
responsible for surfacing a NEEDS_USER fix_hint (the user has to
install the tool by hand). We never auto-install on a platform we
do not recognize, because that is the difference between "I
provisioned my dev box" and "I just nuked a CI worker".

WSL is detected via /proc/version's "Microsoft" substring. WSL gets
the same package manager as the underlying distro (apt on
WSL1/WSL2/Ubuntu, etc.) — there is no separate "wsl" strategy.
"""
from __future__ import annotations

import shutil
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Literal

OS = Literal["linux", "darwin", "windows", "other"]
PkgManager = Literal["apt", "brew", "dnf", "pacman", "winget", "none"]


class OSKind(str, Enum):
    LINUX = "linux"
    DARWIN = "darwin"
    WINDOWS = "windows"
    OTHER = "other"


class PkgManagerKind(str, Enum):
    APT = "apt"
    BREW = "brew"
    DNF = "dnf"
    PACMAN = "pacman"
    WINGET = "winget"
    NONE = "none"


@dataclass(frozen=True)
class PlatformInfo:
    os: OSKind
    pkg_manager: PkgManagerKind
    is_wsl: bool
    # Useful for the install command prefix; the caller may still want
    # to ask the user for a password. `sudo` is always present on
    # Linux/macOS in practice, but we resolve it via `shutil.which` so
    # a chroot / sandbox without sudo can degrade gracefully.
    has_sudo: bool

    @property
    def can_auto_install(self) -> bool:
        # A platform is auto-installable iff we recognize it AND a
        # package manager is on PATH AND sudo is available. The caller
        # is still free to refuse (e.g. `--dry-run`).
        return (
            self.os in (OSKind.LINUX, OSKind.DARWIN)
            and self.pkg_manager is not PkgManagerKind.NONE
            and self.has_sudo
        )


def detect_os() -> OSKind:
    """Best-effort OS classification from `sys.platform`."""
    p = sys.platform
    if p.startswith("linux"):
        return OSKind.LINUX
    if p == "darwin":
        return OSKind.DARWIN
    # `sys.platform` returns "win32" on all Windows versions (yes, even 64-bit).
    if p in ("win32", "cygwin"):
        return OSKind.WINDOWS
    return OSKind.OTHER


def is_wsl() -> bool:
    """True when running under WSL (1 or 2) on Windows."""
    proc_version = Path("/proc/version")
    try:
        text = proc_version.read_text(encoding="utf-8", errors="replace").lower()
    except OSError:
        return False
    return "microsoft" in text or "wsl" in text


def detect_pkg_manager(os: OSKind | None = None) -> PkgManagerKind:
    """First package manager we recognize on PATH, gated by OS."""
    os = os or detect_os()
    candidates: tuple[tuple[OSKind, PkgManagerKind, str], ...] = (
        (OSKind.LINUX, PkgManagerKind.APT, "apt-get"),
        (OSKind.LINUX, PkgManagerKind.DNF, "dnf"),
        (OSKind.LINUX, PkgManagerKind.PACMAN, "pacman"),
        (OSKind.DARWIN, PkgManagerKind.BREW, "brew"),
        (OSKind.WINDOWS, PkgManagerKind.WINGET, "winget"),
    )
    for allowed_os, kind, binary in candidates:
        if os is not allowed_os:
            continue
        if shutil.which(binary):
            return kind
    return PkgManagerKind.NONE


def detect() -> PlatformInfo:
    """One-shot detection; cached by the caller if it is hot-pathed."""
    os = detect_os()
    return PlatformInfo(
        os=os,
        pkg_manager=detect_pkg_manager(os),
        is_wsl=is_wsl() if os is OSKind.LINUX else False,
        has_sudo=shutil.which("sudo") is not None,
    )
