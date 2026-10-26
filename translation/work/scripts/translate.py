"""Step 3: paragraph-level translation (id-aligned) with glossary, first-occurrence term notes and context.

usage: translate.py [--pages 1,5,14]   (default: all pages)
Writes/extends work/translations.json  {block_id: zh}
"""
import argparse, csv, json, re, sys, threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from llm import call, Refused
from glossary_util import ja_variants

WORK = Path(__file__).resolve().parent.parent
ap = argparse.ArgumentParser()
ap.add_argument("--pages", default="")
ap.add_argument("--workers", type=int, default=6)
args = ap.parse_args()
pages = {int(p) for p in args.pages.split(",") if p}

blocks = json.load(open(WORK / "blocks.json"))
gloss = list(csv.DictReader(open(WORK.parent / "glossary.csv", encoding="utf-8-sig")))
out_path = WORK / "translations.json"
done = json.load(open(out_path)) if out_path.exists() else {}
doubt_path = WORK / "doubts.json"
doubts = json.load(open(doubt_path)) if doubt_path.exists() else {}
lock = threading.Lock()

JA = re.compile(r"[぀-ヿ㐀-鿿]")
units = [b for b in blocks if b["type"] in ("h1", "h2", "h3", "p", "front", "note")
         or (b["type"] == "ref" and JA.search(b["text"]))]

# first block (document order) in which each glossary term appears in the Japanese source
first_seen = {}
for g in gloss:
    vs = ja_variants(g)
    if not vs:
        continue
    for b in units:
        if any(v in b["text"] for v in vs):
            first_seen.setdefault(b["id"], []).append(g)
            break

gloss_lines = "\n".join(f"{g['日文']}\t{g['中文']}\t{g['英文']}\t{g['备注'][:160]}" for g in gloss)
SYSTEM = [{"type": "text", "cache_control": {"type": "ephemeral"}, "text": f"""你是一名日译中生物学专业译者，长期翻译生物学、检验医学教材、专著和科研读物。你正在翻译日本国立科学博物馆的《臨床検査技術の系統化調査》（伊藤道雄，2017），读者是中国大陆体外诊断（IVD）企业的专业人员。译文语体应接近中文生物学/检验医学专著或教材，而不是新闻稿、口语或机器翻译腔。

## 必须遵守
1. 事实和逻辑不得增删，不得意译改变原意。数字、单位、公式、图表编号、物种拉丁名、基因名、蛋白名、试剂名、公司名、法规名必须保留并准确对应。作者的个人判断和语气（如「〜と思われる」「〜ではないだろうか」）照原样保留，不得把推测译成定论。
2. 专业术语以下面的术语表为准，全文一致；术语表里没有的，优先使用中文生物学和检验医学界通用译名。若不确定，在译文中写【待核：原词】，不要编造。
3. 输入里如果给出「本批次中首次出现的术语」，就在译文中该术语第一次出现的地方写成「中文译名（日文原词/英文或缩写）」，例如「细胞凋亡（アポトーシス/apoptosis）」「放射免疫测定（ラジオイムノアッセイ/RIA）」；人名写成「中文译名（原文拼写）」。其他地方不加这种括注。英文缩写首次出现时给出中文全称，如「C反应蛋白（CRP）」。
4. 物种拉丁名用 <i>…</i> 标为斜体（如 <i>Escherichia coli</i>）；基因名、蛋白名、缩写按学科惯例处理，全文一致。除 <i> 和原有的 <sup> 外不要输出其他标签。
5. <sup>…</sup> 标签及其内容（引用编号、指数）必须原样保留在对应位置。
6. 语体：句子长度适中，逻辑连接自然；拆分日式长定语，少用生硬被动句，避免「进行……的处理」「被……所」「对……进行……」「的的的」等翻译腔。日文省略的主语只在不补就会产生歧义时补出，并用〔〕标出。
7. 中日同形异义词不要照搬汉字：検討 → 研究/探讨/考察（不能译成"检讨"）；精度管理 → 质量控制；基準値 → 参考区间；至適 → 最适；有意差 → 显著性差异；投与 → 给药；処置 → 处理；（株）→ 按术语表的企业名。
8. 日本年号换算成公历并保留原年号，如「平成28年度」→「2016年度（平成28年度）」，只在该段首次出现时这样写。「円」译为「日元」。
9. 保留原文段落结构、标题层级、列表编号（①②、（1）等）、图表编号和注释位置；不要合并或拆分段落。标题（h1/h2/h3）只译标题文字，保留编号和「｜」。
10. 参考文献条目（type=ref）：作者名、出版社、页码、年份、URL 原样保留，只把日文书名、篇名和说明文字译成中文，整条输出为一行。
11. 疑似原文笔误、数字前后矛盾、歧义、缺上下文之处：译文照原文翻译，不擅自修正，也不在译文里加注；把问题写进该段的 doubts 列表。术语表备注中写明「列入疑点清单」的条目，在其出现的段落也要列入 doubts。

## 术语表（日文<TAB>中文<TAB>英文<TAB>备注）
{gloss_lines}

## 输出格式
只输出一个 JSON 对象。键是段落 id，值是对象：{{"zh": "该段译文", "doubts": [{{"问题": "……", "建议": "……"}}]}}，没有疑点时 doubts 为 []。必须覆盖输入里的每一个 id，不要输出其他内容。"""}]


