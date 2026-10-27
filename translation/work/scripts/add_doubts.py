"""Step 4f: put the doubt list into the layout PDFs.

Margin markers 「疑N」 next to each paragraph / figure that has a source-text doubt (classes A and B), a
doubt list appended at the end, and two-way links (marker → list entry, list entry → page).
Numbers are the same as in the HTML reading edition.

usage: add_doubts.py ZH_IN.pdf SOURCE.pdf OUT_ZH.pdf OUT_DUAL.pdf
"""
import html, json, re, subprocess, sys, tempfile
from pathlib import Path
import pymupdf

sys.path.insert(0, str(Path(__file__).parent))
WORK = Path(__file__).resolve().parent.parent
zh_in, src_path, out_zh, out_dual = sys.argv[1:5]

blocks = json.load(open(WORK / "blocks.json"))
doubts = json.load(open(WORK / "doubts.json"))
for i, f in json.load(open(WORK / "figures.json")).items():
    if f.get("doubts"):
        doubts[i] = f["doubts"]
cls = json.load(open(WORK / "doubt_classes.json"))
CLS = {"A": "原文疑似错误", "B": "歧义或信息不足"}

# ---- numbering identical to build_html.py (all classes counted, document order)
items, n = [], 0
for b in blocks:
    for k, d in enumerate(doubts.get(b["id"], [])):
        n += 1
        c = cls.get(f'{b["id"]}#{k}', "B")
        if c in CLS:
            items.append({"n": n, "block": b, "cls": c, "q": d.get("问题", ""), "a": d.get("建议", "")})

# ---- 1. margin markers on the Chinese pages
from fontTools import subset as ft_subset
FONT = str(WORK / "fonts" / "doubt_subset.otf")
_o = ft_subset.Options(); _o.name_IDs = ["*"]; _o.retain_gids = True; _o.notdef_outline = True
_f = ft_subset.load_font(str(WORK / "fonts" / "NotoSansCJKsc-Regular.otf"), _o)
_s = ft_subset.Subsetter(_o); _s.populate(text="疑0123456789·"); _s.subset(_f); ft_subset.save_font(_f, FONT, _o)
COLOUR = (0.62, 0.10, 0.04)
FS = 6.5
zh = pymupdf.open(zh_in)
measure = pymupdf.Font(fontfile=FONT)
by_block = {}
for it in items:
    by_block.setdefault(it["block"]["id"], []).append(it)
markers = []                      # (page index, rect, [numbers])
used = {}                         # (page, side) -> list of occupied y ranges
for bid, its in by_block.items():
    b = its[0]["block"]
    pno = b["page"] - 1
    page = zh[pno]
    W = page.rect.width
    left_edge, right_edge = (59.5, 529.0) if b["page"] % 2 else (70.9, 540.6)
    x0, y0, x1, y1 = b.get("bbox", [0, 60, 0, 60])
    if b["type"] == "appendix":
        side, y = "right", 60.0
    elif b["type"] in ("fig", "tab"):
        side, y = "right", y0
    else:
        side, y = ("left" if x0 < W / 2 - 20 else "right"), y0
    nums = [it["n"] for it in its]
    labels = ["疑" + "·".join(str(v) for v in nums[k:k + 2]) for k in range(0, len(nums), 2)]   # two numbers per line
    h = len(labels) * FS * 1.25
    occ = used.setdefault((pno, side), [])
    while any(not (y + h < a or y > c) for a, c in occ):              # avoid overlapping another marker
        y += 3
    occ.append((y, y + h))
    page.insert_font(fontname="dq", fontfile=FONT)
    rects = []
    for k, lab in enumerate(labels):
        w = measure.text_length(lab, fontsize=FS)
        x = left_edge - 5 - w if side == "left" else right_edge + 5
        x = max(4, min(W - w - 4, x))
        base = y + FS + k * FS * 1.25
        page.insert_text((x, base), lab, fontname="dq", fontfile=FONT, fontsize=FS, color=COLOUR)
        rects.append(pymupdf.Rect(x - 1, base - FS, x + w + 1, base + 2))
        markers.append((pno, rects[-1], nums[k * 2:k * 2 + 2]))
zh_marked = tempfile.mktemp(suffix=".pdf")
zh.save(zh_marked, garbage=3, deflate=True)

# ---- 2. the list as A4 pages (Chromium prints CJK cleanly)
def esc(s):
    return html.escape(re.sub(r"<[^>]+>", "", str(s)))


rows = []
for it in items:
    b = it["block"]
    src = b.get("text") or b.get("caption") or f'附录 p.{b["page"]}'
    rows.append(f'<tr><td class="n">【疑{it["n"]}】</td><td class="p">p.{b["page"]}</td><td class="c c{it["cls"]}">{CLS[it["cls"]]}</td>'
                f'<td class="ja">{esc(src)[:70]}…</td><td>{esc(it["q"])}</td><td>{esc(it["a"])}</td></tr>')
