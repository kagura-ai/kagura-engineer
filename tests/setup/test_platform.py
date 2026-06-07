"""Unit tests for setup.platform detection.

The detection functions are intentionally side-effect free: they read
`sys.platform` and the filesystem, but never call out to a network or
subprocess. Each test monkeypatches one of those inputs to exercise
the matrix.
"""
from __future__ import annotations

import sys

import pytest

from kagura_engineer.setup.platform import (
    OSKind,
    PkgManagerKind,
    PlatformInfo,
    detect,
    detect_os,
    detect_pkg_manager,
    is_wsl,
)


# --- detect_os ---------------------------------------------------------


def test_detect_os_linux(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    assert detect_os() is OSKind.LINUX


def test_detect_os_darwin(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    assert detect_os() is OSKind.DARWIN


def test_detect_os_windows(monkeypatch):
    # `sys.platform` is "win32" on all Windows versions.
    monkeypatch.setattr(sys, "platform", "win32")
    assert detect_os() is OSKind.WINDOWS


def test_detect_os_other(monkeypatch):
    monkeypatch.setattr(sys, "platform", "freebsd")
    assert detect_os() is OSKind.OTHER


# --- is_wsl ------------------------------------------------------------


def test_is_wsl_true_when_microsoft_in_proc_version(tmp_path, monkeypatch):
    fake_proc = tmp_path / "version"
    fake_proc.write_text("Linux version 5.15.0 (Microsoft@somewhere)\n")
    # `is_wsl` reads the hard-coded path; monkey-patch via a symlink would
    # be ideal but Path("/proc/version") is not writable in a test env.
    # Instead we patch the module-level constant via attribute set.
    from kagura_engineer.setup import platform as p

    monkeypatch.setattr(p, "Path", lambda *_a, **_k: fake_proc)
    assert is_wsl() is True


def test_is_wsl_false_for_plain_linux(monkeypatch):
    from kagura_engineer.setup import platform as p

    fake_proc = type("P", (), {"read_text": staticmethod(lambda *a, **k: "Linux 5.15\n")})()
    monkeypatch.setattr(p, "Path", lambda *_a, **_k: fake_proc)
    assert is_wsl() is False


# --- detect_pkg_manager ------------------------------------------------


def test_pkg_manager_apt_when_present(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    # `shutil.which` returns truthy if the binary exists; we model
    # "apt-get present, dnf absent, pacman absent" by mapping each name
    # to a boolean.
    table = {"apt-get": "/usr/bin/apt-get", "dnf": None, "pacman": None}
    monkeypatch.setattr("shutil.which", lambda name: table.get(name))
    assert detect_pkg_manager() is PkgManagerKind.APT


def test_pkg_manager_falls_through_to_dnf(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    table = {"apt-get": None, "dnf": "/usr/bin/dnf", "pacman": None}
    monkeypatch.setattr("shutil.which", lambda name: table.get(name))
    assert detect_pkg_manager() is PkgManagerKind.DNF


def test_pkg_manager_brew_on_darwin(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr("shutil.which", lambda name: "/opt/homebrew/bin/brew" if name == "brew" else None)
    assert detect_pkg_manager() is PkgManagerKind.BREW


def test_pkg_manager_none_on_windows_without_winget(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr("shutil.which", lambda name: None)
    assert detect_pkg_manager() is PkgManagerKind.NONE


def test_pkg_manager_never_picks_wrong_os(monkeypatch):
    # apt-get exists on PATH but the OS is darwin. We must NOT return
    # APT in that case — pkg managers are OS-gated.
    monkeypatch.setattr(sys, "platform", "darwin")
    table = {
        "apt-get": "/usr/bin/apt-get",  # exists on PATH
        "brew": None,
    }
    monkeypatch.setattr("shutil.which", lambda name: table.get(name))
    assert detect_pkg_manager() is PkgManagerKind.NONE


# --- PlatformInfo.can_auto_install -------------------------------------


def test_can_auto_install_linux_apt_with_sudo():
    p = PlatformInfo(
        os=OSKind.LINUX,
        pkg_manager=PkgManagerKind.APT,
        is_wsl=False,
        has_sudo=True,
    )
    assert p.can_auto_install is True


def test_can_auto_install_darwin_brew_with_sudo():
    p = PlatformInfo(
        os=OSKind.DARWIN,
        pkg_manager=PkgManagerKind.BREW,
        is_wsl=False,
        has_sudo=True,
    )
    assert p.can_auto_install is True


def test_cannot_auto_install_without_sudo():
    # `sudo` missing is the common case in a hardened container.
    p = PlatformInfo(
        os=OSKind.LINUX,
        pkg_manager=PkgManagerKind.APT,
        is_wsl=False,
        has_sudo=False,
    )
    assert p.can_auto_install is False


def test_cannot_auto_install_windows_even_with_winget():
    # Plan 2 v1 only hints at Windows; we never auto-install there.
    p = PlatformInfo(
        os=OSKind.WINDOWS,
        pkg_manager=PkgManagerKind.WINGET,
        is_wsl=False,
        has_sudo=True,
    )
    assert p.can_auto_install is False


def test_cannot_auto_install_unknown_os():
    p = PlatformInfo(
        os=OSKind.OTHER,
        pkg_manager=PkgManagerKind.NONE,
        is_wsl=False,
        has_sudo=True,
    )
    assert p.can_auto_install is False


# --- detect() one-shot integration -------------------------------------


def test_detect_returns_platform_info(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name if name in ("apt-get", "sudo") else None)
    info = detect()
    assert isinstance(info, PlatformInfo)
    assert info.os is OSKind.LINUX
    assert info.pkg_manager is PkgManagerKind.APT
    assert info.has_sudo is True
    # is_wsl is environment-dependent (this test runs in WSL2); we only
    # assert it is a bool — the WSL flag has its own dedicated tests.
    assert isinstance(info.is_wsl, bool)
