"""Step 1: structured extraction of the Japanese PDF into blocks.json + cropped figure images.

Block types: h1/h2/h3 headings, p (body paragraph), ref (reference item), note (註), caption-bearing
figure/table objects (fig/tab, with region crop + in-figure text), front (front-matter paragraph),
en (English text kept as-is), appendix (whole-page objects p99-103).
"""
import json, re, sys, unicodedata
from collections import Counter
from pathlib import Path
import pymupdf

SRC, OUT = Path(sys.argv[1]), Path(sys.argv[2])
IMG_DIR = OUT / "img"
IMG_DIR.mkdir(parents=True, exist_ok=True)
APPENDIX_FROM = 99          # p99-103: timeline, lineage charts, artefact table, errata
BODY_SIZE, NOTE_SIZE = 9.2, 7.8
COL_WIDTH = 221.0


def norm(s):
    """Map Kangxi radicals / compatibility ideographs to standard kanji; drop auto-spacing between CJK and ASCII."""
    s = "".join(unicodedata.normalize("NFKC", c) if (0x2E80 <= ord(c) <= 0x2FDF or 0xF900 <= ord(c) <= 0xFAFF) else c for c in s)
    s = re.sub(r"[ \u3000]*\t[\t \u3000]*", "　", s)
    s = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", s)
    s = re.sub(r"(?<=[0-9A-Za-z%.)\]])[  ](?=[぀-ヿ㐀-鿿（～])", "", s)
    s = re.sub(r"(?<=[぀-ヿ㐀-鿿）])[  ](?=[0-9A-Za-z(])", "", s)
    return s


def join_lines(a, b):
    if a and b and re.match(r"[A-Za-z0-9,.;:)]", a[-1]) and re.match(r"[A-Za-z(]", b[0]):
        return a + " " + b
    return a + b


def line_info(l):
    spans = [s for s in l["spans"] if s["text"]]
    if not spans:
        return None
    maxsz = max(s["size"] for s in spans)
    parts = []
    for s in spans:
        t = s["text"]
        if maxsz < 12 and s["size"] < maxsz * 0.7 and t.strip():
            t = f"<sup>{t.strip()}</sup>"
        parts.append(t)
    text = norm("".join(parts)).replace("</sup><sup>", "")
    main = max(spans, key=lambda s: (s["size"], len(s["text"])))
    return {"bbox": [round(v, 1) for v in l["bbox"]], "text": text, "size": round(maxsz, 1),
            "font": main["font"].split("+")[-1], "dir": l["dir"]}


def classify(li):
    sz, font, t = li["size"], li["font"], li["text"].strip()
    if li["bbox"][1] >= 795:
        return "footer"
    if not t:
        return "empty"
    if sz >= 19:
        return "h1"
    if 15 <= sz < 19 and re.fullmatch(r"\d+(\.\d+)*", t):
        return "secnum"
    if 15 <= sz < 19 and re.match(r"^\d+\.\d+[\s　]", t):
        return "h2"
    if 9.6 <= sz < 10.5 and font.startswith("FutoGo"):
        return "h2"
    if 9.0 <= sz <= 12 and re.match(r"^\d+\.\d+[\s　]", t) and not font.startswith("Ryumin"):
        return "h2"
    if abs(sz - BODY_SIZE) < 0.2 and font.startswith("ShinGo"):
        return "h3"
    if 8.0 <= sz <= 9.0 and re.match(r"^(図|表)\s*\d", t):
        return "caption"
    if abs(sz - BODY_SIZE) < 0.2 and font.startswith("Ryumin"):
        return "body"
    if sz < 6.5 and font.startswith("Ryumin") and li["bbox"][3] - li["bbox"][1] < 9:
        li["text"] = f"<sup>{t}</sup>"            # stray superscript (exponent, footnote mark) on its own line
        return "body"
    if abs(sz - NOTE_SIZE) < 0.2 and font.startswith("Ryumin"):
        return "note"
    return "figtext"


