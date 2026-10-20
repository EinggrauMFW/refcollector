"""Individually named v0.1 matching cases; no import of the legacy executable."""

from refcollector.identity.normalize import match_score
from refcollector.identity.policy_v1 import assess


def item(title, authors=(), year=2010, typ="journal-article"):
    return {
        "title": [title],
        "author": [{"family": a, "given": "X"} for a in authors],
        "issued": {"date-parts": [[year]]},
        "type": typ,
        "DOI": "10.1/x",
    }


def test_hyphen_joined_in_reference():
    assert match_score("Pretrained language models", "A. Smith. Pre-trained language models. 2020.") >= 0.99


def test_hyphen_joined_in_title():
    assert match_score("Pre-trained language models", "A. Smith. Pretrained language models. 2020.") >= 0.99


def test_hyphen_split():
    assert match_score("Wave-power absorption", "Wave power absorption by arrays") >= 0.99


def test_british_spelling():
    assert match_score("Optimization of wave energy", "Optimisation of wave energy devices") >= 0.99


def test_our_spelling():
    assert match_score("Animal behavior", "Animal behaviour study") >= 0.99


def test_plural():
    assert match_score("Wave energy converters", "Wave energy converter arrays") >= 0.99


def test_ampersand():
    assert match_score("Operation & maintenance", "Operation and maintenance costs") >= 0.99


def test_ligature_nfkc():
    assert match_score("Effects of noise", "Eﬀects of noise on fish") >= 0.99


def test_unrelated_title():
    assert match_score("Deep learning for vision", "Wave energy converters in Chile") < 0.3


def test_wrong_year_tolerated_with_flag():
    result = assess(
        item(
            "Application of generalized additive models to butterfly transect count data",
            ["Rothery", "Roy"],
            2001,
        ),
        "P. Rothery and D. B. Roy. Application of generalized additive models to butterfly "
        "transect count data. J. Appl. Stat., 2010.",
    )
    assert result["accepted"] and any("year differs" in f for f in result["flags"])


def test_garbled_author_tolerated_with_flag():
    result = assess(
        item("Wave energy utilization: A review of the technologies", ["Falcao"], 2010),
        "F. d. O. Antonio. Wave energy utilization: A review of the technologies. "
        "Renewable and Sustainable Energy Reviews 14 (2010).",
    )
    assert result["accepted"] and any("author names" in f for f in result["flags"])


def test_clean_match_without_flags():
    result = assess(
        item("Random Forests", ["Breiman"], 2001),
        "L. Breiman. Random Forests. Machine Learning, 45:5-32, 2001.",
    )
    assert result["accepted"] and not result["flags"]
    assert result["author_overlap"] == 1.0 and result["year_delta"] == 0


def test_component_appendix_rejected():
    result = assess(
        item(
            "Appendix 3: Directive 2001/20/EC of the European Parliament and of the Council of 4 April 2001",
            (),
            2009,
            "component",
        ),
        "European Council, Council Directive 2009/28/EC of the European Parliament and of "
        "the Council of 23 April 2009.",
    )
    assert not result["accepted"]


def test_short_authorless_wrong_year_rejected():
    assert not assess(
        item("Prime Minister, Cabinet and government", (), 2024, "book"),
        "Australian Government Department of Prime Minister and Cabinet. Emission Reduction Target. 2016.",
    )["accepted"]


def test_one_word_wrong_author_rejected():
    assert not assess(
        item("Review", ["Nobody"], 2019),
        "Kurniawan, T. A. (2019). Remote Sensing and GIS - A Review. Resources, 8(149).",
    )["accepted"]


def test_one_of_four_authors_generic_title_rejected():
    assert not assess(
        item("Handling data review", ["Adams", "Baker", "Clark", "Davis"], 2006),
        "Adams, Q. (2006). Handling data review. Journal of Things.",
    )["accepted"]


def test_different_work_rejected():
    assert not assess(
        item("Some aspects of the French flexible bag wave-energy device", ["French"], 1985),
        "M. J. French, The search for low cost wave energy and the flexible bag device, 1979.",
    )["accepted"]
