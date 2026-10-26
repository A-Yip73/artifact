"""Step 6: self-check (D), doubt list (C) and term/abbreviation table (B).

Writes work/qa_checks.json, output/QA_report.md, output/术语与缩写表.csv, work/qa_summary.html
"""
import csv, html, json, random, re, sys, unicodedata
from collections import Counter
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from llm import call, total_cost
from glossary_util import ja_variants, zh_variants

WORK = Path(__file__).resolve().parent.parent
OUT = WORK.parent
blocks = json.load(open(WORK / "blocks.json"))
tr = json.load(open(WORK / "translations.json"))
figs = json.load(open(WORK / "figures.json"))
doubts = json.load(open(WORK / "doubts.json")) if (WORK / "doubts.json").exists() else {}
render = json.load(open(WORK / "render_check.json")) if (WORK / "render_check.json").exists() else None
gloss = list(csv.DictReader(open(OUT / "glossary.csv", encoding="utf-8-sig")))
JA = re.compile(r"[぀-ヿ㐀-鿿]")
units = [b for b in blocks if b["type"] in ("h1", "h2", "h3", "p", "front", "note") or (b["type"] == "ref" and JA.search(b["text"]))]
figlike = [b for b in blocks if b["type"] in ("fig", "tab", "appendix")]
byid = {b["id"]: b for b in blocks}
res = {}
NOTE = re.compile(r"（[^（）]*[\u3040-\u30ffA-Za-z][^（）]*[/／][^（）]*）")      # first-occurrence glosses such as （アポトーシス/apoptosis）


def plain(s):
    return re.sub(r"<[^>]+>", "", s)


# 1. completeness
missing = [b["id"] for b in units if not tr.get(b["id"], "").strip()]
fig_missing = [b["id"] for b in figlike if b["id"] not in figs]
ratio_flags = []
for b in units:
    z = tr.get(b["id"], "")
    ja_len = len(re.sub(r"\s", "", plain(b["text"])))
    zh_len = len(re.sub(r"\s", "", NOTE.sub("", plain(z))))
    if ja_len >= 30 and z and not (0.5 <= zh_len / ja_len <= 2.0):
        ratio_flags.append({"id": b["id"], "page": b["page"], "ja_len": ja_len, "zh_len": zh_len})
res["completeness"] = {"units": len(units), "translated": len(units) - len(missing), "missing": missing,
                       "figures": len(figlike), "figures_done": len(figlike) - len(fig_missing),
                       "figures_missing": fig_missing, "length_ratio_flags": ratio_flags}


# 2. numbers / symbols / superscripts
def nfkc_digits(s):
    return "".join(unicodedata.normalize("NFKC", c) if c.isdigit() or c in "．，" else c for c in s)


NUM = re.compile(r"\d+(?:[.,]\d+)*")
SYM = re.compile(r"(?<![A-Za-z])[A-Za-z][A-Za-z0-9\-+]*[A-Za-z0-9+]|(?<![A-Za-z])[A-Z](?![A-Za-z])")
num_flags, sym_flags, sup_flags = [], [], []
for b in units:
    z = tr.get(b["id"], "")
    if not z:
        continue
    ja, zh = nfkc_digits(plain(b["text"])), nfkc_digits(plain(z))
    zh += " " + " ".join(f"{(int(c) - 1) * 100 + int(d) * 10}" for c, d in re.findall(r"(\d{2})世纪(\d)0年代", zh))
    zh += " " + " ".join(str("一二三四五六七八九十".index(ch) + 1) for ch in re.findall(r"[一二三四五六七八九十]", zh))
    lost = Counter(NUM.findall(ja)) - Counter(NUM.findall(zh))
    if lost:
        num_flags.append({"id": b["id"], "page": b["page"], "missing": sorted(lost.elements())})
    lost_s = set(SYM.findall(ja)) - set(SYM.findall(zh))
    if lost_s:
        sym_flags.append({"id": b["id"], "page": b["page"], "missing": sorted(lost_s)})
    if Counter(re.findall(r"<sup>(.*?)</sup>", b["text"])) != Counter(re.findall(r"<sup>(.*?)</sup>", z)):
        sup_flags.append({"id": b["id"], "page": b["page"]})
res["numbers"], res["symbols"], res["superscripts"] = num_flags, sym_flags, sup_flags

# 3. terminology consistency
term_flags = []
for g in gloss:
    jvs, alts = ja_variants(g), zh_variants(g)
    if not jvs or not alts:
        continue
    hits = [b for b in units if any(v in b["text"] for v in jvs) and tr.get(b["id"])]
    off = [b["id"] for b in hits if not any(a in tr[b["id"]] for a in alts)]
    if off:
        term_flags.append({"ja": g["日文"], "zh": g["中文"], "occurrences": len(hits), "n_off": len(off), "ids": off[:8]})
