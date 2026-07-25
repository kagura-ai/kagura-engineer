"""Markdown link correctness (issue #101).

`README.md` is the PyPI long description. PyPI renders it standalone: there is
no repository root for a relative path to resolve against, its HTML sanitizer
strips heading `id` attributes so intra-page anchors do not resolve, and `docs/`
is excluded from the sdist entirely — so a relative link there is broken twice
over.

The project rule is broader than that one file: **every markdown link is an
absolute `https://` URL.** Absolute links resolve identically from a checkout,
from github.com, from the PyPI project page, and from anything that mirrors a
file without its directory structure. Nothing but a test keeps that true.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_README = _REPO_ROOT / "README.md"
_BLOB = "https://github.com/kagura-ai/kagura-engineer/blob/main/"

# Inline markdown links: `](target)`. Image sources use the same syntax, so
# badges are covered by the same rule.
_LINK_RE = re.compile(r"\]\(([^)\s]+)")

_MARKDOWN_FILES = [_README, *sorted(_REPO_ROOT.glob("docs/**/*.md"))]


def _targets(path: Path) -> list[str]:
    return _LINK_RE.findall(path.read_text(encoding="utf-8"))


def _rel(path: Path) -> str:
    return str(path.relative_to(_REPO_ROOT))


# Flatten to (file, target) so a failure names the offending file *and* link.
_ALL_LINKS = [(p, t) for p in _MARKDOWN_FILES for t in _targets(p)]
_BLOB_LINKS = [(p, t) for p, t in _ALL_LINKS if t.startswith(_BLOB)]


def test_the_link_scan_found_something():
    # Guards every assertion below against silently passing on a parse failure
    # or a bad glob.
    assert len(_MARKDOWN_FILES) > 5
    assert len(_ALL_LINKS) > 30


@pytest.mark.parametrize(
    "path,target", _ALL_LINKS, ids=[f"{_rel(p)}->{t}" for p, t in _ALL_LINKS]
)
def test_every_markdown_link_is_absolute(path: Path, target: str):
    assert target.startswith(("https://", "mailto:")), (
        f"{_rel(path)} links to {target!r}, which is relative or an intra-page "
        f"anchor. Use an absolute URL (repository files: {_BLOB}<path>)."
    )


@pytest.mark.parametrize(
    "path,target", _BLOB_LINKS, ids=[f"{_rel(p)}->{t}" for p, t in _BLOB_LINKS]
)
def test_every_repo_file_link_points_at_a_real_file(path: Path, target: str):
    # An absolute URL that 404s is no better than a relative one, and a bulk
    # relative→absolute rewrite is exactly where a link silently changes target
    # (a `../README.md` meaning docs/README.md is easy to flatten into the root
    # one). Resolve blob URLs against the working tree so that fails here.
    rel = target[len(_BLOB):].split("#", 1)[0]
    assert (_REPO_ROOT / rel).exists(), (
        f"{_rel(path)} links to missing file: {rel}"
    )


def test_historical_records_link_to_the_docs_map_not_the_root_readme():
    # The boilerplate header of every dated record offers both "current README"
    # and "document map"; they must remain distinct targets. (pathlib.glob has
    # no brace expansion, so enumerate the roots explicitly — a `{a,b}` pattern
    # would silently match nothing and pass vacuously.)
    checked = 0
    for path in _MARKDOWN_FILES:
        body = path.read_text(encoding="utf-8")
        if "[document map]" not in body:
            continue
        checked += 1
        assert f"[document map]({_BLOB}docs/README.md)" in body, _rel(path)
    assert checked >= 10, f"expected the boilerplate in many records, saw {checked}"


def test_readme_links_to_the_docs_it_relocated_content_into():
    # The slim-down is only safe if the moved reference material is reachable.
    body = _README.read_text(encoding="utf-8")
    for page in ("commands.md", "configuration.md", "headless-permissions.md",
                 "development.md"):
        assert f"{_BLOB}docs/{page}" in body, f"README does not link docs/{page}"


def test_readme_stays_short_enough_to_work_as_a_landing_page():
    # Issue #101 capped this at 200 lines. The bound is the point of the change:
    # without it the README silently re-accretes reference material.
    lines = _README.read_text(encoding="utf-8").splitlines()
    assert len(lines) <= 200, f"README is {len(lines)} lines; move detail into docs/"
