"""Step 4d: translate the rotated vector text of the appendix pages (p99 timeline, p100–101 lineage charts,
p102 artefact table) in place: remove each Japanese line from the text layer and write the Chinese line at
the same origin and rotation. Graphics are left untouched.

usage: appendix.py IN_ZH.pdf OUT_ZH.pdf [--pages 99,100,101,102]
Translations are cached in work/appendix_lines.json.
"""
import argparse, base64, io, json, re, sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import pymupdf

sys.path.insert(0, str(Path(__file__).parent))
from llm import call

WORK = Path(__file__).resolve().parent.parent
ap = argparse.ArgumentParser()
ap.add_argument("inp"); ap.add_argument("out")
ap.add_argument("--pages", default="99,100,101,102")
args = ap.parse_args()
PAGES = [int(p) for p in args.pages.split(",")]
JA = re.compile(r"[぀-ヿ㐀-鿿]")
FONT_FULL = str(WORK / "fonts" / "NotoSansCJKsc-Regular.otf")
cache_path = WORK / "appendix_lines.json"
cache = json.load(open(cache_path)) if cache_path.exists() else {}
blocks = json.load(open(WORK / "blocks.json"))
figs = json.load(open(WORK / "figures.json"))
appx = {b["page"]: figs.get(b["id"], {}) for b in blocks if b["type"] == "appendix"}

doc = pymupdf.open(args.inp)


def page_lines(page):
    out = []
    for b in page.get_text("dict")["blocks"]:
        for l in b.get("lines", []):
            text = "".join(s["text"] for s in l["spans"]).strip()
            if not text or not JA.search(text):
                continue
            s0 = l["spans"][0]
            out.append({"text": text, "bbox": list(l["bbox"]), "dir": list(l["dir"]), "origin": list(s0["origin"]),
                        "size": max(s["size"] for s in l["spans"])})
    # reading order: for text running upwards the columns go left→right, lines within a column top→bottom
    out.sort(key=lambda x: (round(x["bbox"][0] / 6), x["bbox"][1]))
    for n, x in enumerate(out, 1):
        x["id"] = n
    return out


SYSTEM = """你在为日文 PDF 的附录页（横排的年表、技术系统化图、资料一览表）制作中文版，逐行替换文字。
你会收到页面图片（已转成横向，便于阅读）、这一页已有的「日文 → 中文」对照或中文译表（以它为准），以及按编号列出的文字行。
请把每一行译成中文：
- 同一个单元格或同一句话被拆成相邻几行时，把译文按顺序分到这几行，拼起来要通顺；每行中文长度与原行相当或更短，因为要放回原位置；
- 年份、数字、人名的原文拼写、英文、型号、化学式原样保留；西方人名写成中文通行译名，括号里的原文拼写保留；
- 术语与已有对照一致；不加解释或括注。
只输出 JSON 对象：{"1": "…", "2": "…"}，覆盖每一个编号。"""


def translate_page(pno):
    key = str(pno + 1)
    page = doc[pno]
    lines = page_lines(page)
    if key in cache and len(cache[key]["lines"]) == len(lines):
        return key
    pix = page.get_pixmap(dpi=110)
    from PIL import Image
    im = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    if any(abs(x["dir"][1]) > 0.5 for x in lines):
        im = im.rotate(-90, expand=True)
    buf = io.BytesIO(); im.save(buf, "PNG")
    ref = appx.get(pno + 1, {})
    ref_txt = json.dumps({"labels": ref.get("labels"), "table": ref.get("table")}, ensure_ascii=False)[:30000]
    result = {}
    for k in range(0, len(lines), 90):                        # keep each request a manageable size
        part = lines[max(0, k - 5):k + 90]                     # 5 lines of overlap for context
        text = (f"第 {pno + 1} 页。\n【已有对照 / 译表】\n{ref_txt}\n\n【文字行】\n" +
                "\n".join(f'{x["id"]}: {x["text"]}' for x in part))
        out = call(SYSTEM, [{"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                                         "data": base64.b64encode(buf.getvalue()).decode()}},
                            {"type": "text", "text": text}], tag=f"appendix:p{pno + 1}", effort="medium", max_tokens=32000)
        res = json.loads(out[out.index("{"):out.rindex("}") + 1])
        for x in lines[k:k + 90]:
            if str(x["id"]) in res:
                result[str(x["id"])] = res[str(x["id"])]
    for x in lines:
        x["zh"] = result.get(str(x["id"]), "")
    cache[key] = {"lines": lines}
    return key


with ThreadPoolExecutor(4) as ex:
    for k in ex.map(translate_page, [p - 1 for p in PAGES]):
        print("translated page", k, "lines:", len(cache[k]["lines"]), "missing:", sum(1 for x in cache[k]["lines"] if not x["zh"]), flush=True)
json.dump(cache, open(cache_path, "w"), ensure_ascii=False, indent=1)

# font subset with exactly the characters we write
from fontTools import subset as ft_subset
chars = set("".join(x["zh"] for k in cache for x in cache[k]["lines"])) | set("0123456789")
chars = {c for c in chars if not (c in "⁰¹²³⁴⁵⁶⁷⁸⁹₀₁₂₃₄₅₆₇₈₉" and not pymupdf.Font(fontfile=FONT_FULL).has_glyph(ord(c)))}
SUB = str(WORK / "fonts" / "appendix_subset.otf")
opts = ft_subset.Options(); opts.name_IDs = ["*"]; opts.retain_gids = True; opts.notdef_outline = True
font = ft_subset.load_font(FONT_FULL, opts)
sub = ft_subset.Subsetter(opts); sub.populate(text="".join(sorted(chars))); sub.subset(font)
ft_subset.save_font(font, SUB, opts)
MEASURE = pymupdf.Font(fontfile=SUB)

PLAIN = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹₀₁₂₃₄₅₆₇₈₉", "01234567890123456789")
for k in cache:                                           # glyphs the font lacks → plain digits (rotated text)
    for x in cache[k]["lines"]:
        x["zh"] = "".join(c if MEASURE.has_glyph(ord(c)) else c.translate(PLAIN) for c in x["zh"])

for p in PAGES:
    page = doc[p - 1]
    lines = [x for x in cache[str(p)]["lines"] if x["zh"]]
    for x in lines:                                           # remove the Japanese text only (no fill, graphics kept)
        r = pymupdf.Rect(x["bbox"])
        shrink = 0.36 * (r.width if abs(x["dir"][1]) > 0.5 else r.height)    # stay clear of neighbouring lines
        r = r + ((shrink, 0, -shrink, 0) if abs(x["dir"][1]) > 0.5 else (0, shrink, 0, -shrink))
        page.add_redact_annot(r, fill=False)
    page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE, graphics=pymupdf.PDF_REDACT_LINE_ART_NONE)
    page.insert_font(fontname="apx", fontfile=SUB)
    for x in lines:
        r = pymupdf.Rect(x["bbox"])
        dx, dy = x["dir"]
        if abs(dy) > 0.5:                                     # vertical run (page rotated content)
            length, rot = r.height, (90 if dy < 0 else 270)
        else:
            length, rot = r.width, 0
        fs = x["size"]
        w = MEASURE.text_length(x["zh"], fontsize=fs)
        if w > length * 1.02:
            fs = max(3.0, fs * length * 1.02 / w)
        page.insert_text(pymupdf.Point(x["origin"]), x["zh"], fontname="apx", fontfile=SUB, fontsize=fs,
                         rotate=rot, color=(0, 0, 0))
    print("rewrote page", p, "lines:", len(lines), flush=True)
doc.save(args.out, garbage=3, deflate=True)
print("wrote", args.out)
