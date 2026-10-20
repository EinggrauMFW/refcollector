"""Offline tests for matching, normalisation and export helpers.  Run: python tests/test_matching.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import refcollector as rc  # noqa: E402


def item(title, authors=(), year=2010, typ="journal-article"):
    return {"title": [title], "author": [{"family": a, "given": "X"} for a in authors],
            "issued": {"date-parts": [[year]]}, "type": typ, "DOI": "10.1/x"}


def check(name, cond):
    print(("ok   " if cond else "FAIL ") + name)
    return cond


ok = True

# --- normalisation ---
ok &= check("hyphen joined in reference", rc.match_score("Pretrained language models", "A. Smith. Pre-trained language models. 2020.") >= 0.99)
ok &= check("hyphen joined in title", rc.match_score("Pre-trained language models", "A. Smith. Pretrained language models. 2020.") >= 0.99)
ok &= check("hyphen split", rc.match_score("Wave-power absorption", "Wave power absorption by arrays") >= 0.99)
ok &= check("British spelling", rc.match_score("Optimization of wave energy", "Optimisation of wave energy devices") >= 0.99)
ok &= check("-our spelling", rc.match_score("Animal behavior", "Animal behaviour study") >= 0.99)
ok &= check("plural", rc.match_score("Wave energy converters", "Wave energy converter arrays") >= 0.99)
ok &= check("ampersand", rc.match_score("Operation & maintenance", "Operation and maintenance costs") >= 0.99)
ok &= check("ligature via NFKC", rc.match_score("Effects of noise", "Eﬀects of noise on fish") >= 0.99)
ok &= check("unrelated title scores low", rc.match_score("Deep learning for vision", "Wave energy converters in Chile") < 0.3)

# --- assess: accepted, with the right flags ---
a = rc.assess(item("Application of generalized additive models to butterfly transect count data", ["Rothery", "Roy"], 2001),
              "P. Rothery and D. B. Roy. Application of generalized additive models to butterfly transect count data. J. Appl. Stat., 2010.")
ok &= check("wrong year tolerated with flag", a["accepted"] and any("year differs" in f for f in a["flags"]))

a = rc.assess(item("Wave energy utilization: A review of the technologies", ["Falcao"], 2010),
              "F. d. O. Antonio. Wave energy utilization: A review of the technologies. Renewable and Sustainable Energy Reviews 14 (2010).")
ok &= check("garbled author accepted with flag", a["accepted"] and any("author names" in f for f in a["flags"]))

a = rc.assess(item("Random Forests", ["Breiman"], 2001), "L. Breiman. Random Forests. Machine Learning, 45:5-32, 2001.")
ok &= check("clean match has no flags", a["accepted"] and not a["flags"] and a["author_overlap"] == 1.0 and a["year_delta"] == 0)

# --- assess: rejected ---
a = rc.assess(item("Appendix 3: Directive 2001/20/EC of the European Parliament and of the Council of 4 April 2001", (), 2009, "component"),
              "European Council, Council Directive 2009/28/EC of the European Parliament and of the Council of 23 April 2009.")
ok &= check("component / appendix fragment rejected", not a["accepted"])

a = rc.assess(item("Prime Minister, Cabinet and government", (), 2024, "book"),
              "Australian Government Department of Prime Minister and Cabinet. Emission Reduction Target. 2016.")
ok &= check("short authorless title with wrong year rejected", not a["accepted"])

a = rc.assess(item("Review", ["Nobody"], 2019), "Kurniawan, T. A. (2019). Remote Sensing and GIS - A Review. Resources, 8(149).")
ok &= check("one-word title rejected", not a["accepted"])

a = rc.assess(item("Handling data review", ["Adams", "Baker", "Clark", "Davis"], 2006),
              "Adams, Q. (2006). Handling data review. Journal of Things.")
ok &= check("1 of 4 authors on a generic title rejected", not a["accepted"])

a = rc.assess(item("Some aspects of the French flexible bag wave-energy device", ["French"], 1985),
              "M. J. French, The search for low cost wave energy and the flexible bag device, 1979.")
ok &= check("different work (title low) rejected", not a["accepted"])

# --- exports ---
metas = [rc.normalize(item("Random Forests", ["Breiman"], 2001)) for _ in range(3)]
keys = rc.unique_keys(metas)
ok &= check("unique keys", len(set(keys)) == 3 and keys[0] == "Breiman2001Random")
csl = rc.to_csl(rc.normalize({**item("Random Forests", ["Breiman"], 2001), "container-title": ["Machine Learning"]}), "k")
ok &= check("csl fields", csl["type"] == "article-journal" and csl["author"][0]["family"] == "Breiman"
            and csl["issued"] == {"date-parts": [[2001]]} and csl["container-title"] == "Machine Learning")
bib = rc.to_bibtex(rc.normalize({**item("Conf paper", ["Lee"], 2015, "proceedings-article"), "container-title": ["EWTEC"]}), "Lee2015Conf")
ok &= check("inproceedings uses booktitle", bib.startswith("@inproceedings{Lee2015Conf") and "booktitle = {EWTEC}" in bib)

print("\nALL PASSED" if ok else "\nSOME FAILED")
sys.exit(0 if ok else 1)
