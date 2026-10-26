"""Step 3b: translate figure/table captions, in-figure text and tables (vision + extracted text).

usage: figures.py [--pages ...]   writes work/figures.json {block_id: {...}}
"""
import argparse, base64, csv, json, re, sys, threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from llm import call, Refused

WORK = Path(__file__).resolve().parent.parent
ap = argparse.ArgumentParser()
ap.add_argument("--pages", default="")
ap.add_argument("--workers", type=int, default=5)
args = ap.parse_args()
pages = {int(p) for p in args.pages.split(",") if p}

blocks = json.load(open(WORK / "blocks.json"))
gloss = list(csv.DictReader(open(WORK.parent / "glossary.csv", encoding="utf-8-sig")))
out_path = WORK / "figures.json"
done = json.load(open(out_path)) if out_path.exists() else {}
lock = threading.Lock()

gloss_lines = "\n".join(f"{g['日文']}\t{g['中文']}\t{g['英文']}" for g in gloss)
SYSTEM = [{"type": "text", "cache_control": {"type": "ephemeral"}, "text": f"""你是一名日译中生物学专业译者，长期翻译生物学、检验医学教材和专著。你在翻译《臨床検査技術の系統化調査》（伊藤道雄，2017）里的图、表和附录页，读者是中国大陆 IVD 企业员工。

你会收到一张从原 PDF 裁出的图片，以及从 PDF 文字层抽出的文字（文字层比图片更可靠；图片里的文字如果是位图，就只能从图片读取）。

规范：
- 语体接近中文生物学/检验医学专著，避免翻译腔；术语以术语表为准，全文一致；不确定的术语写【待核：原词】，不要编造。
- 数值、单位、型号、图表编号、英文缩写、化学式、引用编号（如 12））原样保留；物种拉丁名用 <i>…</i> 标斜体，除此之外不要输出标签。
- 日本年号换算成公历并保留原年号，如「平成28年」→「2016年（平成28年）」。西方人名写成「中文译名（原文拼写）」。
- 看不清的文字写〔无法辨认〕，不要猜测。疑似原文笔误或矛盾之处照原文翻译，并写进 doubts。

## 术语表（日文<TAB>中文<TAB>英文）
{gloss_lines}

## 输出格式
只输出一个 JSON 对象，字段如下：
- "caption_zh"：图题/表题的译文（保留「图2.1」这样的编号，把「図」写成「图」）。附录页没有图题时，给这一页起一个简短的中文标题。
- "caption_sub_zh"：图题下方说明文字的译文，没有就给空字符串。
- "table"：如果内容是表格（包括附录里的一览表、正误表），给出完整的中文译表 {{"header": [列名...], "rows": [[单元格...], ...]}}，单元格内换行用 <br>；如果不是表格就给 null。
- "labels"：如果是图（示意图、流程图、照片上的文字、年表、系统化图），列出图中所有日文文字及译文 [{{"ja": "...", "zh": "..."}}, ...]，按从上到下、从左到右的阅读顺序；图里只有英文或没有文字就给 []。表格这一项给 []。
- "doubts"：疑点列表 [{{"问题": "……", "建议": "……"}}]（如图内文字无法辨认、原表疑似有误、与正文矛盾），没有就给 []。"""}]


def img_block(path):
    return {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                         "data": base64.b64encode(open(WORK / path, "rb").read()).decode()}}


def run(b):
    tag = f"fig:{b['id']}:{b.get('label', 'p' + str(b['page']))}"
    if b["type"] == "appendix":
        text = f"这是报告第 {b['page']} 页（附录，{'原页面为横排，图片已旋转为横向' if b['rotated'] else '竖排'}）。\n\n【PDF 文字层】\n{b['raw_text']}"
    else:
        text = (f"这是报告第 {b['page']} 页的{'表' if b['type'] == 'tab' else '图'}。\n\n【图题/表题】{b['caption']}\n"
                f"【图题下方说明】{b['caption_sub'] or '（无）'}\n"
                f"【图/表区域内的文字层（按行）】\n" + ("\n".join(b["figtext"]) or "（无文字层，若图片里有文字请从图片读取）"))
    content = [img_block(b["image"]), {"type": "text", "text": text}]
    for attempt in range(3):
        out = call(SYSTEM, content, tag=tag, effort="medium", max_tokens=48000)
        try:
            res = json.loads(out[out.index("{"):out.rindex("}") + 1])
            break
        except ValueError:
            print(f"[{tag}] bad JSON, retry", flush=True)
    else:
        return tag + " FAILED"
    with lock:
        done[b["id"]] = res
        json.dump(done, open(out_path, "w"), ensure_ascii=False, indent=1)
    return tag


todo = [b for b in blocks if b["type"] in ("fig", "tab", "appendix") and b["id"] not in done
        and (not pages or b["page"] in pages)]
print(f"{len(todo)} figures/tables", flush=True)
if todo:                                   # first request alone warms the prompt cache for the rest
    try:
        print("warm-up", run(todo[0]), flush=True)
    except Refused as e:
        print("REFUSED", e, flush=True)
    todo = todo[1:]
with ThreadPoolExecutor(args.workers) as ex:
    futs = [ex.submit(run, b) for b in todo]
    for k, f in enumerate(as_completed(futs), 1):
        try:
            print(f"{k}/{len(todo)} {f.result()}", flush=True)
        except Refused as e:
            print("REFUSED", e, flush=True)