def rows(lines):
    """Group lines whose vertical centres are within 4pt, order left to right, join with a wide space."""
    out = []
    for li in sorted(lines, key=lambda li: ((li["bbox"][1] + li["bbox"][3]) / 2, li["bbox"][0])):
        cy = (li["bbox"][1] + li["bbox"][3]) / 2
        if out and abs(out[-1]["cy"] - cy) < 4:
            out[-1]["items"].append(li)
        else:
            out.append({"cy": cy, "items": [li]})
    res = []
    for r in out:
        items = sorted(r["items"], key=lambda li: li["bbox"][0])
        text = items[0]["text"].strip()
        for a, b in zip(items, items[1:]):
            text += ("　" if b["bbox"][0] - a["bbox"][2] > 4 else "") + b["text"].strip()
        res.append({"text": text, "size": max(li["size"] for li in items),
                    "bbox": [min(li["bbox"][0] for li in items), min(li["bbox"][1] for li in items),
                             max(li["bbox"][2] for li in items), max(li["bbox"][3] for li in items)]})
    return res


def inflate(r, d):
    return pymupdf.Rect(r.x0 - d, r.y0 - d, r.x1 + d, r.y1 + d)


def cluster(rects, pad=7):
    parent = list(range(len(rects)))
    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    big = [inflate(r, pad) for r in rects]
    for i in range(len(rects)):
        for j in range(i + 1, len(rects)):
            if big[i].intersects(big[j]):
                parent[find(i)] = find(j)
    groups = {}
    for i in range(len(rects)):
        groups.setdefault(find(i), []).append(i)
    return list(groups.values())


doc = pymupdf.open(SRC)
blocks, counter = [], {"n": 0}


def new_block(**kw):
    counter["n"] += 1
    b = {"id": f"b{counter['n']:04d}", **kw}
    blocks.append(b)
    return b


open_para = None      # paragraph that may continue into the next column/page
last_short, last_sentence_end, last_item_head = True, True, False
in_refs = False       # inside a 参考・引用文献 list (may continue across pages)

