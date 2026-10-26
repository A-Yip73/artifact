"""Step 5: build the bilingual (ja/zh) reading edition as one self-contained HTML file.

usage: build_html.py OUT.html [--pages 1,5,14] [--intro intro.txt]
"""
import argparse, base64, csv, html, io, json, re
from pathlib import Path
from PIL import Image
import sys
sys.path.insert(0, str(Path(__file__).parent))
from glossary_util import zh_variants

WORK = Path(__file__).resolve().parent.parent
ap = argparse.ArgumentParser()
ap.add_argument("out")
ap.add_argument("--pages", default="")
ap.add_argument("--intro", default="")
ap.add_argument("--title-note", default="")
args = ap.parse_args()
pages = {int(p) for p in args.pages.split(",") if p}

blocks = json.load(open(WORK / "blocks.json"))
tr = json.load(open(WORK / "translations.json")) if (WORK / "translations.json").exists() else {}
figs = json.load(open(WORK / "figures.json")) if (WORK / "figures.json").exists() else {}
doubts = json.load(open(WORK / "doubts.json")) if (WORK / "doubts.json").exists() else {}
for _id, _f in figs.items():
    if _f.get("doubts"):
        doubts[_id] = _f["doubts"]
doubt_cls = json.load(open(WORK / "doubt_classes.json")) if (WORK / "doubt_classes.json").exists() else {}
CLS_NAME = {"A": "原文疑似错误", "B": "原文歧义或信息不足", "C": "译法说明"}
doubt_items = []          # (n, block, problem, suggestion, class)


def doubt_badges(b):
    out = []
    for k, d in enumerate(doubts.get(b["id"], [])):
        c = doubt_cls.get(f'{b["id"]}#{k}', "B")
        n = len(doubt_items) + 1
        doubt_items.append((n, b, d.get("问题", ""), d.get("建议", ""), c))
        if c in ("A", "B"):
            out.append(f'<a class="dq dq{c}" id="dq{n}" href="#d{n}" title="{CLS_NAME[c]}：{esc(d.get("问题", ""))}">疑{n}</a>')
    return "".join(out)
gloss = list(csv.DictReader(open(WORK.parent / "glossary.csv", encoding="utf-8-sig")))
if pages:
    blocks = [b for b in blocks if b["page"] in pages]

esc = html.escape
TERM_TIP = {}
for g in gloss:
    if g["类别"] in ("人名",):
        continue
    for v in zh_variants(g):
        if len(v) >= 3 or re.search(r"[A-Za-z]", v):      # two-character words (固定, 标记 …) give too many false hits
            TERM_TIP.setdefault(v, " · ".join(x for x in (g["日文"].strip(), g["英文"].strip()) if x))
TERM_RE = re.compile("|".join(re.escape(v) for v in sorted(TERM_TIP, key=len, reverse=True))) if TERM_TIP else None
translator_notes = []


def rich(text, zh=False, bid=None):
    """Escape text but keep <sup>/<i>; in Chinese text mark glossary terms (once per block) and 【待核】 flags."""
    parts = re.split(r"(<sup>.*?</sup>|</?i>)", text)
    seen, out = set(), []
    for part in parts:
        if part.startswith("<sup>"):
            out.append("<sup>" + esc(part[5:-6]) + "</sup>"); continue
        if part in ("<i>", "</i>"):
            out.append(part); continue
        s_ = esc(part).replace("\n", "<br>")
        if zh:
            s_ = re.sub(r"【待核：(.*?)】", r'<span class="tbd" title="待核：译名未确定">【待核：\1】</span>', s_)
            if TERM_RE:
                def term(m):
                    t = m.group(0)
                    if t in seen:
                        return t
                    seen.add(t)
                    return f'<span class="term" tabindex="0" data-tip="{esc(TERM_TIP[t])}">{t}</span>'
                chunks = re.split(r'(<span class="tbd".*?</span>)', s_)
                s_ = "".join(c if c.startswith('<span class="tbd"') else TERM_RE.sub(term, c) for c in chunks)
        out.append(s_)
    return "".join(out)


def img_data(path, max_w=1500):
    im = Image.open(WORK / path).convert("RGB")
    from PIL import ImageOps
    box = ImageOps.invert(im.convert("L")).point(lambda v: 255 if v > 18 else 0).getbbox()   # trim white margins
    if box:
        pad = 12
        im = im.crop((max(0, box[0] - pad), max(0, box[1] - pad), min(im.width, box[2] + pad), min(im.height, box[3] + pad)))
    if im.width > max_w:
        im = im.resize((max_w, round(im.height * max_w / im.width)), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=80, optimize=True, progressive=True)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def pg(b):
    ps = b.get("pages") or [b["page"]]
    label = f"p.{ps[0]}" + (f"–{ps[-1]}" if len(ps) > 1 else "")
    return f'<a class="pg" href="#{b["id"]}" title="原 PDF 页码">{label}</a>'


