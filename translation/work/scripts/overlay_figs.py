"""Step 4c: write Chinese into the raster figures and tables of the layout PDFs.

Figures: OCR (tesseract jpn) finds where the Japanese sits, Claude reads the boxed image and returns
corrected Japanese + Chinese per box, and each box is covered with its background colour and re-lettered.
Tables: ruling lines give the cell grid, cells are filled from the Chinese table in figures.json.

usage: overlay_figs.py ZH.pdf DUAL.pdf OUT_ZH.pdf OUT_DUAL.pdf [--labels 図2.1,表3.3] [--pages-only]
Plans are cached in work/overlay_plans.json, so re-running does not call the API again.
"""
import argparse, base64, csv, io, json, os, re, statistics, subprocess, sys, tempfile
from pathlib import Path
import pymupdf
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).parent))
from llm import call

WORK = Path(__file__).resolve().parent.parent
ap = argparse.ArgumentParser()
ap.add_argument("zh"); ap.add_argument("dual"); ap.add_argument("out_zh"); ap.add_argument("out_dual")
ap.add_argument("--labels", default="")
ap.add_argument("--pages-only", action="store_true", help="save only the pages that were changed (for samples)")
ap.add_argument("--figure-mode", default="annotate", choices=["annotate", "replace"],
                help="annotate: keep the image, add Chinese next to each label (default); replace: paint over the Japanese")
args = ap.parse_args()

blocks = json.load(open(WORK / "blocks.json"))
figs = json.load(open(WORK / "figures.json"))
src = pymupdf.open(WORK / "source_decrypted.pdf")
plan_path = WORK / "overlay_plans.json"
plans = json.load(open(plan_path)) if plan_path.exists() else {}
FONT_FULL = str(WORK / "fonts" / "NotoSansCJKsc-Regular.otf")
DPI = 400
JA = re.compile(r"[぀-ヿ㐀-鿿]")

wanted = {l for l in args.labels.split(",") if l}
targets = [b for b in blocks if b["type"] in ("fig", "tab") and b.get("has_raster")
           and (figs.get(b["id"], {}).get("labels") or figs.get(b["id"], {}).get("table"))
           and len(b["figtext"]) <= 1 and (not wanted or b["label"] in wanted)]


def image_rects(page, region):
    rects = []
    for i in page.get_image_info():
        r = pymupdf.Rect(i["bbox"])
        if r.intersects(region) and r.width * r.height > 2000:
            rects.append(r)
    return rects


def render(rect, pno):
    pix = src[pno].get_pixmap(dpi=DPI, clip=rect)
    return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)


# ---------------------------------------------------------------- OCR → text segments
def ocr_words(img):
    with tempfile.NamedTemporaryFile(suffix=".png") as tf:
        img.save(tf.name)
        out = subprocess.run(["tesseract", tf.name, "-", "-l", "jpn", "--psm", "11", "tsv"],
                             capture_output=True, text=True, env={**os.environ, "OMP_THREAD_LIMIT": "1"}).stdout
    return [(int(w["left"]), int(w["top"]), int(w["left"]) + int(w["width"]), int(w["top"]) + int(w["height"]), w["text"], float(w["conf"]))
            for w in csv.DictReader(io.StringIO(out), delimiter="\t")
            if w["level"] == "5" and w["text"].strip() and float(w["conf"]) > 5]