for pno in range(1, APPENDIX_FROM):
    page = doc[pno - 1]
    d = page.get_text("dict")
    lines = [li for b in d["blocks"] if b["type"] == 0 for l in b["lines"] if (li := line_info(l))]
    for li in lines:
        li["cls"] = classify(li)

    # ---------- front matter (p1-p3) uses its own simple rules ----------
    if pno <= 3:
        open_para, prev = None, None
        flines = [li for li in lines if li["bbox"][1] < 795 and li["text"].strip() and not re.fullmatch(r"\d+", li["text"].strip())]
        right_edge = [max([li["bbox"][2] for li in flines if li["bbox"][0] < 297] or [0]),
                      max([li["bbox"][2] for li in flines if li["bbox"][0] >= 297] or [0])]
        for li in flines:
            t = li["text"]
            ascii_ratio = sum(c.isascii() for c in t) / max(1, len(t))
            kind = "en" if ascii_ratio > 0.85 else "front"
            prev_full = prev is not None and prev["bbox"][2] >= right_edge[0 if prev["bbox"][0] < 297 else 1] - 14
            starts = (open_para is None or not prev_full or open_para["type"] != kind
                      or re.match(r"^\s*(■|　|第\d+章|Chapter|\d{4}\s*年|\d+．)", t))
            if starts:
                open_para = new_block(type=kind, page=pno, text=t.strip(), bbox=li["bbox"])
            else:
                open_para["text"] = join_lines(open_para["text"], t.strip())
            prev = li
        last_short, last_sentence_end, last_item_head = True, True, False
        open_para = None
        continue

    body = [li for li in lines if li["cls"] == "body"]
    def mode(vals, default):
        return Counter(round(v) for v in vals).most_common(1)[0][0] if vals else default
    col_left = [mode([li["bbox"][0] for li in body if li["bbox"][0] < 297], 59.5 if pno % 2 else 70.9),
                mode([li["bbox"][0] for li in body if li["bbox"][0] >= 297], 303.3 if pno % 2 else 314.6)]
    col_right = [mode([li["bbox"][2] for li in body if li["bbox"][0] < 297], col_left[0] + COL_WIDTH),
                 mode([li["bbox"][2] for li in body if li["bbox"][0] >= 297], col_left[1] + COL_WIDTH)]

    def col_of(li):
        return 0 if li["bbox"][0] < 297 else 1

    # merge section number + title on the same baseline
    secnums = [li for li in lines if li["cls"] == "secnum"]
    for sn in secnums:
        for li in lines:
            if li["cls"] == "h2" and abs(li["bbox"][1] - sn["bbox"][1]) < 6 and li["bbox"][0] > sn["bbox"][0]:
                li["text"] = sn["text"].strip() + "　" + li["text"].strip()
                sn["cls"] = "merged"
    for sn in secnums:
        if sn["cls"] == "secnum":
            sn["cls"] = "h2"

    # ---------- text flow: headings + body lines, column by column ----------
    flow = [li for li in lines if li["cls"] in ("h1", "h2", "h3", "body")]
    flow.sort(key=lambda li: (-1 if li["cls"] == "h1" else col_of(li), li["bbox"][1], li["bbox"][0]))
    # merge body fragments that sit on the same visual row (superscripts, tab-aligned values)
    merged = []
    for li in flow:
        prev = merged[-1] if merged else None
        if prev and li["cls"] == prev["cls"] == "body" and col_of(li) == col_of(prev) \
                and abs((li["bbox"][1] + li["bbox"][3]) / 2 - (prev["bbox"][1] + prev["bbox"][3]) / 2) < 6:
            a, b = (prev, li) if prev["bbox"][0] <= li["bbox"][0] else (li, prev)
            gap = b["bbox"][0] - a["bbox"][2]
            prev["text"] = a["text"].rstrip() + ("　" if gap > 6 else "") + b["text"].lstrip()
            prev["bbox"] = [min(a["bbox"][0], b["bbox"][0]), min(a["bbox"][1], b["bbox"][1]),
                            max(a["bbox"][2], b["bbox"][2]), max(a["bbox"][3], b["bbox"][3])]
        else:
            merged.append(dict(li))
    flow = merged
    col_min = [min([li["bbox"][0] for li in flow if li["cls"] == "body" and col_of(li) == c] or [col_left[c]]) for c in (0, 1)]
    for i, li in enumerate(flow):
        t = li["text"].strip()
        if li["cls"] in ("h1", "h2", "h3"):
            t = re.sub(r"\s*｜\s*", "｜", t).strip()
            new_block(type=li["cls"], page=pno, text=t, bbox=li["bbox"])
            in_refs = li["cls"] == "h3" and "文献" in t
            open_para, last_short, last_sentence_end, last_item_head = None, True, True, False
            continue
        c = col_of(li)
        x0, x1 = li["bbox"][0], li["bbox"][2]
        nxt = flow[i + 1] if i + 1 < len(flow) else None
        indented = x0 >= col_min[c] + 5
        marker = bool(re.match(r"^[①-⑳・]|^[（(]\d+[）)]", t))
        item_head = bool(re.match(r"^[（(]\d+[）)]", t)) and "。" not in t and nxt is not None \
            and nxt["cls"] == "body" and col_of(nxt) == c and nxt["bbox"][0] > x0 + 5
        if in_refs:
            starts = bool(re.match(r"^\d+\s*[）)]", t))
        else:
            starts = open_para is None or last_short or last_item_head \
                or ((indented or marker) and last_sentence_end)
            starts = starts and not (t.startswith("<sup>") and open_para is not None)
        if starts or open_para is None:
            open_para = new_block(type="ref" if in_refs else "p", page=pno, text=t, bbox=li["bbox"])
        else:
            open_para["text"] = join_lines(open_para["text"], t)
            open_para.setdefault("pages", [open_para["page"]])
            if pno not in open_para["pages"]:
                open_para["pages"].append(pno)
        last_short = x1 < col_right[c] - 6
        last_sentence_end = bool(re.search(r"[。．」』）)：:]$", t))
        last_item_head = item_head and not in_refs

    # ---------- notes (註): group by column, split at （註N） ----------
    note_lines = sorted([li for li in lines if li["cls"] == "note"], key=lambda li: (col_of(li), li["bbox"][1], li["bbox"][0]))
    cur, prev = None, None
    for li in note_lines:
        t = li["text"]
        same_row = prev is not None and abs(li["bbox"][1] - prev["bbox"][1]) < 2.5
        if t.lstrip().startswith("（註") or cur is None:
            cur = new_block(type="note", page=pno, text=t.strip(), bbox=li["bbox"])
        elif same_row:
            cur["text"] += " ｜ " + t.strip()
        elif prev and (prev["bbox"][2] < col_right[col_of(prev)] - 10 or re.match(r"^\s*[・「]", t) or li["bbox"][0] > prev["bbox"][0] + 20):
            cur["text"] += "\n" + t.strip()
        else:
            cur["text"] = join_lines(cur["text"], t.strip())
        prev = li

    # ---------- figures & tables ----------
    caps = [li for li in lines if li["cls"] == "caption"]
    figtext = [li for li in lines if li["cls"] == "figtext"]
    imgs = [pymupdf.Rect(b["bbox"]) for b in d["blocks"] if b["type"] == 1]
    draws = [dr["rect"] for dr in page.get_drawings() if dr["rect"].width * dr["rect"].height > 4 or dr["rect"].width > 20 or dr["rect"].height > 20]
    elems = [("cap", pymupdf.Rect(c["bbox"]), c) for c in caps] + [("txt", pymupdf.Rect(t["bbox"]), t) for t in figtext] \
        + [("img", r, None) for r in imgs] + [("drw", r, None) for r in draws]
    groups = cluster([e[1] for e in elems]) if elems else []
    objs = []
    for g in groups:
        members = [elems[i] for i in g]
        kinds = {m[0] for m in members}
        if not ({"cap", "img"} & kinds) and not ("txt" in kinds and "drw" in kinds):
            continue                            # decoration only
        if kinds == {"drw"}:
            continue
        gcaps = [m for m in members if m[0] == "cap"]
        if len(gcaps) <= 1:
            objs.append({"caps": [m[2] for m in gcaps], "members": members})
        else:                                   # split a group that holds several captions
            parts = {id(c[2]): {"caps": [c[2]], "members": [c]} for c in gcaps}
            for m in members:
                if m[0] == "cap":
                    continue
                def dist(c):
                    cr = pymupdf.Rect(c[2]["bbox"])
                    is_tab = c[2]["text"].lstrip().startswith("表")
                    dy = (m[1].y0 - cr.y1) if is_tab else (cr.y0 - m[1].y1)
                    dx = max(0, cr.x0 - m[1].x1, m[1].x0 - cr.x1)
                    return (0 if dy > -5 else 1, abs(dy) + dx)
                parts[id(min(gcaps, key=dist)[2])]["members"].append(m)
            objs.extend(parts.values())
    # objects without caption: attach to the nearest captioned object on the page
    capped = [o for o in objs if o["caps"]]
    for o in [o for o in objs if not o["caps"]]:
        if capped:
            r = pymupdf.Rect(o["members"][0][1])
            for m in o["members"]:
                r |= m[1]
            near = min(capped, key=lambda c: min(abs(r.y0 - m[1].y1) + abs(r.x0 - m[1].x0) for m in c["members"]))
            near["members"].extend(o["members"])
        else:
            capped.append(o)
    for o in capped:
        region = None
        for m in o["members"]:
            region = m[1] if region is None else region | m[1]
        region = inflate(region, 4) & page.rect
        cap_text = " ".join(c["text"].strip() for c in sorted(o["caps"], key=lambda c: c["bbox"][1]))
        kind = "tab" if cap_text.startswith("表") else "fig"
        num = re.match(r"^(図|表)\s*([\d.]+)", cap_text)
        label = (num.group(1) + num.group(2)) if num else f"p{pno}-{len(blocks)}"
        # text lines inside the region that are not the caption: caption sub-lines vs in-figure text
        cap_rects = [pymupdf.Rect(c["bbox"]) for c in o["caps"]]
        sub, inner = [], []
        for row in rows([m[2] for m in o["members"] if m[0] == "txt"]):
            near_cap = any(0 <= row["bbox"][1] - cr.y1 < 30 and row["bbox"][0] < cr.x1 + 30 for cr in cap_rects) and row["size"] < 8
            (sub if near_cap and kind == "fig" else inner).append(row["text"])
        fname = f"{label.replace('.', '_')}.png"
        page.get_pixmap(dpi=200, clip=region).save(IMG_DIR / fname)
        new_block(type=kind, page=pno, label=label, caption=cap_text, caption_sub="\n".join(sub),
                  figtext=inner, image=f"img/{fname}", bbox=[round(v, 1) for v in region],
                  has_raster=any(m[0] == "img" for m in o["members"]))
    open_para_page_end = open_para