def zh_table(t):
    if not t or not t.get("rows"):
        return ""
    head = "".join(f"<th>{c}</th>" for c in t.get("header") or [])
    rows = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in t["rows"])
    return (f'<div class="ztable" lang="zh-CN"><div class="ztable-label">中文译表</div><div class="scroll"><table>'
            f'{"<thead><tr>" + head + "</tr></thead>" if head else ""}<tbody>{rows}</tbody></table></div></div>')


def figure(b):
    f = figs.get(b["id"], {})
    cap_ja = b.get("caption", "")
    cap_zh = f.get("caption_zh", "")
    if b["type"] == "appendix":
        cap_ja, cap_zh = f"附录 p.{b['page']}", f.get("caption_zh", "")
    labels = f.get("labels") or []
    lab_html = ""
    if labels:
        rows = "".join(f'<tr><td lang="ja">{esc(l.get("ja", ""))}</td><td lang="zh-CN">{rich(l.get("zh", ""))}</td></tr>' for l in labels)
        lab_html = (f'<details class="labels"{" open" if len(labels) <= 14 else ""}><summary>图内文字对照（{len(labels)} 处）</summary>'
                    f'<div class="scroll"><table><thead><tr><th>日文</th><th>中文</th></tr></thead><tbody>{rows}</tbody></table></div></details>')
    sub = ""
    if b.get("caption_sub") or f.get("caption_sub_zh"):
        sub = (f'<div class="pair sub"><div class="ja" lang="ja">{rich(b.get("caption_sub", ""))}</div>'
               f'<div class="zh" lang="zh-CN">{rich(f.get("caption_sub_zh", ""))}</div></div>')
    note = doubt_badges(b)
    note = f'<p class="fignote">{note}</p>' if note else ""
    kind = {"fig": "fig", "tab": "tab", "appendix": "appx"}[b["type"]]
    return (f'<figure class="figure {kind}" id="{b["id"]}">{pg(b)}'
            f'<div class="imgwrap"><img loading="lazy" src="{img_data(b["image"])}" alt="{esc(cap_zh or cap_ja)}"></div>'
            f'<figcaption><div class="pair cap"><div class="ja" lang="ja">{rich(cap_ja)}</div>'
            f'<div class="zh" lang="zh-CN">{rich(cap_zh, zh=True)}</div></div>{sub}</figcaption>'
            f'{zh_table(f.get("table"))}{lab_html}{note}</figure>')


body, toc = [], []
for b in blocks:
    t = b["type"]
    if t in ("fig", "tab", "appendix"):
        body.append(figure(b)); continue
    ja = b["text"]
    zh = tr.get(b["id"])
    if t == "en":
        body.append(f'<div class="row en" id="{b["id"]}">{pg(b)}<div class="full" lang="en"><span class="badge">英文原文 · 未翻译</span>{esc(ja)}</div></div>')
        continue
    if zh is None and t == "ref":
        zh_html = f'<span class="same">{rich(ja)}</span>'
    elif zh is None:
        zh_html = '<span class="missing">〔未翻译〕</span>'
    else:
        zh_html = rich(zh, zh=True, bid=b["id"])
    cls = {"h1": "row h h1", "h2": "row h h2", "h3": "row h h3", "p": "row p", "front": "row p front",
           "note": "row note", "ref": "row ref"}[t]
    if t in ("h1", "h2"):
        toc.append((t, b["id"], zh or ja, ja))
    body.append(f'<div class="{cls}" id="{b["id"]}">{pg(b)}<div class="ja" lang="ja">{rich(ja)}</div>'
                f'<div class="zh" lang="zh-CN">{zh_html}{doubt_badges(b)}</div></div>')

GLOSS_RE = re.compile(r"（[^（）]*[\u3040-\u30ffA-Za-z][^（）]*[/／][^（）]*）")
toc_html = "".join(f'<li class="{lvl}"><a href="#{i}"><span lang="zh-CN">{esc(GLOSS_RE.sub("", re.sub("<[^>]+>", "", z)))}</span>'
                   f'<span class="tocja" lang="ja">{esc(re.sub("<[^>]+>", "", j))}</span></a></li>' for lvl, i, z, j in toc)