doc_html = f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><style>
@page {{ size: A4; margin: 14mm 12mm 14mm 12mm; }}
body {{ font: 8.6pt/1.5 "Noto Sans CJK SC","Noto Sans SC",sans-serif; color:#1f2328; }}
h1 {{ font-size: 15pt; margin: 0 0 4pt; }} p.note {{ color:#555; margin: 0 0 8pt; }}
table {{ border-collapse: collapse; width: 100%; }} th, td {{ border: 0.5pt solid #bbb; padding: 3pt 4pt; vertical-align: top; text-align: left; overflow-wrap:anywhere; }}
th {{ background: #e3f1f0; }} tr {{ break-inside: avoid; }} thead {{ display: table-header-group; }}
td.n {{ white-space: nowrap; color: #9e1a0a; font-weight: 700; }} td.p {{ white-space: nowrap; color:#0b6b6f; }}
td.c {{ white-space: nowrap; }} td.cA {{ color:#9e1a0a; }} td.ja {{ color:#666; font-family:"Noto Sans CJK JP",sans-serif; font-size:7.6pt; width: 22%; }}
</style></head><body>
<h1>疑点清单</h1>
<p class="note">正文照原文翻译，不擅自修正。下列 {len(items)} 条为原文疑似错误或歧义、信息不足之处，编号与页边的「疑N」及 HTML 精读版一致（译法说明类未列入）。
在电脑上阅读时，点击页边的「疑N」可跳到本表对应条目，点击本表的编号或页码可跳回原页。涉及史实的判断来自译者（Claude）的背景知识，只作核对线索，引用前请查证。</p>
<table><thead><tr><th>编号</th><th>位置</th><th>类别</th><th>原文摘录</th><th>问题</th><th>建议</th></tr></thead><tbody>{''.join(rows)}</tbody></table>
</body></html>"""
tmp_html = tempfile.mktemp(suffix=".html"); Path(tmp_html).write_text(doc_html)
list_pdf = tempfile.mktemp(suffix=".pdf")
js = f"""const {{ chromium }} = require('playwright');
(async () => {{ const b = await chromium.launch(); const p = await b.newPage();
await p.goto('file://{tmp_html}'); await p.pdf({{ path: '{list_pdf}', format: 'A4', printBackground: true,
  displayHeaderFooter: true, headerTemplate: '<span></span>',
  footerTemplate: '<div style="font-size:7px;width:100%;text-align:center;color:#888">疑点清单 <span class="pageNumber"></span> / <span class="totalPages"></span></div>',
  margin: {{ top: '14mm', bottom: '16mm', left: '12mm', right: '12mm' }} }}); await b.close(); }})();"""
subprocess.run(["node", "-e", js], check=True)
lst = pymupdf.open(list_pdf)

# where each entry landed in the list: (list page, rect of 【疑N】, rect of p.N)
where = {}
for lp, page in enumerate(lst):
    for it in items:
        if it["n"] in where:
            continue
        hits = page.search_for(f'【疑{it["n"]}】')
        if hits:
            r = hits[0]
            prs = [q for q in page.search_for(f'p.{it["block"]["page"]}') if abs(q.y0 - r.y0) < 3]
            where[it["n"]] = (lp, r, prs[0] if prs else None)
missing = [it["n"] for it in items if it["n"] not in where]
if missing:
    print("WARNING: list entries not located:", missing[:10])


# ---- 3. assemble both outputs and add the links
def build(base_doc, x_offset, out):
    first_list = base_doc.page_count
    base_doc.insert_pdf(lst)
    marker_pos = {}
    for pno, rect, nums in markers:
        r = rect + (x_offset, 0, x_offset, 0)
        for v in nums:
            marker_pos.setdefault(v, (pno, r))
        if nums[0] in where:
            lp, lr, _ = where[nums[0]]
            base_doc[pno].insert_link({"kind": pymupdf.LINK_GOTO, "from": r, "page": first_list + lp,
                                       "to": pymupdf.Point(0, max(0, lr.y0 - 20))})
    for v, (lp, lr, pr) in where.items():
        if v not in marker_pos:
            continue
        pno, r = marker_pos[v]
        target = {"kind": pymupdf.LINK_GOTO, "page": pno, "to": pymupdf.Point(0, max(0, r.y0 - 40))}
        for src_rect in [lr] + ([pr] if pr else []):
            base_doc[first_list + lp].insert_link(dict(target, **{"from": src_rect}))
    base_doc.save(out, garbage=3, deflate=True)
    print("wrote", out, base_doc.page_count, "pages")


build(pymupdf.open(zh_marked), 0, out_zh)

src = pymupdf.open(src_path)
marked = pymupdf.open(zh_marked)
dual = pymupdf.open()
for i in range(src.page_count):
    w, h = src[i].rect.width, src[i].rect.height
    pg = dual.new_page(width=2 * w, height=h)
    pg.show_pdf_page(pymupdf.Rect(0, 0, w, h), src, i)
    pg.show_pdf_page(pymupdf.Rect(w, 0, 2 * w, h), marked, i)
build(dual, src[0].rect.width, out_dual)
print("markers:", len(markers), "entries:", len(items), "pages with markers:", len({m[0] for m in markers}))
