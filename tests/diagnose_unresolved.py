"""For unresolved journal-looking entries, find out WHY they failed.
Categories:
  rejected   a good candidate exists in Crossref (top 10) or OpenAlex but is_match() said no
  not_found  no candidate with a similar title in any source
  garbled    the entry itself is too short / fragmentary to identify
Usage: python tests/diagnose_unresolved.py <email>"""
import glob
import os
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import refcollector as rc  # noqa: E402

res = rc.Resolver(sys.argv[1])
JOURNAL = re.compile(
    r"\b(?:Journal|Renewable|Energy|Ocean|Engineering|Review|Transactions|Letters|Science|Fluid|Coastal|Applied)\b"
    r".*?(?:\d+\s*[(:.]\s*\d+|\(\d{4}\)|\d{4})", re.I)
entries = []
for f in sorted(glob.glob(str(Path(__file__).parent / "out" / "*.unresolved.txt"))):
    for e in open(f, encoding="utf-8").read().split("\n\n"):
        if e and JOURNAL.search(e) and re.search(r"\bpp?\.?\s*\d+|\d+\s*[:(]\s*\d+", e):
            entries.append((os.path.basename(f)[:9], e))

print(len(entries), "unresolved entries that look like journal articles\n")
cats = Counter()
for name, e in entries:
    if len(e) < 60:
        cats["garbled"] += 1
        print(f"[garbled ] {name} {e[:100]}")
        continue
    q = rc.clean_ref(e)
    cands = res._crossref_candidates(q, rows=10)
    title = rc.guess_title(e)
    oa = res._openalex_candidate_dois(title, rows=5) if title else []
    best = max(cands, key=lambda it: rc.match_score((it.get("title") or [""])[0], e), default=None)
    score = rc.match_score((best.get("title") or [""])[0], e) if best else 0
    oa_score = max((rc.match_score(t, e) for t, _d, _y in oa), default=0)
    if max(score, oa_score) >= 0.75:
        cats["rejected"] += 1
        why = ""
        if best and score >= 0.75:
            yrs = re.findall(r"\b(?:19|20)\d{2}\b", e)
            why = f"crossref '{(best.get('title') or [''])[0][:60]}' year {rc.item_year(best)} vs {yrs}"
        else:
            why = "openalex only"
        print(f"[rejected] {name} {e[:90]}\n            {why}")
    else:
        cats["not_found"] += 1
        print(f"[notfound] {name} {e[:110]}  (best score cr={score:.2f} oa={oa_score:.2f})")
print("\n", dict(cats))