sys.path.insert(0, str(Path(__file__).parent))
from glossary_util import ja_variants
all_blocks = json.load(open(WORK / "blocks.json"))
def first_loc(g):
    vs = ja_variants(g)
    for b in all_blocks:
        txt = b.get("text", "") + b.get("caption", "") + " ".join(b.get("figtext", []))
        if any(v in txt for v in vs):
            return f'<a href="#{b["id"]}">p.{b["page"]}</a>' if any(x["id"] == b["id"] for x in blocks) else f"p.{b['page']}"
    return "—"
gl_rows = "".join(f'<tr><td lang="ja">{esc(g["日文"])}</td><td lang="zh-CN">{esc(g["中文"])}</td><td lang="en">{esc(g["英文"])}</td>'
                  f'<td>{esc(g["类别"])}</td><td>{first_loc(g)}</td><td>{esc(g["备注"])}</td></tr>' for g in gloss)
def doubt_rows(cs):
    return "".join(f'<tr id="d{n}"><td>{"<a href=#dq" + str(n) + ">" if c in "AB" else ""}{n}{"</a>" if c in "AB" else ""}</td><td><a href="#{b["id"]}">p.{b["page"]}</a></td>'
                   f'<td lang="ja">{esc(re.sub("<[^>]+>", "", b.get("text") or b.get("caption") or ("附录 p." + str(b["page"])))[:60])}…</td>'
                   f'<td>{esc(q)}</td><td>{esc(a)}</td></tr>' for n, b, q, a, c in doubt_items if c in cs)


DTH = '<thead><tr><th>#</th><th>位置</th><th>原文</th><th>问题</th><th>建议</th></tr></thead>'
nA, nB, nC = (sum(1 for x in doubt_items if x[4] == k) for k in "ABC")
tn_rows = (f'<h3>原文疑似错误（{nA} 条）</h3><div class="scroll"><table>{DTH}<tbody>{doubt_rows("A")}</tbody></table></div>'
           f'<h3>原文歧义或信息不足（{nB} 条）</h3><div class="scroll"><table>{DTH}<tbody>{doubt_rows("B")}</tbody></table></div>'
           f'<details><summary>译法说明（{nC} 条，非原文问题）</summary><div class="scroll"><table>{DTH}<tbody>{doubt_rows("C")}</tbody></table></div></details>')
qa_html = ""
if (WORK / "qa_summary.html").exists():
    qa_html = (WORK / "qa_summary.html").read_text()
intro = Path(args.intro).read_text().strip() if args.intro else ""
title_zh = re.sub(r"<[^>]+>", "", tr.get(blocks[0]["id"], "临床检验技术的系统化调查")) if blocks and blocks[0]["page"] == 1 else "临床检验技术的系统化调查"