def ocr_segments(img):
    from PIL import ImageOps
    words = ocr_words(img)
    gray = img.convert("L")
    white_text = gray.point(lambda v: 0 if v > 205 else 255).convert("RGB")     # white lettering → black on white
    extra = ocr_words(ImageOps.invert(img.convert("RGB"))) + \
        [w for w in ocr_words(white_text) if statistics.median(
            gray.getpixel((x, y)) for x in range(max(0, w[0] - 6), min(img.width, w[2] + 6), 3)
            for y in (max(0, w[1] - 6), min(img.height - 1, w[3] + 5))) < 190]      # only where the surround is dark
    for w in extra:
        a = (w[2] - w[0]) * (w[3] - w[1])
        if not any(max(0, min(w[2], v[2]) - max(w[0], v[0])) * max(0, min(w[3], v[3]) - max(w[1], v[1])) > 0.4 * a for v in words):
            words.append(w)
    boxes = [b for b in words if 8 < b[3] - b[1] < img.height * 0.3]
    if not boxes:
        return []
    h = statistics.median(b[3] - b[1] for b in boxes)
    boxes = [b for b in boxes if b[3] - b[1] < 2.2 * h]                            # drawings misread as huge glyphs
    parent = list(range(len(boxes)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]; i = parent[i]
        return i
    for i, a in enumerate(boxes):
        for j in range(i + 1, len(boxes)):
            b = boxes[j]
            same_line = abs((a[1] + a[3]) / 2 - (b[1] + b[3]) / 2) < 0.45 * h
            gap = max(b[0] - a[2], a[0] - b[2])
            if same_line and gap < 0.9 * h:
                parent[find(i)] = find(j)
    groups = {}
    for i in range(len(boxes)):
        groups.setdefault(find(i), []).append(boxes[i])
    segs = []
    for g in groups.values():
        g.sort(key=lambda b: b[0])
        segs.append({"box": [min(b[0] for b in g), min(b[1] for b in g), max(b[2] for b in g), max(b[3] for b in g)],
                     "ocr": "".join(b[4] for b in g), "ch": statistics.median(b[3] - b[1] for b in g)})
    segs.sort(key=lambda s: (s["box"][1] // max(1, int(h)), s["box"][0]))
    for n, s in enumerate(segs, 1):
        s["id"] = n
    return segs


def annotated(img, segs, max_w=1600):
    im = img.copy(); d = ImageDraw.Draw(im)
    try:
        f = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", max(14, img.width // 90))
    except OSError:
        f = ImageFont.load_default()
    for s in segs:
        x0, y0, x1, y1 = s["box"]
        d.rectangle([x0 - 2, y0 - 2, x1 + 2, y1 + 2], outline=(230, 0, 0), width=3)
        d.text((x0, max(0, y0 - f.size - 2)), str(s["id"]), fill=(0, 0, 230), font=f)
    if im.width > max_w:
        im = im.resize((max_w, round(im.height * max_w / im.width)), Image.LANCZOS)
    buf = io.BytesIO(); im.save(buf, "PNG")
    return {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": base64.b64encode(buf.getvalue()).decode()}}


SYSTEM = """你在为一份日文 PDF 制作中文版，任务是替换图片里的日文文字。你会收到：
1. 一张图：红框是 OCR 检测到的文字区域，框左上角的蓝色数字是编号；
2. OCR 结果：识别出的文字可能有错字，以图片为准；
3. 这张图已有的「日文 → 中文」对照（以它为准，全文术语一致）。

请列出要替换的每一处日文（一个词、短语或标签算一处），给出它占用的框编号 ids、校正后的日文 ja 和中文 zh：
- 一处日文被拆进多个框（同一行断成几段，或一个标签分成两行）时，把这些框都放进同一个 ids，中文整体给出；
- 一个框里有几个互不相干的标签时，仍按一处处理，zh 按原来的顺序写，中间保留原来的空格或标点；
- 数字、单位、纯英文、化学式、型号，以及箭头、线条等被误识别成的字，不要列入；如果它们和日文在同一个框里，zh 中原样保留；
- zh 要简短，适合放回原位置，不加括注或解释；
- 每个编号最多出现在一处。
另外列出图中能看到、但没有被任何框覆盖的日文（unboxed）。

只输出 JSON：{"items": [{"ids": [3, 4], "ja": "フローセル", "zh": "流动池"}], "unboxed": ["…"]}"""


def plan_figure(b, rect_idx, img):
    segs = ocr_segments(img)
    if not segs:
        return {"kind": "fig", "segments": [], "unboxed": []}
    f = figs[b["id"]]
    pairs = "\n".join(f'{l["ja"]} → {l["zh"]}' for l in f.get("labels") or [])
    text = (f"图号：{b['label']}（第 {b['page']} 页，图片 {rect_idx + 1}）\n【已有对照】\n{pairs}\n\n【OCR 区域】\n"
            + "\n".join(f'{s["id"]}: {s["ocr"]}' for s in segs))
    out = call(SYSTEM, [annotated(img, segs), {"type": "text", "text": text}], tag=f"overlay:{b['label']}", effort="medium", max_tokens=16000)
    res = json.loads(out[out.index("{"):out.rindex("}") + 1])
    by_id = {s["id"]: s for s in segs}
    keep, used = [], set()
    for r in res.get("items", []):
        ss = [by_id[i] for i in r.get("ids", []) if i in by_id and i not in used]
        if not ss or not r.get("zh") or not JA.search(r.get("ja", "") + "".join(x["ocr"] for x in ss)):
            continue
        used |= {x["id"] for x in ss}
        rows = []
        for x in sorted(ss, key=lambda x: x["box"][1]):
            if not rows or x["box"][1] > rows[-1] + 0.6 * x["ch"]:
                rows.append(x["box"][1])
        keep.append({"box": [min(x["box"][0] for x in ss), min(x["box"][1] for x in ss), max(x["box"][2] for x in ss), max(x["box"][3] for x in ss)],
                     "ja": r.get("ja", ""), "zh": r["zh"], "ch": statistics.median(x["ch"] for x in ss), "rows": len(rows)})
    unboxed = [u for u in res.get("unboxed", []) if isinstance(u, str) and JA.search(u)]
    ch = statistics.median(x["ch"] for x in segs)
    located = locate_unboxed(b, img, unboxed, pairs, ch) if unboxed else []

    def covered(x):                                   # already labelled in the first pass (or a fragment of such a word)
        cx, cy = (x["box"][0] + x["box"][2]) / 2, (x["box"][1] + x["box"][3]) / 2
        for k in keep:
            x0, y0, x1, y1 = k["box"]; m = 0.6 * ch
            if x0 - m <= cx <= x1 + m and y0 - m <= cy <= y1 + m:
                return True
        return False
    located = [x for x in located if not covered(x)]
    return {"kind": "fig", "segments": keep + located, "unboxed": unboxed}


LOCATE = """图上叠加了坐标网格（细青线每 100 像素一条，数字是像素坐标，原点在左上角）。
请找出下面列出的每处日文在图中的位置，给出紧贴该文字的矩形 [x0, y0, x1, y1]（像素坐标，按这张图的尺寸），并给出中文译文（以已有对照为准）。
同一段日文在图中出现多次时分别列出。找不到的不要列。
只输出 JSON：{"items": [{"ja": "…", "zh": "…", "box": [x0, y0, x1, y1]}]}"""


def gridded(img, max_w=1400):
    scale = min(1.0, max_w / img.width)
    im = img.resize((round(img.width * scale), round(img.height * scale)), Image.LANCZOS) if scale < 1 else img.copy()
    d = ImageDraw.Draw(im)
    try:
        f = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 13)
    except OSError:
        f = ImageFont.load_default()
    for x in range(0, im.width, 100):
        d.line([(x, 0), (x, im.height)], fill=(0, 190, 210), width=1); d.text((x + 2, 2), str(x), fill=(0, 120, 140), font=f)
    for y in range(0, im.height, 100):
        d.line([(0, y), (im.width, y)], fill=(0, 190, 210), width=1); d.text((2, y + 2), str(y), fill=(0, 120, 140), font=f)
    buf = io.BytesIO(); im.save(buf, "PNG")
    return {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": base64.b64encode(buf.getvalue()).decode()}}, scale, im.size


def locate_unboxed(b, img, unboxed, pairs, ch):
    block, scale, size = gridded(img)
    text = (f"图号：{b['label']}，图片尺寸 {size[0]}×{size[1]} 像素。\n【已有对照】\n{pairs}\n\n【要定位的日文】\n" + "\n".join(unboxed))
    out = call(LOCATE, [block, {"type": "text", "text": text}], tag=f"locate:{b['label']}", effort="medium", max_tokens=8000)
    try:
        res = json.loads(out[out.index("{"):out.rindex("}") + 1])
    except ValueError:
        return []
    found = []
    for r in res.get("items", []):
        bx = r.get("box")
        if not (isinstance(bx, list) and len(bx) == 4 and r.get("zh")):
            continue
        x0, y0, x1, y1 = (int(v / scale) for v in bx)
        x0, x1 = sorted((max(0, x0), min(img.width, x1))); y0, y1 = sorted((max(0, y0), min(img.height, y1)))
        if x1 - x0 > 4 and y1 - y0 > 4:
            found.append({"box": [x0, y0, x1, y1], "ja": r.get("ja", ""), "zh": r["zh"], "ch": ch, "rows": 1, "approx": True})
    return found


# ---------------------------------------------------------------- tables: ruling lines → cells
def rulings(img):
    g = img.convert("L"); w, h = g.size; px = g.load()
    dark = lambda x, y: px[x, y] < 140
    rows = [y for y in range(h) if sum(dark(x, y) for x in range(0, w, 2)) > 0.55 * (w / 2)]
    cols = [x for x in range(w) if sum(dark(x, y) for y in range(0, h, 2)) > 0.55 * (h / 2)]

    def merge(vals):
        out = []
        for v in vals:
            if out and v - out[-1][-1] <= 3:
                out[-1].append(v)
            else:
                out.append([v])
        return [(min(g), max(g)) for g in out]
    return merge(rows), merge(cols)


def plan_table(b, img):
    t = figs[b["id"]]["table"]
    grid = ([t["header"]] if t.get("header") else []) + t["rows"]
    rows, cols = rulings(img)
    want_r, want_c = len(grid) + 1, max(len(r) for r in grid) + 1

    def thin(lines, want):                       # double borders / stray rules: merge the closest pair until counts match
        lines = list(lines)
        while len(lines) > want:
            k = min(range(len(lines) - 1), key=lambda i: lines[i + 1][0] - lines[i][1])
            if k + 1 == len(lines) - 1:              # pair at the far edge: keep the outer (last) line
                del lines[k]
            else:                                    # otherwise keep the first; the cell then paints over the stray rule
                del lines[k + 1]
        return lines
    if len(rows) > want_r and len(rows) - want_r <= 2:
        rows = thin(rows, want_r)
    if len(cols) > want_c and len(cols) - want_c <= 2:
        cols = thin(cols, want_c)
    if len(rows) - 1 != len(grid) or len(cols) - 1 != max(len(r) for r in grid):
        return {"kind": "table", "ok": False, "found": [len(rows) - 1, len(cols) - 1], "want": [len(grid), max(len(r) for r in grid)]}
    cells = []
    for i, row in enumerate(grid):
        for j, txt in enumerate(row):
            box = [cols[j][1] + 1, rows[i][1] + 1, cols[j + 1][0] - 1, rows[i + 1][0] - 1]
            cells.append({"box": box, "zh": re.sub(r"<br\s*/?>", "\n", str(txt)), "header": bool(t.get("header")) and i == 0})
    return {"kind": "table", "ok": True, "cells": cells}


# ---------------------------------------------------------------- build plans (cached, in parallel)
import threading
from concurrent.futures import ThreadPoolExecutor
lock = threading.Lock()


def plan_block(b):
    pno = b["page"] - 1
    rects = image_rects(src[pno], pymupdf.Rect(b["bbox"]))
    entry = {"label": b["label"], "page": b["page"], "images": []}
    for k, r in enumerate(rects):
        pix = pymupdf.open(WORK / "source_decrypted.pdf")[pno].get_pixmap(dpi=DPI, clip=r)   # own handle per thread
        img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        if b["type"] == "tab" and figs[b["id"]].get("table"):
            p = plan_table(b, img)
            if not p.get("ok"):
                print(f"{b['label']}: grid {p['found']} != table {p['want']}, using side notes", flush=True)
                p = plan_figure(b, k, img)
        else:
            p = plan_figure(b, k, img)
        p.update({"rect": [r.x0, r.y0, r.x1, r.y1], "px": [img.width, img.height]})
        entry["images"].append(p)
    with lock:
        plans[b["id"]] = entry
        json.dump(plans, open(plan_path, "w"), ensure_ascii=False, indent=1)
    n = sum(len(p.get("segments", p.get("cells", []))) for p in entry["images"])
    a = sum(sum(1 for x in p.get("segments", []) if x.get("approx")) for p in entry["images"])
    print(f"planned {b['label']}: {n} replacements ({a} located in 2nd pass)", flush=True)


todo = [b for b in targets if b["id"] not in plans]
with ThreadPoolExecutor(6) as ex:
    for f in [ex.submit(plan_block, b) for b in todo]:
        try:
            f.result()
        except Exception as e:                     # one bad figure must not stop the rest
            print("PLAN FAILED", repr(e), flush=True)

# ---------------------------------------------------------------- font subset with every character we will write
from fontTools import subset as ft_subset
chars = set("0123456789+-()")
for e in plans.values():
    for p in e["images"]:
        for s in p.get("segments", []) + p.get("cells", []):
            chars |= set(re.sub(r"<[^>]+>", "", s["zh"]))
SUB_FONT = str(WORK / "fonts" / "overlay_subset.otf")
opts = ft_subset.Options(); opts.name_IDs = ["*"]; opts.retain_gids = True; opts.notdef_outline = True
font = ft_subset.load_font(FONT_FULL, opts)
sub = ft_subset.Subsetter(opts); sub.populate(text="".join(sorted(chars))); sub.subset(font)
ft_subset.save_font(font, SUB_FONT, opts)
MEASURE = pymupdf.Font(fontfile=SUB_FONT)


def colours(img, box):
    """Background = most frequent (quantised) colour in a ring around the box; foreground = the pixels most unlike it."""
    from collections import Counter
    x0, y0, x1, y1 = box; w, h = img.size; px = img.load()
    ring = [px[x, y] for x in range(max(0, x0 - 5), min(w, x1 + 5)) for y in (max(0, y0 - 5), min(h - 1, y1 + 4))] + \
           [px[x, y] for y in range(max(0, y0 - 5), min(h, y1 + 5)) for x in (max(0, x0 - 5), min(w - 1, x1 + 4))]
    q = Counter(tuple(v // 24 for v in c) for c in ring).most_common(1)[0][0]
    members = [c for c in ring if tuple(v // 24 for v in c) == q]
    bg = tuple(statistics.median(c[i] for c in members) for i in range(3))
    inside = sorted((px[x, y] for x in range(x0, x1, 2) for y in range(y0, y1, 2)),
                    key=lambda c: -sum(abs(c[i] - bg[i]) for i in range(3)))
    far = inside[:max(1, len(inside) // 7)]
    fg = tuple(statistics.median(c[i] for c in far) for i in range(3))
    if sum(abs(fg[i] - bg[i]) for i in range(3)) < 150:
        fg = (0, 0, 0) if sum(bg) > 380 else (255, 255, 255)
    return tuple(v / 255 for v in bg), tuple(v / 255 for v in fg)


def grow(img, box, ch, bg):
    """Extend a box sideways (and a little vertically) while ink continues, so the whole word gets covered."""
    g = img.convert("L"); px = g.load(); w, h = g.size
    bgv = sum(bg) / 3 * 255
    x0, y0, x1, y1 = box
    ink_col = lambda x: any(abs(px[x, y] - bgv) > 70 for y in range(y0, y1, 2))
    gap_max, reach = int(0.6 * ch), int(2.5 * ch)

    def walk(start, step, limit):
        last, x = start, start
        while 0 <= x + step < w and abs(x - start) < limit:
            x += step
            if ink_col(x):
                last = x
            elif abs(x - last) > gap_max:
                break
        return last
    nx0, nx1 = walk(x0, -1, reach), walk(x1, 1, reach)
    ink_row = lambda y: any(abs(px[x, y] - bgv) > 70 for x in range(nx0, nx1, 2))
    ny0, ny1 = y0, y1
    while ny0 > 0 and y0 - ny0 < 0.3 * ch and ink_row(ny0 - 1):
        ny0 -= 1
    while ny1 < h - 1 and ny1 - y1 < 0.3 * ch and ink_row(ny1 + 1):
        ny1 += 1
    return [nx0, ny0, nx1, ny1]


SUP = dict(zip("⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁽⁾", "0123456789+-()"))
SUB = dict(zip("₀₁₂₃₄₅₆₇₈₉₊₋₍₎", "0123456789+-()"))


def runs(text):
    """Split into (kind, text) runs: kind is '', 'sup' or 'sub'. <sup>…</sup> and glyphs missing from the font become runs."""
    out = []
    for part in re.split(r"(<sup>.*?</sup>|<sub>.*?</sub>)", text):
        if part.startswith("<sup>"):
            out.append(("sup", re.sub(r"<[^>]+>", "", part))); continue
        if part.startswith("<sub>"):
            out.append(("sub", re.sub(r"<[^>]+>", "", part))); continue
        for ch in re.sub(r"<[^>]+>", "", part):
            kind = "sup" if ch in SUP and not MEASURE.has_glyph(ord(ch)) else "sub" if ch in SUB and not MEASURE.has_glyph(ord(ch)) else ""
            c = SUP.get(ch, ch) if kind == "sup" else SUB.get(ch, ch) if kind == "sub" else ch
            if out and out[-1][0] == kind:
                out[-1] = (kind, out[-1][1] + c)
            else:
                out.append((kind, c))
    return [r for r in out if r[1]]


def measure(text, fs):
    return sum(MEASURE.text_length(t, fontsize=fs * (0.62 if k else 1)) for k, t in runs(text))


def draw(page, x, y, text, fs, color):
    for k, t in runs(text):
        size = fs * (0.62 if k else 1)
        dy = -0.38 * fs if k == "sup" else 0.15 * fs if k == "sub" else 0
        page.insert_text((x, y + dy), t, fontname="ovl", fontfile=SUB_FONT, fontsize=size, color=color)
        x += MEASURE.text_length(t, fontsize=size)


def wrap(text, fs, width):
    lines = []
    for para in text.split("\n"):
        cur = ""
        for tok in re.findall(r"<su[pb]>.*?</su[pb]>|[A-Za-z0-9.\-/+%α-ωΑ-Ω]+|.", para):
            if cur and measure(cur + tok, fs) > width:
                lines.append(cur); cur = tok.lstrip()
            else:
                cur += tok
        lines.append(cur)
    return lines


def put_item(page, r, zh, bg, fg, dx, ch_pt, rows):
    fs = ch_pt * 0.95
    while True:
        lines = wrap(zh, fs, r.width)
        if len(lines) <= max(rows, 1) or fs <= ch_pt * 0.6:
            break
        fs -= 0.2
    lines = wrap(zh, fs, r.width) if len(lines) > max(rows, 1) else lines
    if len(lines) > max(rows, 1):                    # still too long: keep the rows, widen the patch
        lines = [zh] if rows <= 1 else lines
    width = max(measure(ln, fs) for ln in lines)
    cover = pymupdf.Rect(r)
    if width > r.width:
        cx = (r.x0 + r.x1) / 2
        cover = pymupdf.Rect(cx - width / 2 - 0.6, r.y0, cx + width / 2 + 0.6, r.y1)
    need_h = len(lines) * fs * 1.18
    if need_h > cover.height:
        cy = (cover.y0 + cover.y1) / 2
        cover = pymupdf.Rect(cover.x0, cy - need_h / 2, cover.x1, cy + need_h / 2)
    cover = cover + (dx, 0, dx, 0)
    page.draw_rect(cover, color=None, fill=bg, overlay=True)
    cx = (cover.x0 + cover.x1) / 2
    y = (cover.y0 + cover.y1) / 2 - need_h / 2 + fs * 0.92
    for ln in lines:
        w = measure(ln, fs)
        draw(page, cx - w / 2, y, ln, fs, fg)
        y += fs * 1.18


NOTE_COLOUR = (0.62, 0.10, 0.04)


def put_note(page, img, seg, others, placed, to_pdf, R, W, H, dx):
    """Write the Chinese next to the Japanese label, in the emptiest nearby spot; the image itself is untouched."""
    sx, sy = R.width / W, R.height / H                    # pt per px
    ch_pt = seg["ch"] * sy                              # seg["ch"] is the figure's median letter height
    fs = max(4.0, min(ch_pt * 0.72, 7.0))
    lines = [seg["zh"]]
    x0, y0, x1, y1 = seg["box"]
    if measure(seg["zh"], fs) > max(3 * (x1 - x0) * sx, 60):
        lines = wrap(seg["zh"], fs, max((x1 - x0) * sx * 1.6, 40))
    w = max(measure(ln, fs) for ln in lines) / sx + 4
    h = len(lines) * fs * 1.2 / sy + 2
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    cands = [(cx - w / 2, y1 + 2), (x1 + 3, cy - h / 2), (x0, y1 + 2), (cx - w / 2, y0 - h - 2),
             (x0 - w - 3, cy - h / 2), (x1 - w, y1 + 2), (x0, y0 - h - 2)]
    g = img.convert("L"); gp = g.load()

    def score(k, c):
        a = [max(0, c[0]), max(0, c[1]), min(W, c[0] + w), min(H, c[1] + h)]
        if a[2] - a[0] < w * 0.8 or a[3] - a[1] < h * 0.8:
            return 1e9
        pts = [(x, y) for x in range(int(a[0]), int(a[2]), 3) for y in range(int(a[1]), int(a[3]), 3)]
        ink = sum(gp[x, y] < 200 for x, y in pts) / max(1, len(pts))
        hit = sum(1 for o in others + placed if not (a[2] < o[0] or a[0] > o[2] or a[3] < o[1] or a[1] > o[3]))
        return ink * 10 + hit * 6 + k * 0.15
    k, c = min(enumerate(cands), key=lambda kc: score(*kc))
    a = [max(0, min(W - w, c[0])), max(0, min(H - h, c[1]))]
    box = [a[0], a[1], a[0] + w, a[1] + h]
    placed.append(box)
    r = to_pdf(box) + (dx, 0, dx, 0)
    page.draw_rect(r, color=None, fill=(1, 1, 1), fill_opacity=0.82, overlay=True)
    y = r.y0 + 1 / sy * sy + fs * 0.92
    for ln in lines:
        draw(page, r.x0 + 2 * sx, y, ln, fs, NOTE_COLOUR)
        y += fs * 1.2


def put_cell(page, r, zh, bg, dx, fs_max, bold=False):
    pad = min(1.5, r.width * 0.04)
    inner = pymupdf.Rect(r.x0 + pad, r.y0 + 0.5, r.x1 - pad, r.y1 - 0.5)
    fs = fs_max
    while fs > 3.2:
        lines = wrap(zh, fs, inner.width)
        if len(lines) * fs * 1.22 <= inner.height:
            break
        fs -= 0.25
    lines = wrap(zh, fs, inner.width)
    page.draw_rect(pymupdf.Rect(r) + (dx, 0, dx, 0), color=None, fill=bg, overlay=True)
    total = len(lines) * fs * 1.22
    y = inner.y0 + (inner.height - total) / 2 + fs * 0.98
    for ln in lines:
        draw(page, inner.x0 + dx, y, ln, fs, (0, 0, 0))
        y += fs * 1.22


def apply(doc, dx_fn):
    touched = set()
    for b in targets:
        e = plans.get(b["id"])
        if not e:
            continue
        page = doc[e["page"] - 1]
        dx = dx_fn(page)
        page.insert_font(fontname="ovl", fontfile=SUB_FONT)
        for p in e["images"]:
            R = pymupdf.Rect(p["rect"]); W, H = p["px"]
            img = render(R, e["page"] - 1)
            to_pdf = lambda box: pymupdf.Rect(R.x0 + box[0] * R.width / W, R.y0 + box[1] * R.height / H,
                                              R.x0 + box[2] * R.width / W, R.y0 + box[3] * R.height / H)
            if p["kind"] == "table":
                heights = [c["box"][3] - c["box"][1] for c in p["cells"]]
                fs_max = min(max(statistics.median(heights) * R.height / H * 0.6, 5.0), 8.5)
                for c in p["cells"]:                      # source tables are black on white
                    put_cell(page, to_pdf(c["box"]), c["zh"], (1, 1, 1), dx, fs_max)
            else:
                med = statistics.median(x["ch"] for x in p["segments"]) if p["segments"] else 0
                if args.figure_mode == "annotate":
                    placed, boxes = [], [x["box"] for x in p["segments"]]
                    for s in sorted(p["segments"], key=lambda x: (x["box"][1], x["box"][0])):
                        seg = dict(s, ch=med)                    # one note size per figure
                        put_note(page, img, seg, [b2 for b2 in boxes if b2 is not s["box"]], placed, to_pdf, R, W, H, dx)
                    continue
                for s in p["segments"]:
                    ch = min(max(s["ch"], 0.7 * med), 1.5 * med)
                    bg, _ = colours(img, s["box"])
                    x0, y0, x1, y1 = grow(img, s["box"], ch, bg)
                    pad = max(3, int(ch * 0.18))
                    box = [max(0, x0 - pad), max(0, y0 - pad), min(W, x1 + pad), min(H, y1 + pad)]
                    bg, fg = colours(img, box)
                    put_item(page, to_pdf(box), s["zh"], bg, fg, dx, ch * R.height / H, s["rows"])
        touched.add(e["page"] - 1)
    return touched


for path_in, path_out, dx_fn in ((args.zh, args.out_zh, lambda p: 0), (args.dual, args.out_dual, lambda p: p.rect.width / 2)):
    if path_in == "-":
        continue
    doc = pymupdf.open(path_in)
    touched = apply(doc, dx_fn)
    if args.pages_only:
        out = pymupdf.open()
        for pno in sorted(touched):
            out.insert_pdf(doc, from_page=pno, to_page=pno)
        out.save(path_out, garbage=4, deflate=True)
    else:
        doc.save(path_out, garbage=3, deflate=True)
    print("wrote", path_out, "pages changed:", [p + 1 for p in sorted(touched)])
