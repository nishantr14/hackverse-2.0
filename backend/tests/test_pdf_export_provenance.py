"""
A printed page that names people must carry what those names rest on.

WHY A BACKEND TEST GUARDS A FRONTEND FILE

The PDF export is the only artifact this product makes that leaves the room.
Everything on screen sits next to its own provenance and a reader who wants
the caveat can scroll to it; a page that has been printed, emailed or
photographed is read by someone who cannot. So the export carries a rule the
screen does not need:

    if the scenario was run in NAMED mode, the document states that the
    profiles are modelled rather than submitted, and states who could not be
    named at all — on the same pages as the names.

The frontend has no test runner and adding a dependency for this is not this
file's call, so the guard is static and lives here with the other checks of
that kind. It asserts SHAPE, not rendered output: that the export reaches for
`dataBasis`, `privacyBasis` and `anonymousCapacity` at all, and that the
footer's claim about naming is decided per mode rather than nailed to one
answer. Those are the two ways this has actually gone wrong.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
EXPORT = REPO_ROOT / "frontend" / "src" / "lib" / "exportPdf.ts"
SIMULATOR_VIEW = REPO_ROOT / "frontend" / "src" / "screens" / "SimulatorView.tsx"


def _strip_comments(text_: str) -> str:
    """This file documents the rule it enforces, at length, in prose."""
    text_ = re.sub(r"/\*.*?\*/", "", text_, flags=re.DOTALL)
    return re.sub(r"//[^\n]*", "", text_)


def _export_source() -> str:
    return _strip_comments(EXPORT.read_text(encoding="utf-8"))


def test_a_named_export_prints_the_synthetic_data_label():
    """Rule 1, first half. Names without provenance is the worst artifact
    this product could produce, and it is the one that travels."""
    source = _export_source()
    assert "dataBasis" in source, (
        "the export names people but never reads dataBasis — a printed page "
        "would present modelled profiles as if somebody had submitted them"
    )


def test_a_named_export_prints_the_consent_gate():
    """Rule 1, second half.

    `anonymousCapacity` is what makes the gate visible instead of looking
    like a short candidate list: without it, four names read as everyone
    considered rather than as everyone who consented to be named.
    """
    source = _export_source()
    for field in ("anonymousCapacity", "privacyBasis"):
        assert field in source, f"the named export drops {field}"


def test_the_excluded_are_printed_as_excluded_not_omitted():
    source = _export_source()
    assert "excluded" in source, (
        "somebody ruled out on a stated boundary must appear as ruled out; "
        "dropping them silently makes an exclusion look like a low score"
    )


def test_the_no_individual_is_named_claim_is_decided_per_mode():
    """The regression this test exists for.

    The footer read "No individual is named or scored anywhere" on every
    page of every export. It was true when written and false the day named
    mode shipped — and it survived the first fix, which made the footer
    mode-aware but still called it with a hardcoded `false` on page one, so
    a two-page document denied on page 1 what it printed on page 2.
    """
    source = _export_source()
    claim = "No individual is named"
    assert claim in source, "the capacity-mode claim is gone; this test needs updating"
    assert "footer(false)" not in source, (
        "page one's footer is hardcoded to the capacity-mode claim. In a named "
        "export that page denies what the next page prints."
    )
    # The claim must sit on a branch, not stand alone.
    assert re.search(r"named\s*\?[^;]*" + re.escape(claim), source, re.DOTALL), (
        f'"{claim}" is not on the false branch of a mode test'
    )


def test_the_screen_hands_the_recommendations_to_the_export():
    """A guard on the caller, because the export cannot print what it is not
    given — and the failure is silent: a named run exports a clean-looking
    capacity page with the recommendations quietly dropped."""
    source = _strip_comments(SIMULATOR_VIEW.read_text(encoding="utf-8"))
    call = re.search(r"exportScenarioPdf\(\{(.*?)\}\)", source, re.DOTALL)
    assert call, "exportScenarioPdf is no longer called with an options object"
    assert "workforce" in call.group(1), (
        "the Simulator runs named scenarios but exports without the workforce "
        "set, so the PDF silently loses every recommendation"
    )


def test_the_export_does_not_round_the_fit_terms_its_own_way():
    """The page asserts the terms sum to the score, so they have to.

    It printed each term at 1dp independently: 35 + 15.6 + 18.8 + 8 + 10 =
    87.4 against an 87% headline, one line above a sentence saying the
    contributions "sum to matchScore, so the arithmetic is the explanation".
    A document that disproves its own claim in the next paragraph is worse
    than one that makes no claim. `fitPoints` is largest-remainder and is
    shared with the card, so the page matches the screen digit for digit.
    """
    source = _export_source()
    assert "fitPoints(" in source, "the export rounds the fit terms itself again"
    assert "rec.contributions[" not in source, (
        "the export reads raw contributions instead of the shared rounding, "
        "which is how the 1dp drift got in"
    )