CSS = r"""
:root{--paper:#fbfaf6;--panel:#ffffff;--ink:#1f2328;--muted:#646a73;--faint:#e6e3dc;--rule:#d9d5cc;
--accent:#0b6b6f;--accent-soft:#e3f1f0;--note:#f6f1e3;--note-rule:#e2d4ad;--tn:#a14b16;--shadow:0 1px 2px rgba(0,0,0,.05);
--ja:"Noto Serif JP","Noto Serif CJK JP","Source Han Serif JP","Hiragino Mincho ProN","Yu Mincho",serif;
--zh:"Noto Serif SC","Noto Serif CJK SC","Source Han Serif SC","Songti SC","STSong","SimSun",serif;
--sans:"Noto Sans SC","Noto Sans CJK SC","PingFang SC","Hiragino Sans GB","Microsoft YaHei",system-ui,sans-serif;}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--paper:#15171a;--panel:#1c1f23;--ink:#e7e5e0;--muted:#9aa0a8;
--faint:#2a2e33;--rule:#353a40;--accent:#5cc3c0;--accent-soft:#1b3332;--note:#26231b;--note-rule:#4a4127;--tn:#f0a571;--shadow:none}}
:root[data-theme="dark"]{--paper:#15171a;--panel:#1c1f23;--ink:#e7e5e0;--muted:#9aa0a8;--faint:#2a2e33;--rule:#353a40;
--accent:#5cc3c0;--accent-soft:#1b3332;--note:#26231b;--note-rule:#4a4127;--tn:#f0a571;--shadow:none}
*{box-sizing:border-box}
html{scroll-padding-top:64px}
body{margin:0;background:var(--paper);color:var(--ink);font:17px/1.8 var(--zh);-webkit-text-size-adjust:100%}
[lang="ja"]{font-family:var(--ja)} [lang="zh-CN"]{font-family:var(--zh)} [lang="en"]{font-family:Georgia,"Times New Roman",serif}
a{color:var(--accent)}
.wrap{max-width:1240px;margin:0 auto;padding:0 28px}
header.mast{padding:56px 0 28px;border-bottom:1px solid var(--rule)}
.kicker{font:600 13px/1 var(--sans);letter-spacing:.16em;color:var(--accent);text-transform:uppercase}
.mast h1{font:700 40px/1.25 var(--sans);margin:14px 0 6px}
.mast .orig{font-size:20px;color:var(--muted);margin:0}
.meta{display:grid;grid-template-columns:max-content 1fr;gap:4px 18px;margin:22px 0 0;font:14px/1.6 var(--sans);color:var(--muted)}
.meta dt{font-weight:600;color:var(--ink)} .meta dd{margin:0}
.bar{position:sticky;top:0;z-index:10;background:color-mix(in srgb,var(--paper) 92%,transparent);backdrop-filter:blur(6px);border-bottom:1px solid var(--rule)}
.bar .wrap{display:flex;gap:12px;align-items:center;min-height:52px;flex-wrap:wrap;font:14px var(--sans)}
.seg{display:inline-flex;border:1px solid var(--rule);border-radius:8px;overflow:hidden}
.seg button{font:inherit;padding:6px 14px;border:0;background:transparent;color:var(--ink);cursor:pointer}
.seg button+button{border-left:1px solid var(--rule)}
.seg button[aria-pressed="true"]{background:var(--accent);color:#fff}
.bar .spacer{flex:1} .bar a{text-decoration:none;font-weight:600}
section.box{background:var(--panel);border:1px solid var(--rule);border-radius:12px;padding:22px 26px;margin:28px 0;box-shadow:var(--shadow)}
section.box h2{font:700 18px/1.4 var(--sans);margin:0 0 10px}
.guide p{margin:.4em 0}
.guide .tag{display:inline-block;font:600 12px var(--sans);color:var(--accent);background:var(--accent-soft);border-radius:4px;padding:2px 8px;margin-bottom:6px}
.toc ol{list-style:none;padding:0;margin:0;columns:2;column-gap:40px}
.toc li{break-inside:avoid;margin:2px 0} .toc li.h2{padding-left:1.4em;font-size:15px}
.toc a{text-decoration:none;color:var(--ink)} .toc a:hover span:first-child{color:var(--accent)}
.toc .tocja{display:block;font-size:12px;color:var(--muted);line-height:1.4}
main{padding:12px 0 40px}
.row{display:grid;grid-template-columns:1fr 1fr;column-gap:44px;position:relative;padding:6px 0}
.row .ja{color:var(--muted);font-size:15.5px;line-height:1.85}
.row .zh{font-size:17px;line-height:1.85}
.pg{position:absolute;left:-58px;top:10px;font:12px var(--sans);color:var(--muted);text-decoration:none;opacity:.75}
.pg:hover{color:var(--accent);opacity:1}
.row.p .ja,.row.p .zh{text-align:justify}
.row.h{padding-top:30px}
.row.h1{border-top:2px solid var(--ink);margin-top:36px;padding-top:22px}
.row.h1 .zh,.row.h1 .ja{font:700 28px/1.35 var(--sans)} .row.h1 .ja{font-family:var(--ja);font-size:22px}
.row.h2 .zh{font:700 21px/1.4 var(--sans)} .row.h2 .ja{font:600 17px/1.4 var(--ja)}
.row.h3 .zh{font:700 18px/1.4 var(--sans)} .row.h3 .ja{font:600 15.5px/1.4 var(--ja)}
.row.note{background:var(--note);border-left:3px solid var(--note-rule);border-radius:6px;padding:12px 16px;margin:10px 0}
.row.note .ja,.row.note .zh{font-size:15px}
.row.note .pg{left:-74px}
.row.ref .ja,.row.ref .zh{font-size:14px;line-height:1.6}
.row.ref .zh{color:var(--muted)} .row.ref{padding:2px 0}
.row.en .full{grid-column:1/-1;font-size:15.5px;color:var(--muted)}
.badge{display:inline-block;font:600 11px var(--sans);color:var(--muted);border:1px solid var(--rule);border-radius:4px;padding:0 6px;margin-right:8px}
.same{color:var(--muted)} .missing{color:var(--tn);font:13px var(--sans)}
sup{font-size:.65em;line-height:0}
.term{border-bottom:1px dotted color-mix(in srgb,var(--accent) 70%,transparent);position:relative;cursor:help}
.term:hover::after,.term:focus::after{content:attr(data-tip);position:absolute;left:0;top:100%;z-index:5;white-space:nowrap;
font:13px/1.5 var(--ja);background:var(--ink);color:var(--paper);padding:4px 8px;border-radius:6px;margin-top:4px}
.tn{color:var(--tn);font-size:.86em}
.tbd{color:var(--tn);background:color-mix(in srgb,var(--tn) 10%,transparent);border-radius:3px;padding:0 2px}
.dq{display:inline-block;margin-left:6px;font:600 11px/1.6 var(--sans);color:var(--tn);border:1px solid color-mix(in srgb,var(--tn) 45%,transparent);border-radius:4px;padding:0 5px;text-decoration:none;vertical-align:2px}
.hint{font:14px/1.6 var(--sans);color:var(--muted);margin:0 0 10px}
#notes h3{font:700 15px var(--sans);margin:18px 0 8px} #notes details summary{font:600 14px var(--sans);color:var(--accent);margin-top:18px;cursor:pointer}
.dqB{color:var(--muted);border-color:var(--rule)}
#notes td:nth-child(3){color:var(--muted);font-size:13px}
figure.figure{margin:22px 0;padding:18px;background:var(--panel);border:1px solid var(--rule);border-radius:12px;position:relative;box-shadow:var(--shadow)}
figure .pg{left:-58px}
.imgwrap{text-align:center;background:#fff;border-radius:6px;padding:6px}
.imgwrap img{max-width:100%;max-height:78vh;height:auto}
figure.appx .imgwrap img{max-height:none}
.pair{display:grid;grid-template-columns:1fr 1fr;column-gap:44px;margin-top:10px}
.pair.cap .ja,.pair.cap .zh{font:600 15px/1.6 var(--sans)} .pair.cap .ja{font-family:var(--ja);color:var(--muted)}
.pair.sub .ja,.pair.sub .zh{font-size:14px;line-height:1.6;color:var(--muted)}
.scroll{overflow-x:auto}
table{border-collapse:collapse;width:100%;font:14px/1.55 var(--zh)}
th,td{border:1px solid var(--rule);padding:6px 9px;vertical-align:top;text-align:left;overflow-wrap:anywhere}
thead th{background:var(--accent-soft);font-family:var(--sans)}
.ztable{margin-top:14px} .ztable-label,.labels summary{font:600 13px var(--sans);color:var(--accent);margin-bottom:6px;cursor:pointer}
.labels{margin-top:12px} .labels td:first-child{color:var(--muted);width:45%}
.fignote{font-size:14px;color:var(--tn);margin:10px 0 0}
#glossary td:nth-child(6){font-size:13px;color:var(--muted)}
#notes li{margin:4px 0;font-size:15px}
footer{border-top:1px solid var(--rule);padding:24px 0 60px;font:13px/1.7 var(--sans);color:var(--muted)}
body[data-mode="zh"] .ja, body[data-mode="ja"] .zh{display:none}
body[data-mode="zh"] .row, body[data-mode="ja"] .row, body[data-mode="zh"] .pair, body[data-mode="ja"] .pair{grid-template-columns:1fr}
body[data-mode="zh"] main, body[data-mode="ja"] main{max-width:820px;margin:0 auto}
body[data-mode="ja"] .ztable{display:none}
body[data-mode="ja"] .row .ja{color:var(--ink);font-size:17px}
@media (max-width:1360px){.pg,figure .pg,.row.note .pg{position:static;display:inline-block;margin-bottom:2px;grid-column:1/-1}}
@media (max-width:760px){.wrap{padding:0 16px}.mast h1{font-size:28px}.row,.pair{grid-template-columns:1fr;row-gap:4px}
.row .ja{font-size:14.5px}.toc ol{columns:1}.bar .wrap{gap:8px;flex-wrap:nowrap}.bar .lbl{display:none}.bar a{font-size:13px}.seg button{padding:6px 10px}.meta{grid-template-columns:1fr}.meta dt{margin-top:6px}
figure.figure{padding:10px}}
@media print{@page{size:A4 landscape;margin:12mm 14mm}
body{font-size:10.5pt;background:#fff;color:#000}.bar,.toc{display:none}.wrap{max-width:none;padding:0}
.row,.pair,tr,.imgwrap,figcaption{break-inside:avoid}figure.figure{break-inside:auto}.imgwrap{break-after:avoid}.row.h{break-after:avoid}.pg{position:static;display:inline-block;font-size:8pt}
.row .ja{font-size:9.5pt}.row .zh{font-size:10.5pt}figure.figure{box-shadow:none}.imgwrap img{max-height:120mm}figure.appx .imgwrap img{max-height:170mm}
details>summary{list-style:none}.term{border:0}.term::after{display:none}section.box{box-shadow:none}}
"""

