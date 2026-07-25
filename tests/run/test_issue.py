"""Issue #92 — harness-side issue acquisition and task-echo verification."""
import subprocess
from types import SimpleNamespace

from kagura_engineer.run import issue as issue_mod
from kagura_engineer.run.issue import (
    IssueBrief,
    fetch_issue,
    significant_tokens,
    task_echo_matches,
)


def _fake_run(stdout="", returncode=0, exc=None, capture=None):
    def _run(argv, **kwargs):
        if capture is not None:
            capture.append((argv, kwargs))
        if exc is not None:
            raise exc
        return SimpleNamespace(returncode=returncode, stdout=stdout, stderr="")
    return _run


# --------------------------------------------------------------------------
# fetch_issue
# --------------------------------------------------------------------------

def test_fetch_issue_returns_title_and_body(monkeypatch, tmp_path):
    payload = '{"number": 42, "title": "Add a chess clock", "body": "Countdown per side."}'
    monkeypatch.setattr(issue_mod, "run_text", _fake_run(stdout=payload))
    brief = fetch_issue(42, tmp_path)
    assert brief == IssueBrief(number=42, title="Add a chess clock", body="Countdown per side.")


def test_fetch_issue_asks_gh_for_the_right_fields_in_the_worktree(monkeypatch, tmp_path):
    capture: list[tuple] = []
    payload = '{"number": 7, "title": "t", "body": "b"}'
    monkeypatch.setattr(issue_mod, "run_text", _fake_run(stdout=payload, capture=capture))
    fetch_issue(7, tmp_path)
    argv, kwargs = capture[0]
    assert argv[:4] == ["gh", "issue", "view", "7"]
    assert "number,title,body" in argv
    assert kwargs["cwd"] == str(tmp_path)


def test_fetch_issue_returns_none_when_gh_fails(monkeypatch, tmp_path):
    monkeypatch.setattr(issue_mod, "run_text", _fake_run(returncode=1))
    assert fetch_issue(42, tmp_path) is None


def test_fetch_issue_returns_none_when_gh_is_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(issue_mod, "run_text", _fake_run(exc=OSError("no gh")))
    assert fetch_issue(42, tmp_path) is None


def test_fetch_issue_returns_none_on_timeout(monkeypatch, tmp_path):
    exc = subprocess.TimeoutExpired(cmd="gh", timeout=1)
    monkeypatch.setattr(issue_mod, "run_text", _fake_run(exc=exc))
    assert fetch_issue(42, tmp_path) is None


def test_fetch_issue_returns_none_on_unparseable_json(monkeypatch, tmp_path):
    monkeypatch.setattr(issue_mod, "run_text", _fake_run(stdout="not json"))
    assert fetch_issue(42, tmp_path) is None


def test_fetch_issue_returns_none_on_non_object_json(monkeypatch, tmp_path):
    monkeypatch.setattr(issue_mod, "run_text", _fake_run(stdout="[1, 2]"))
    assert fetch_issue(42, tmp_path) is None


def test_fetch_issue_returns_none_when_title_is_absent(monkeypatch, tmp_path):
    # A brief with no title cannot anchor the prompt or the echo gate.
    monkeypatch.setattr(issue_mod, "run_text", _fake_run(stdout='{"number": 4, "body": "b"}'))
    assert fetch_issue(4, tmp_path) is None


def test_fetch_issue_tolerates_a_null_body(monkeypatch, tmp_path):
    # `gh` emits `"body": null` for an issue opened with no description.
    monkeypatch.setattr(
        issue_mod, "run_text", _fake_run(stdout='{"number": 4, "title": "t", "body": null}')
    )
    brief = fetch_issue(4, tmp_path)
    assert brief is not None and brief.body == ""


def test_fetch_issue_truncates_an_enormous_body(monkeypatch, tmp_path):
    import json
    huge = "x" * 50_000
    payload = json.dumps({"number": 1, "title": "t", "body": huge})
    monkeypatch.setattr(issue_mod, "run_text", _fake_run(stdout=payload))
    brief = fetch_issue(1, tmp_path)
    assert brief is not None
    assert len(brief.body) < len(huge)
    assert "truncated" in brief.body.lower()


