"""Extraction-only check over a folder of PDFs (no network).
Usage: python tests/batch_extract.py <folder-with-pdfs> [out_dir]"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import refcollector as rc  # noqa: E402

root = Path(sys.argv[1])
out = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(__file__).parent / "out"
out.mkdir(exist_ok=True, parents=True)

for pdf in sorted(root.glob("*.pdf")):
    try:
        text = rc.pdf_text(pdf)
        refs = rc.split_references(text)
    except SystemExit as e:
        print(f"{pdf.name[:40]:40} FAIL: {e}")
        continue
    except Exception as e:  # noqa: BLE001
        print(f"{pdf.name[:40]:40} ERROR {type(e).__name__}: {e}")
        continue
    with_doi = sum(1 for r in refs if rc.find_doi(r))
    lens = sorted(len(r) for r in refs)
    print(f"{pdf.name[:40]:40} refs={len(refs):4} with_doi={with_doi:4} "
          f"len min/med/max={lens[0]}/{lens[len(lens)//2]}/{lens[-1]}")
    (out / f"{pdf.stem}.refs.txt").write_text("\n\n".join(refs), encoding="utf-8")