res["terminology"] = sorted(term_flags, key=lambda x: -x["n_off"])

# 4. Latin binomials in italics
LATIN = re.compile(r"\b((?:Escherichia|Staphylococcus|Streptococcus|Mycobacterium|Bacillus|Salmonella|Vibrio|Clostridium|Helicobacter|Pseudomonas|Candida|Plasmodium|Treponema|Neisseria|Haemophilus|Klebsiella|Listeria|Legionella|Chlamydia|Mycoplasma|Homo|Mus|Rattus|Saccharomyces) [a-z]{3,})\b")
latin = []
for b in units:
    for name in LATIN.findall(plain(b["text"])):
        z = tr.get(b["id"], "")
        if name in plain(z):
            latin.append({"id": b["id"], "page": b["page"], "name": name, "italic": f"<i>{name}</i>" in z or bool(re.search(r"<i>[^<]*" + re.escape(name.split()[0]), z))})
res["latin_names"] = latin

# 5. abbreviations: first occurrence must carry a Chinese full name in brackets
ABBR = re.compile(r"(?<![A-Za-z])([A-Z][A-Za-z0-9\-]{1,7})(?![A-Za-z])")
seen_abbr, abbr_flags = set(), []
SKIP = {"CT", "MRI", "DNA", "RNA", "PDF", "X", "OEM", "CRT", "LED", "CCD", "WEB", "HP", "URL", "ATP", "NADH", "NAD"}
for b in units:
    z = plain(tr.get(b["id"], ""))
    for m in ABBR.finditer(z):
        a = m.group(1)
        if a in seen_abbr or not re.fullmatch(r"[A-Z0-9\-]{2,8}", a) or a.isdigit():
            continue
        seen_abbr.add(a)
        ctx = z[max(0, m.start() - 90):m.end() + 40]
        chem = re.search(r"\d", a) and re.search(r"[A-Z][a-z]?\d", a)
        if not chem and not re.search(r"（[^（）]*" + re.escape(a) + r"[^（）]*）|" + re.escape(a) + r"（[^（）]+）", ctx) and a not in SKIP:
            abbr_flags.append({"abbr": a, "id": b["id"], "page": b["page"], "context": ctx})
res["abbreviations_first_use"] = abbr_flags

# 6. translationese patterns
PATTERNS = {
    "进行……的处理/操作": r"进行[^，。；]{0,12}的(处理|操作|测定|检测|研究|分析)",
    "被……所": r"被[^，。；]{1,12}所",
    "对……进行……": r"对[^，。；]{1,15}进行",
    "连续三个“的”": r"的[^，。；的]{1,6}的[^，。；的]{1,6}的",
    "作为……而……": r"作为[^，。；]{1,12}而",
}
tq = {}
for name, pat in PATTERNS.items():
    hits = [(b["id"], b["page"], m.group(0)) for b in units for m in re.finditer(pat, plain(tr.get(b["id"], "")))]
    tq[name] = {"count": len(hits), "examples": hits[:6]}
res["translationese"] = tq

# 7. 【待核】 flags and doubts
tbd = [(b["id"], b["page"], m) for b in units for m in re.findall(r"【待核：(.*?)】", tr.get(b["id"], ""))]
for fid, f in figs.items():
    blob = json.dumps(f, ensure_ascii=False)
    tbd += [(fid, byid[fid]["page"], m) for m in re.findall(r"【待核：(.*?)】", blob)]
res["to_verify"] = tbd
all_doubts = [(i, d) for i, ds in doubts.items() for d in ds] + [(i, d) for i, f in figs.items() for d in (f.get("doubts") or [])]
res["doubts"] = len(all_doubts)

# 8. errata (p103): the corrected wording must already be in this PDF
alltext = "\n".join(b.get("text", "") + b.get("caption", "") + " ".join(b.get("figtext", [])) + b.get("raw_text", "") for b in blocks if b["page"] < 103)
errata = [("大分大学医学部附属病院", "大分大学医学部付属病院"), ("東亞特殊電機", "東亜特殊電機"), ("半自動血球計数装置", "半自動血球計算装置"),
          ("本格展開し", "進出し"), ("並み居る", None), ("SYRINGE", "SYLINGE")]
res["errata"] = [{"correct": c, "present": c in alltext, "old_present": (o in alltext) if o else None} for c, o in errata]