def make_chunks(items, max_chars=1800, max_items=14):
    chunks, cur, n = [], [], 0
    for b in items:
        if cur and (n + len(b["text"]) > max_chars or len(cur) >= max_items):
            chunks.append(cur); cur, n = [], 0
        cur.append(b); n += len(b["text"])
    if cur:
        chunks.append(cur)
    return chunks


index = {b["id"]: i for i, b in enumerate(units)}


def run_chunk(chunk):
    i0, i1 = index[chunk[0]["id"]], index[chunk[-1]["id"]]
    before = units[max(0, i0 - 2):i0]
    after = units[i1 + 1:i1 + 2]
    terms = [g for b in chunk for g in first_seen.get(b["id"], [])]
    payload = [{"id": b["id"], "type": b["type"], "page": b["page"], "ja": b["text"]} for b in chunk]
    msg = ""
    if before:
        msg += "【上文（仅供参考，不要翻译）】\n" + "\n".join(b["text"] for b in before) + "\n\n"
    msg += "【待翻译段落】\n" + json.dumps(payload, ensure_ascii=False, indent=0) + "\n\n"
    if after:
        msg += "【下文（仅供参考，不要翻译）】\n" + "\n".join(b["text"][:300] for b in after) + "\n\n"
    if terms:
        msg += "【本批次中首次出现、需要按规范第 3 条加注的术语】\n" + "\n".join(
            f"{g['日文']} → {g['中文']}（{g['英文'] or '无英文'}）" for g in terms) + "\n"
    tag = f"tr:{chunk[0]['id']}-{chunk[-1]['id']}"
    for attempt in range(3):
        text = call(SYSTEM, msg, tag=tag, effort="medium", max_tokens=32000)
        try:
            res = json.loads(text[text.index("{"):text.rindex("}") + 1])
        except ValueError:
            print(f"[{tag}] bad JSON, retry", flush=True); continue
        res = {k: (v if isinstance(v, dict) else {"zh": v, "doubts": []}) for k, v in res.items()}
        missing = [b["id"] for b in chunk if not str(res.get(b["id"], {}).get("zh", "")).strip()]
        bad_sup = [b["id"] for b in chunk if b["id"] in res
                   and b["text"].count("<sup>") != res[b["id"]]["zh"].count("<sup>")]
        if missing or bad_sup:
            print(f"[{tag}] missing={missing} sup_mismatch={bad_sup}, retry", flush=True)
            if attempt < 2:
                continue
        with lock:
            for b in chunk:
                r = res.get(b["id"])
                if r and r.get("zh"):
                    done[b["id"]] = r["zh"]
                    if r.get("doubts"):
                        doubts[b["id"]] = r["doubts"]
                    else:
                        doubts.pop(b["id"], None)
            json.dump(done, open(out_path, "w"), ensure_ascii=False, indent=1)
            json.dump(doubts, open(doubt_path, "w"), ensure_ascii=False, indent=1)
        return tag
    return tag + " (incomplete)"


todo = [b for b in units if b["id"] not in done and (not pages or b["page"] in pages)]
chunks = make_chunks(todo)
print(f"{len(todo)} blocks in {len(chunks)} chunks", flush=True)
if chunks:                                   # first request alone warms the prompt cache for the rest
    try:
        print("warm-up", run_chunk(chunks[0]), flush=True)
    except Refused as e:
        print("REFUSED", e, flush=True)
    chunks = chunks[1:]
with ThreadPoolExecutor(args.workers) as ex:
    futs = [ex.submit(run_chunk, c) for c in chunks]
    for k, f in enumerate(as_completed(futs), 1):
        try:
            print(f"{k}/{len(chunks)} {f.result()}", flush=True)
        except Refused as e:
            print("REFUSED", e, flush=True)
