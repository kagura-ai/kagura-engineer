import tomllib
from pathlib import Path

import kagura_engineer


def test_version_exposed():
    assert kagura_engineer.__version__ == "0.3.2"


def test_kagura_brain_pinned_to_0_4_plus():
    # `select()`/`BrainHandle`/`BRAIN_API_KEY_ENV` (#63) need kagura-brain >= 0.4.0;
    # older pins lack the selector API (and `<0.2` also excluded brain #11's
    # CLAUDE_* security scrub).
    data = tomllib.loads(Path("pyproject.toml").read_text())
    deps = data["project"]["dependencies"]
    brain = [d for d in deps if d.replace(" ", "").startswith("kagura-brain")]
    assert brain == ["kagura-brain>=0.4.0,<0.5"], brain