# split reference items that PyMuPDF glued onto one line ("…参照）18） 巽典之…")
fixed = []
for b in blocks:
    if b["type"] != "ref":
        fixed.append(b); continue
    m0 = re.match(r"^(\d+)", b["text"])
    pieces, rest, n = [], b["text"], int(m0.group(1)) if m0 else None
    while n is not None:
        m = re.search(r"(?<=[）)。])\s*(?=%d\s*[）)])" % (n + 1), rest)
        if not m:
            break
        pieces.append(rest[:m.start()]); rest = rest[m.end():]; n += 1
    pieces.append(rest)
    for i, t in enumerate(pieces):
        fixed.append({**b, "text": t.strip()} if i == 0 else {**b, "id": b["id"] + f"_{i}", "text": t.strip()})
blocks[:] = fixed

# ---------- merge headings that wrapped onto a second line (ids of the remaining blocks stay unchanged) ----------
merged = []
for b in blocks:
    prev = merged[-1] if merged else None
    if prev and b["type"] == prev["type"] and b["type"] in ("h1", "h2", "h3") and b["page"] == prev["page"] \
            and 0 < b["bbox"][1] - prev["bbox"][3] < 12 and not re.match(r"^\d+(\.\d+)*[\s　｜]", b["text"]):
        prev["text"] += b["text"]
        prev["bbox"] = [min(prev["bbox"][0], b["bbox"][0]), prev["bbox"][1], max(prev["bbox"][2], b["bbox"][2]), b["bbox"][3]]
        prev.setdefault("merged_ids", []).append(b["id"])
        continue
    merged.append(b)
blocks[:] = merged

# ---------- appendix pages: whole-page objects ----------
for pno in range(APPENDIX_FROM, doc.page_count + 1):
    page = doc[pno - 1]
    rotated = sum(1 for b in page.get_text("dict")["blocks"] if b["type"] == 0 for l in b["lines"] if l["dir"] != (1.0, 0.0)) > 20
    pix = page.get_pixmap(dpi=200, clip=pymupdf.Rect(0, 0, page.rect.width, 795))
    fname = f"appendix_p{pno}.png"
    pix.save(IMG_DIR / fname)
    if rotated:                              # store a landscape copy for reading
        from PIL import Image
        Image.open(IMG_DIR / fname).rotate(-90, expand=True).save(IMG_DIR / fname)
    text = norm(page.get_text())
    new_block(type="appendix", page=pno, image=f"img/{fname}", rotated=rotated, raw_text=text)

json.dump(blocks, open(OUT / "blocks.json", "w"), ensure_ascii=False, indent=1)
from collections import Counter
print(Counter(b["type"] for b in blocks))