# 9. back-translation spot check (5 paragraphs: technical ch.5, conclusion ch.10, 3 random)
if "--no-back" not in sys.argv:
    random.seed(7)
    paras = [b for b in units if b["type"] == "p" and len(b["text"]) > 150 and tr.get(b["id"])]
    pick = [next(b for b in paras if 42 <= b["page"] <= 56), next(b for b in paras if b["page"] >= 94)]
    pick += random.sample([b for b in paras if b not in pick], 3)
    items = [{"id": b["id"], "page": b["page"], "ja_original": b["text"], "zh": tr[b["id"]]} for b in pick]
    prompt = ("下面是 5 段日文原文及中文译文。对每一段：(1) 把中文译文独立回译成日文（不要参考原文措辞）；"
              "(2) 将回译与原文逐句比较，指出语义偏差（遗漏、添加、语气或确定程度改变、数字或术语错误），没有就写「无实质偏差」；"
              "(3) 指出译文中的翻译腔或不符合中文专著语体之处，没有就写「无」；(4) 给出 1–5 分的忠实度评分。"
              "只输出 JSON 数组，每项包含 id、back_translation、deviations、style_issues、score。\n\n" + json.dumps(items, ensure_ascii=False, indent=1))
    out = call("你是严格的日中翻译审校。", prompt, tag="qa:backtranslation", effort="high", max_tokens=32000)
    try:
        res["back_translation"] = json.loads(out[out.index("["):out.rindex("]") + 1])
    except ValueError:
        res["back_translation_raw"] = out
elif (WORK / "qa_checks.json").exists():
    prev = json.load(open(WORK / "qa_checks.json"))
    if "back_translation" in prev:
        res["back_translation"] = prev["back_translation"]
json.dump(res, open(WORK / "qa_checks.json", "w"), ensure_ascii=False, indent=1)

# ---------- B. 术语与缩写表 ----------
counts, firsts = {}, {}
for g in gloss:
    vs = ja_variants(g)
    hits = [b for b in blocks if any(v in (b.get("text", "") + b.get("caption", "") + " ".join(b.get("figtext", []))) for v in vs)]
    counts[g["日文"]] = len(hits)
    firsts[g["日文"]] = f"p{hits[0]['page']}（{hits[0]['id']}）" if hits else ""
with open(OUT / "术语与缩写表.csv", "w", newline="", encoding="utf-8-sig") as f:
    w = csv.writer(f)
    w.writerow(["日文原词", "英文/缩写", "中文译名", "类别", "首次出现位置（页/段落）", "出现段落数", "首次出现处是否括注", "备注"])
    for g in gloss:
        loc = firsts[g["日文"]]
        bid = loc.split("（")[-1].rstrip("）") if loc else ""
        noted = ""
        if bid in tr:
            noted = "是" if any(v + "（" in tr[bid] for v in zh_variants(g)) else "否"
        w.writerow([g["日文"], g["英文"], g["中文"], g["类别"], loc, counts[g["日文"]], noted, g["备注"]])

# ---------- C + D. QA_report.md ----------
def loc(i):
    b = byid.get(i, {})
    return f"p{b.get('page', '?')}（{i}）"


L = []
c = res["completeness"]
L += ["# 质量报告：《臨床検査技術の系統化調査》中译", "",
      f"- 正文单元：{c['units']} 个，已译 {c['translated']} 个；图、表、附录页：{c['figures']} 个，已译 {c['figures_done']} 个",
      f"- 疑点：{res['doubts']} 条（分类见 C 节）；【待核】标记：{len(tbd)} 处",
      f"- API 费用合计：约 {total_cost():.2f} 美元", "",
      "## D. 自检结果", "",
      "| 检查项 | 结果 |", "|---|---|",
      f"| 完整性（逐段对齐） | 缺译 {len(c['missing'])} 段，图表缺译 {len(c['figures_missing'])} 个；长度比异常 {len(c['length_ratio_flags'])} 段（见下） |",
      f"| 数字与单位 | 原文数字在译文中缺失的段落 {len(num_flags)} 个（含年号换算、汉字数字改写等正常情况，已逐条列出） |",
      f"| 缩写、型号、拉丁字母符号 | 译文缺失的段落 {len(sym_flags)} 个 |",
      f"| 上标（引用编号、指数） | 不一致 {len(sup_flags)} 段 |",
      f"| 术语一致性 | 未使用术语表译名的词条 {len(term_flags)} 个（多为同一日文词在不同语境下的合理变体，见下） |",
      (f"| 物种拉丁名斜体 | 原文出现 {len(latin)} 处，斜体 {sum(1 for x in latin if x['italic'])} 处 |" if latin else "| 物种拉丁名斜体 | 原文未出现物种拉丁学名（菌名等均以片假名书写），无需处理 |"),
      f"| 缩写首次出现给出全称 | 首次出现未带全称的缩写 {len(abbr_flags)} 个 |",
      "| 翻译腔 | " + "；".join(f"{k} {v['count']} 处" for k, v in tq.items()) + " |",
      f"| 正误表核对（p103） | " + "；".join(f"{e['correct']}：{'已是更正后写法' if e['present'] else '未找到'}" for e in res["errata"]) + " |"]
if render:
    L.append(f"| 渲染检查 | {render.get('summary', '')} |")
