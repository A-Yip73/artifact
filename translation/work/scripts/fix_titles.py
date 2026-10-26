"""Step 4b: BabelDOC leaves the large chapter titles (h1) in Japanese; overlay the Chinese titles.

usage: fix_titles.py MONO.pdf DUAL.pdf OUT_MONO.pdf OUT_DUAL.pdf
"""
import json, sys
from pathlib import Path
import pymupdf

WORK = Path(__file__).resolve().parent.parent
mono_in, dual_in, mono_out, dual_out = sys.argv[1:5]
blocks = json.load(open(WORK / "blocks.json"))
tr = json.load(open(WORK / "translations.json"))
src = pymupdf.open(WORK / "source_decrypted.pdf")
FONT = str(WORK / "fonts" / "NotoSerifCJKsc-Bold.otf")


def title_spans(page):
    """Rect and size of the big Japanese title glyphs (Ryumin Heavy, after the chapter number)."""
    rect, size = None, 0
    for b in page.get_text("dict")["blocks"]:
        for l in b.get("lines", []):
            for s in l["spans"]:
                if "Heavy" in s["font"] and s["size"] > 18 and s["bbox"][1] < 120 and s["text"].strip(" 　｜|"):
                    r = pymupdf.Rect(s["bbox"])
                    rect = r if rect is None else rect | r
                    size = max(size, s["size"])
    return rect, size


jobs = []
for b in blocks:
    if b["type"] != "h1" or b["id"] not in tr:
        continue
    zh = tr[b["id"]].split("｜")[-1].strip()
    rect, size = title_spans(src[b["page"] - 1])
    if rect is not None:
        jobs.append((b["page"] - 1, rect, size, zh))


# subset the CJK font to the title characters, otherwise the whole 25 MB face gets embedded
from fontTools import subset as ft_subset
SUB = str(WORK / "fonts" / "title_subset.otf")
opts = ft_subset.Options(); opts.name_IDs = ["*"]; opts.retain_gids = True; opts.notdef_outline = True
font = ft_subset.load_font(FONT, opts)
sub = ft_subset.Subsetter(opts); sub.populate(text="".join(j[3] for j in jobs) + "0123456789｜"); sub.subset(font)
ft_subset.save_font(font, SUB, opts)
FONT = SUB


def overlay(doc, x_offset, out):
    for pno, rect, size, zh in jobs:
        page = doc[pno]
        r = pymupdf.Rect(rect.x0 + x_offset, rect.y0 - 2, rect.x1 + x_offset, rect.y1 + 2)
        page.add_redact_annot(r, fill=(1, 1, 1))
        page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE, graphics=pymupdf.PDF_REDACT_LINE_ART_NONE)
        page.insert_font(fontname="zhb", fontfile=FONT)
        fs = size * 0.92
        width = pymupdf.Font(fontfile=FONT).text_length(zh, fontsize=fs)
        max_w = 595 - 40 - (rect.x0 - (0 if x_offset == 0 else 0))
        if width > max_w:
            fs *= max_w / width
        page.insert_text((r.x0, rect.y1 - size * 0.18), zh, fontname="zhb", fontsize=fs, color=(0.1, 0.1, 0.1))
    doc.save(out, garbage=3, deflate=True)


overlay(pymupdf.open(mono_in), 0, mono_out)
dual = pymupdf.open(dual_in)
overlay(dual, dual[0].rect.width / 2, dual_out)
print("fixed", len(jobs), "chapter titles:", [j[3] for j in jobs])