JS = r"""
(function(){var b=document.body,btns=document.querySelectorAll('.seg button');
function set(m){b.setAttribute('data-mode',m);btns.forEach(function(x){x.setAttribute('aria-pressed',x.dataset.mode===m)});
try{localStorage.setItem('bilingual-mode',m)}catch(e){}}
btns.forEach(function(x){x.addEventListener('click',function(){set(x.dataset.mode)})});
var m='both';try{m=localStorage.getItem('bilingual-mode')||'both'}catch(e){} if(window.innerWidth<760&&!localStorage) m='both'; set(m);
window.addEventListener('beforeprint',function(){document.querySelectorAll('details').forEach(function(d){d.open=true})});})();
"""

doc = f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>临床检验技术系统化调查 · 中日对照</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Noto+Sans+SC:wght@400;600;700&family=Noto+Serif+JP:wght@400;600&family=Noto+Serif+SC:wght@400;600&display=swap" rel="stylesheet">
<style>{CSS}</style>
</head>
<body data-mode="both">
<header class="mast"><div class="wrap">
<div class="kicker">中日对照精读版{(' · ' + esc(args.title_note)) if args.title_note else ''}</div>
<h1 lang="zh-CN">{esc(title_zh)}</h1>
<p class="orig" lang="ja">臨床検査技術の系統化調査 — Study on the Lineage of Clinical Examination Technology</p>
<dl class="meta">
<dt>作者</dt><dd>伊藤道雄（国立科学博物馆 产业技术史资料信息中心 主任调查员）</dd>
<dt>出处</dt><dd>国立科学博物馆《技术系统化调查报告》第 24 集（2017 年 3 月）；本译本依据 2017 年 8 月全文 PDF 修订版</dd>
<dt>原文</dt><dd>103 页（日文，含英文摘要）</dd>
<dt>翻译</dt><dd>2026 年 10 月，Claude（经 Anthropic API）机器翻译，按术语表统一译名；未经人工逐句审校</dd>
</dl></div></header>
<nav class="bar"><div class="wrap"><span class="lbl">显示</span>
<div class="seg" role="group" aria-label="显示模式"><button data-mode="both">对照</button><button data-mode="zh">仅中文</button><button data-mode="ja">仅日文</button></div>
<span class="spacer"></span><a href="#toc">目录</a><a href="#glossary">术语表</a><a href="#notes">疑点</a></div></nav>
<div class="wrap">
{f'<section class="box guide"><span class="tag">译者导读 · 非原文</span>{"".join(f"<p>{esc(p)}</p>" for p in intro.splitlines() if p.strip())}</section>' if intro else ''}
<section class="box toc" id="toc"><h2>目录</h2><ol>{toc_html}</ol></section>
<main>
{''.join(body)}
</main>
<section class="box" id="glossary"><h2>术语表（{len(gloss)} 条）</h2><div class="scroll"><table>
<thead><tr><th>日文</th><th>中文</th><th>英文</th><th>类别</th><th>首次出现</th><th>备注</th></tr></thead><tbody>{gl_rows}</tbody></table></div></section>
<section class="box" id="notes"><h2>疑点清单（{len(doubt_items)} 条）</h2><p class="hint">译文照原文翻译，不擅自修正。正文中的「疑N」标记对应下表；涉及史实的判断来自译者（Claude）的背景知识，只作核对线索，引用前请查证。</p>
{tn_rows}</section>
{qa_html}
<footer>译文由 Claude 根据原文逐段翻译，供学习和内部参考。原文著作权归国立科学博物馆及作者所有。左侧 p.N 为原 PDF 页码；带虚线下划线的词悬停可看日文原词和英文；「疑N」链接到疑点清单；【待核】表示译名尚待核实。</footer>
</div>
<script>{JS}</script>
</body></html>"""
Path(args.out).write_text(doc)
print("wrote", args.out, f"{len(doc) / 1e6:.1f} MB", "translator notes:", len(translator_notes))
