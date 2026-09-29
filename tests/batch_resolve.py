"""Resolve every reference in every PDF of a folder and report stats + failures.
Usage: python tests/batch_resolve.py <pdf_folder> <email> [max_refs_per_pdf]

Writes tests/out/<pdf>.resolved.json and tests/out/<pdf>.unresolved.txt, prints a summary."""
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import refcollector as rc  # noqa: E402

root, email = Path(sys.argv[1]), sys.argv[2]
cap = int(sys.argv[3]) if len(sys.argv) > 3 else None
out = Path(__file__).parent / "out"
out.mkdir(exist_ok=True)
res = rc.Resolver(email)
total = Counter()

for pdf in sorted(root.glob("*.pdf")):
    try:
        refs = rc.split_references(rc.pdf_text(pdf))
    except SystemExit as e:
        print(f"{pdf.name[:38]:38} EXTRACTION FAIL: {e}", flush=True)
        continue
    refs = refs[:cap] if cap else refs
    rows, bad = [], []
    methods = Counter()
    for raw in refs:
        item, method = res.resolve(raw)
        if item is None:
            bad.append(raw)
            methods["unresolved"] += 1
            continue
        m = rc.normalize(item)
        methods[method] += 1
        if method.startswith("text"):
            methods["  confirmed" if len(m["sources"]) >= 2 else "  single-source"] += 1
        rows.append({"method": method, "raw": raw, "title": m["title"], "year": m["year"], "doi": m["doi"]})
    (out / f"{pdf.stem}.resolved.json").write_text(json.dumps(rows, indent=1, ensure_ascii=False), encoding="utf-8")
    (out / f"{pdf.stem}.unresolved.txt").write_text("\n\n".join(bad), encoding="utf-8")
    total.update(methods)
    print(f"{pdf.name[:38]:38} n={len(refs):4} " + " ".join(f"{k}={v}" for k, v in sorted(methods.items())), flush=True)

n = sum(v for k, v in total.items() if not k.startswith(" "))  # skip the indented sub-counters
print("\nTOTAL", n, dict(total), f"-> unresolved {total['unresolved']/max(n,1):.0%}")
