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
