import tomllib
from pathlib import Path

import kagura_engineer


def test_version_exposed():
    assert kagura_engineer.__version__ == "0.3.0"


def test_kagura_brain_pinned_to_0_2_plus():
    # Backend selection (codex adapter + doctor.check) needs kagura-brain >= 0.2.0;
    # the old `<0.2` pin also excluded brain #11's CLAUDE_* security scrub.
    data = tomllib.loads(Path("pyproject.toml").read_text())
    deps = data["project"]["dependencies"]
    brain = [d for d in deps if d.replace(" ", "").startswith("kagura-brain")]
    assert brain == ["kagura-brain>=0.2.0,<0.3"], brain
