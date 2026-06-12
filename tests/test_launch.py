import subprocess

import kagura_engineer._launch as L


def test_launch_argv_passthrough_on_posix(monkeypatch):
    monkeypatch.setattr(L.sys, "platform", "linux")
    assert L.launch_argv(["claude", "--version"]) == ["claude", "--version"]


def test_launch_argv_wraps_cmd_shim_on_windows(monkeypatch):
    monkeypatch.setattr(L.sys, "platform", "win32")
    monkeypatch.setattr(L.shutil, "which", lambda e: r"C:\npm\claude.cmd")
    monkeypatch.setenv("COMSPEC", r"C:\Windows\System32\cmd.exe")
    assert L.launch_argv(["claude", "--version"]) == [
        r"C:\Windows\System32\cmd.exe", "/c", r"C:\npm\claude.cmd", "--version",
    ]


def test_launch_argv_wraps_bat_shim_on_windows(monkeypatch):
    monkeypatch.setattr(L.sys, "platform", "win32")
    monkeypatch.setattr(L.shutil, "which", lambda e: r"C:\tools\foo.BAT")
    monkeypatch.delenv("COMSPEC", raising=False)
    # COMSPEC missing → default cmd.exe
    assert L.launch_argv(["foo"]) == ["cmd.exe", "/c", r"C:\tools\foo.BAT"]


def test_launch_argv_empty_comspec_falls_back_to_cmd_exe(monkeypatch):
    # A Docker-on-Windows image can set COMSPEC="" — `or` must treat it as unset.
    monkeypatch.setattr(L.sys, "platform", "win32")
    monkeypatch.setattr(L.shutil, "which", lambda e: r"C:\npm\claude.cmd")
    monkeypatch.setenv("COMSPEC", "")
    assert L.launch_argv(["claude"]) == ["cmd.exe", "/c", r"C:\npm\claude.cmd"]


def test_launch_argv_passes_through_real_exe_on_windows(monkeypatch):
    monkeypatch.setattr(L.sys, "platform", "win32")
    monkeypatch.setattr(L.shutil, "which", lambda e: r"C:\Program Files\Git\bin\git.exe")
    assert L.launch_argv(["git", "rev-parse"]) == ["git", "rev-parse"]


def test_launch_argv_passes_through_when_unresolved(monkeypatch):
    monkeypatch.setattr(L.sys, "platform", "win32")
    monkeypatch.setattr(L.shutil, "which", lambda e: None)
    assert L.launch_argv(["nope", "-x"]) == ["nope", "-x"]


def test_launch_argv_empty_argv(monkeypatch):
    monkeypatch.setattr(L.sys, "platform", "win32")
    assert L.launch_argv([]) == []


def test_run_text_forces_utf8_replace_and_launch_argv(monkeypatch):
    seen = {}

    def _fake_run(argv, **kwargs):
        seen["argv"] = argv
        seen["kwargs"] = kwargs
        return subprocess.CompletedProcess(argv, 0, "ok", "")

    monkeypatch.setattr(L.sys, "platform", "win32")
    monkeypatch.setattr(L.shutil, "which", lambda e: r"C:\npm\gh.cmd")
    monkeypatch.setenv("COMSPEC", r"C:\Windows\System32\cmd.exe")
    monkeypatch.setattr(L.subprocess, "run", _fake_run)

    L.run_text(["gh", "auth", "status"], capture_output=True, timeout=5)

    assert seen["argv"][0].endswith("cmd.exe")
    assert seen["argv"][1:3] == ["/c", r"C:\npm\gh.cmd"]
    assert seen["kwargs"]["encoding"] == "utf-8"
    assert seen["kwargs"]["errors"] == "replace"
    assert seen["kwargs"]["capture_output"] is True


def test_run_text_caller_can_override_errors(monkeypatch):
    seen = {}
    monkeypatch.setattr(L.sys, "platform", "linux")
    monkeypatch.setattr(
        L.subprocess, "run",
        lambda argv, **kw: seen.update(kw) or subprocess.CompletedProcess(argv, 0, "", ""),
    )
    L.run_text(["echo"], errors="strict")
    assert seen["errors"] == "strict"
    assert seen["encoding"] == "utf-8"


def test_run_text_caller_can_override_encoding(monkeypatch):
    seen = {}
    monkeypatch.setattr(L.sys, "platform", "linux")
    monkeypatch.setattr(
        L.subprocess, "run",
        lambda argv, **kw: seen.update(kw) or subprocess.CompletedProcess(argv, 0, "", ""),
    )
    L.run_text(["echo"], encoding="latin-1")
    assert seen["encoding"] == "latin-1"  # caller's setdefault wins
    assert seen["errors"] == "replace"  # the other default still applies
