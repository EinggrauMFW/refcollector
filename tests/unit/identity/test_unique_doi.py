"""D-48 requirement 7: prove DOI uniqueness without changing legacy parsing."""

import pytest

from refcollector.identity import identifiers


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (None, None),
        ("No identifier", None),
        ("10.1234/ONE.", "10.1234/one"),
        ("doi:10.1234/one https://doi.org/10.1234/ONE", "10.1234/one"),
        ("10.1234/one doi:10.1234/two", None),
        ("doi:10.1234/one doi:10.1234/two", None),
        ("https://doi.org/10.1234/one https://doi.org/10.1234/two", None),
        ("doi: 10 . 1234 / one", "10.1234/one"),
        ("https://doi.org/10.1234%2Fone", "10.1234/one"),
        ("doi:10.1234/a(b).", "10.1234/a(b)"),
        ("doi:10.1234/a(b) 10.1234/a(b)", "10.1234/a(b)"),
        ("doi:10.1234/one;doi:10.1234/two", None),
        ("Vol 10. 1234/5678", None),
        ("doi:10.1234/long-\npart", "10.1234/long-part"),
    ],
)
def test_req7_unique_doi_all_syntactic_forms(text, expected):
    assert hasattr(identifiers, "unique_doi"), "D-48 unique_doi is not implemented"
    assert identifiers.unique_doi(text) == expected


def test_req7_legacy_first_match_parser_stays_unchanged():
    text = "10.1234/one doi:10.1234/two"
    assert identifiers.normalize_doi(text) == "10.1234/two"
    assert identifiers.read_identifiers(text)["doi"] == "10.1234/two"
