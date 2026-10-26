"""Step 2a: read the whole document once and build glossary.tsv / glossary.csv."""
import csv, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from llm import call

WORK = Path(__file__).resolve().parent.parent
blocks = json.load(open(WORK / "blocks.json"))

parts = []
for b in blocks:
    if b["type"] in ("fig", "tab"):
        parts.append(f"[p{b['page']}] {b['caption']} {b['caption_sub']} {' / '.join(b['figtext'])}")
    elif b["type"] == "appendix":
        parts.append(f"[p{b['page']} 附录] {b['raw_text']}")
    elif b["type"] != "en":
        parts.append(f"[p{b['page']}] {b['text']}")
doc_text = "\n".join(parts)

SYSTEM = """你是资深的日译中科技译者，专长是检验医学与体外诊断（IVD）行业。你将通读一份日文报告的全文，为后续翻译建立术语表。

读者：中国大陆 IVD 企业员工（懂检验医学和 IVD 产品，不懂日语）。

收录范围：
- 检验医学与 IVD 专业术语、方法名、仪器与装置名、试剂与生物分子、疾病与标志物、检验项目及缩写；
- 日本特有的制度和概念（诊疗报酬、保险点数、学会、法规等）；
- 机构、企业、产品名；
- 人名：西方人名（日文写作片假名）要还原出原文拼写；
- 中日同形异义、容易误译的词（例如 検討、検体、精度管理、基準値）。
常见的一般词汇不要收。同一概念只收一条，变体写在备注里。

定译原则：
1. 优先采用中国大陆检验医学和 IVD 行业的通行译法（全国科学技术名词审定委员会规范名、检验医学教材、NMPA 注册文件常用语），其次是学术文献的常用译法。
2. 片假名外来语：先还原出英文原词，再确定中文译名。
3. 日本企业和机构：有官方中文名的就用（如 シスメックス → 希森美康），没有的保留日文汉字或英文名，并在备注里说明。
4. 人名：日本人保留汉字原名；西方人写成「中文通行译名（原文拼写）」，没有通行译名的直接用原文拼写。
5. 有两种以上合理译法、或你没有把握的，状态标「待确认」，在备注里列出候选和取舍理由。确定的标「确定」。

输出格式：只输出一个 TSV 表格，放在 <tsv> 和 </tsv> 之间，第一行是表头，每行一条，字段之间用制表符分隔，字段内不要出现制表符或换行：
日文	中文	英文	类别	首次出现页	状态	备注
类别只能取以下之一：检验术语、方法与原理、仪器装置、试剂与分子、疾病与标志物、制度与法规、机构与企业、产品名、人名、其他。
首次出现页填数字（原文中的 [pN] 标记）。英文不确定时留空。"""

user = [
    {"type": "text", "text": "以下是报告全文。每段开头的 [pN] 是原 PDF 页码。\n\n<document>\n" + doc_text + "\n</document>",
     "cache_control": {"type": "ephemeral"}},
    {"type": "text", "text": "请通读全文，按要求输出术语表。条目按首次出现页排序。"},
]

out = call(SYSTEM, user, tag="glossary", effort="high", max_tokens=100000)
tsv = out.split("<tsv>")[-1].split("</tsv>")[0].strip()
(WORK / "glossary_raw.tsv").write_text(tsv)
rows = [r.split("\t") for r in tsv.splitlines() if r.strip()]
header, body = rows[0], [r + [""] * (7 - len(r)) for r in rows[1:]]
with open(WORK.parent / "glossary.csv", "w", newline="", encoding="utf-8-sig") as f:
    w = csv.writer(f)
    w.writerow(["日文", "中文", "英文", "类别", "首次出现页", "状态", "备注"])
    w.writerows(r[:7] for r in body)
print("entries", len(body), "pending", sum(1 for r in body if "待" in r[5]))
