"""Render every page of a PDF into contact sheets (N pages per sheet) for visual layout review."""
import sys
from pathlib import Path
import pymupdf
from PIL import Image

pdf, out_dir, per = sys.argv[1], Path(sys.argv[2]), int(sys.argv[3]) if len(sys.argv) > 3 else 12
out_dir.mkdir(parents=True, exist_ok=True)
doc = pymupdf.open(pdf)
cols = 4
for start in range(0, doc.page_count, per):
    thumbs = []
    for i in range(start, min(start + per, doc.page_count)):
        pix = doc[i].get_pixmap(dpi=40)
        thumbs.append(Image.frombytes("RGB", (pix.width, pix.height), pix.samples))
    w, h = max(t.width for t in thumbs), max(t.height for t in thumbs)
    rows = (len(thumbs) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * w, rows * h), "white")
    for k, t in enumerate(thumbs):
        sheet.paste(t, ((k % cols) * w, (k // cols) * h))
    sheet.save(out_dir / f"sheet_{start + 1:03d}.png")
print("sheets:", sorted(p.name for p in out_dir.glob("sheet_*.png")))