def test_fetch_issue_falls_back_to_the_requested_number(monkeypatch, tmp_path):
    # A nonconforming/absent `number` must not desync the brief from the issue
    # the run was actually launched for.
    monkeypatch.setattr(issue_mod, "run_text", _fake_run(stdout='{"title": "t", "body": "b"}'))
    brief = fetch_issue(99, tmp_path)
    assert brief is not None and brief.number == 99


# --------------------------------------------------------------------------
# as_prompt_block
# --------------------------------------------------------------------------

def test_prompt_block_carries_title_and_body_verbatim():
    brief = IssueBrief(number=42, title="Add a chess clock", body="Countdown per side.")
    block = brief.as_prompt_block()
    assert "Add a chess clock" in block
    assert "Countdown per side." in block
    assert "#42" in block


def test_prompt_block_is_delimited_so_the_body_cannot_bleed_into_instructions():
    brief = IssueBrief(number=42, title="t", body="b")
    block = brief.as_prompt_block()
    assert "BEGIN ISSUE" in block and "END ISSUE" in block


# --------------------------------------------------------------------------
# significant_tokens
# --------------------------------------------------------------------------

def test_significant_tokens_drops_stopwords_and_short_words():
    assert significant_tokens("the chess clock is a Toggle") == {"chess", "clock", "toggle"}


def test_significant_tokens_splits_on_punctuation_and_casing():
    assert significant_tokens("run: OSS-brain adaptation!") == {"run", "oss", "brain", "adaptation"}


def test_significant_tokens_handles_cjk_via_bigrams():
    # A Japanese title has no ASCII words to match on; character bigrams give the
    # gate something to compare so a CJK repo is not silently ungated.
    toks = significant_tokens("高スコア記録")
    assert "高ス" in toks and "スコ" in toks


def test_significant_tokens_of_empty_text_is_empty():
    assert significant_tokens("") == set()


# --------------------------------------------------------------------------
# task_echo_matches — the #92 substitution detector
# --------------------------------------------------------------------------

def _brief(title, body=""):
    return IssueBrief(number=1, title=title, body=body)


def test_echo_matching_the_issue_passes():
    brief = _brief("Add tetris high-score persistence")
    assert task_echo_matches("Persist the tetris high score across reloads", brief)


def test_echo_of_a_substituted_task_fails():
    # The dogfooded #92 failure: assigned "toggle persistence", implemented
    # "move-history display".
    brief = _brief("Add sound-toggle persistence to tetris")
    assert not task_echo_matches("Implement a chess move-history display panel", brief)


def test_echo_of_a_different_game_fails():
    brief = _brief("Add tetris high-score tracking")
    assert not task_echo_matches("Fix the chess AI resume after undo", brief)


def test_long_title_still_passes_on_a_partial_restatement():
    # Coverage is capped so a verbose title cannot make a faithful one-line
    # restatement impossible to pass.
    brief = _brief(
        "run: OSS-brain adaptation — local models complete the pipeline but "
        "implement the wrong task (phase contracts assume Claude)"
    )
    echo = "Inject the issue text into phase prompts and gate on a task echo"
    assert task_echo_matches(echo, brief)


def test_empty_echo_fails():
    assert not task_echo_matches("", _brief("Add a chess clock"))


def test_untokenizable_title_fails_open():
    # Nothing to judge against — the gate must not halt a run it cannot assess.
    assert task_echo_matches("anything at all", _brief("a b c"))


def test_cjk_echo_matches_a_cjk_title():
    brief = _brief("ループ内 /code-review の自律実行を repo.yaml で制御可能にする")
    assert task_echo_matches("repo.yaml で code-review の自律実行を制御可能にする", brief)


def test_cjk_echo_of_a_different_task_fails():
    brief = _brief("テトリスの高スコアを永続化する")
    assert not task_echo_matches("チェスの棋譜表示パネルを追加する", brief)