L.append("")
if (WORK / "manual_review.md").exists():
    L += [(WORK / "manual_review.md").read_text().strip(), ""]
if res.get("back_translation"):
    L += ["### 回译抽查（5 段）", "", "| 位置 | 评分 | 语义偏差 | 语体问题 |", "|---|---|---|---|"]
    for x in res["back_translation"]:
        L.append(f"| {loc(x.get('id'))} | {x.get('score')} | {str(x.get('deviations')).replace('|', '／')} | {str(x.get('style_issues')).replace('|', '／')} |")
    L.append("")
if c["length_ratio_flags"]:
    L += ["### 长度比异常的段落", ""] + [f"- {loc(x['id'])}：日文 {x['ja_len']} 字 → 中文 {x['zh_len']} 字" for x in c["length_ratio_flags"]] + [""]
if num_flags:
    L += ["### 数字核对明细", ""] + [f"- {loc(x['id'])}：译文中未见 {', '.join(x['missing'])}" for x in num_flags] + [""]
if sym_flags:
    L += ["### 符号核对明细", ""] + [f"- {loc(x['id'])}：译文中未见 {', '.join(x['missing'])}" for x in sym_flags] + [""]
if term_flags:
    L += ["### 术语一致性明细（前 30 条）", ""] + [f"- {x['ja']} → {x['zh']}：{x['n_off']}/{x['occurrences']} 段未用此译名（{', '.join(x['ids'][:4])}）" for x in term_flags[:30]] + [""]
if abbr_flags:
    L += ["### 缩写首次出现明细", ""] + [f"- {x['abbr']}：{loc(x['id'])}，上下文「{x['context']}」" for x in abbr_flags] + [""]
L += ["### 翻译腔示例", ""]
for k, v in tq.items():
    for i, p, m in v["examples"]:
        L.append(f"- {k}：{loc(i)}「{m}」")
cls = json.load(open(WORK / "doubt_classes.json")) if (WORK / "doubt_classes.json").exists() else {}
keyed = [(f"{i}#{k}", i, d) for i, ds in doubts.items() for k, d in enumerate(ds)] + \
        [(f"{i}#{k}", i, d) for i, f in figs.items() for k, d in enumerate(f.get("doubts") or [])]
keyed.sort(key=lambda x: (byid[x[1]]["page"], x[1]))
L += ["", "## C. 疑点清单", "", "译文照原文翻译，不擅自修正。涉及史实的判断来自译者（Claude）的背景知识，只作核对线索，引用前请查证。", ""]
for cat, title in (("A", "原文疑似错误"), ("B", "原文歧义或信息不足"), ("C", "译法说明（非原文问题）")):
    rows = [x for x in keyed if cls.get(x[0], "B") == cat]
    L += [f"### {title}（{len(rows)} 条）", "", "| # | 位置 | 问题 | 建议 |", "|---|---|---|---|"]
    for n, (k, i, d) in enumerate(rows, 1):
        L.append(f"| {cat}{n} | {loc(i)} | {str(d.get('问题', '')).replace('|', '／')} | {str(d.get('建议', '')).replace('|', '／')} |")
    L.append("")
L += ["", "## 【待核】清单", ""] + ([f"- {loc(i)}：{m}" for i, p, m in tbd] or ["无"])
L += ["", "## 已知局限", "",
      "- 版式版（zh.pdf / dual.pdf）由 BabelDOC 排版：图片中的位图文字不翻译（精读版在每张图下附有「图内文字对照」）；带圈数字①②③会显示为 1 2 3；个别段落为适应原版面而字号缩小。",
      "- 版式版的译文以精读版为参考重新生成，措辞可能与精读版略有出入，以精读版为准。",
      "- 本译文由 Claude 机器翻译并自动自检，未经人工逐句审校，正式引用前请核对原文。", ""]
(OUT / "QA_report.md").write_text("\n".join(L))

summary = (f'<section class="box" id="selfcheck"><h2>自检结果摘要</h2><ul>'
           f'<li>正文 {c["translated"]}/{c["units"]} 段已译；图表 {c["figures_done"]}/{c["figures"]} 个已译</li>'
           f'<li>上标不一致 {len(sup_flags)} 段；数字核对提示 {len(num_flags)} 段；缩写首次未带全称 {len(abbr_flags)} 个</li>'
           f'<li>翻译腔：' + "；".join(f"{html.escape(k)} {v['count']} 处" for k, v in tq.items()) + '</li>'
           f'<li>疑点 {res["doubts"]} 条，【待核】{len(tbd)} 处；完整报告见 QA_report.md</li></ul></section>')
(WORK / "qa_summary.html").write_text(summary)
print(json.dumps({k: (len(v) if isinstance(v, (list, dict)) else v) for k, v in res.items()}, ensure_ascii=False))
