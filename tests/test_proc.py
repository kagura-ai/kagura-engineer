from kagura_engineer.proc import as_text


def test_decodes_bytes():
    assert as_text(b"partial\n") == "partial\n"


def test_passes_str_through():
    assert as_text("hi") == "hi"


def test_none_and_empty_become_empty_string():
    assert as_text(None) == ""
    assert as_text(b"") == ""
    assert as_text("") == ""


def test_replaces_undecodable_bytes():
    # invalid UTF-8 must not raise
    assert as_text(b"\xff\xfe") != ""


from kagura_engineer.proc import mcp_args


def test_mcp_args_empty_when_unset():
    assert mcp_args(None) == []
    assert mcp_args("") == []


def test_mcp_args_builds_additive_attach_flags():
    a = mcp_args("/tmp/mcp.json")
    assert a[:2] == ["--mcp-config", "/tmp/mcp.json"]
    assert "--allowedTools" in a
    assert "mcp__kagura-memory__recall" in a and "mcp__kagura-memory__remember" in a
    assert "--strict-mcp-config" not in a  # additive merge, never replaces servers
