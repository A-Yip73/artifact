"""Step 6b: targeted style pass on paragraphs flagged for translationese (meaning, numbers and tags must not change)."""
import json, re, sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from llm import call

WORK = Path(__file__).resolve().parent.parent
blocks = {b["id"]: b for b in json.load(open(WORK / "blocks.json"))}
tr = json.load(open(WORK / "translations.json"))
PATTERNS = [r"进行[^，。；]{0,12}的(处理|操作|测定|检测|研究|分析)", r"被[^，。；]{1,12}所", r"对[^，。；]{1,15}进行",
            r"的[^，。；的]{1,6}的[^，。；的]{1,6}的", r"作为[^，。；]{1,12}而"]
ids = [i for i, z in tr.items() if any(re.search(p, re.sub(r"<[^>]+>", "", z)) for p in PATTERNS)]
print("flagged", len(ids), flush=True)

SYSTEM = """你是中文生物学/检验医学专著的资深编辑。你会收到若干段日文原文及其中文译文。请只做语体润色：
- 消除翻译腔：「进行……的处理」「被……所」「对……进行……」「的的的」「作为……而……」等，改成自然的中文专著表达；
- 不得改变事实、逻辑、确定程度和语气，不得增删信息；
- 数字、单位、术语译名、括注、<sup>…</sup> 和 <i>…</i> 标签、【待核：…】标记必须原样保留；
- 若该用法在中文学术写作中本属自然（例如「对数据进行统计分析」），可以不改。
只输出 JSON 对象：键是段落 id，值是润色后的整段译文；不需要改动的段落原样返回。"""

NUM = re.compile(r"\d+(?:[.,]\d+)*")
changed, kept = {}, []


def run(batch):
    items = [{"id": i, "ja": blocks[i]["text"], "zh": tr[i]} for i in batch]
    out = call(SYSTEM, json.dumps(items, ensure_ascii=False, indent=0), tag=f"polish:{batch[0]}", effort="medium", max_tokens=32000)
    res = json.loads(out[out.index("{"):out.rindex("}") + 1])
    for i in batch:
        new, old = res.get(i, ""), tr[i]
        safe = (new and Counter(NUM.findall(new)) == Counter(NUM.findall(old))
                and Counter(re.findall(r"<sup>.*?</sup>|</?i>|【待核：.*?】", new)) == Counter(re.findall(r"<sup>.*?</sup>|</?i>|【待核：.*?】", old))
                and 0.8 <= len(new) / max(1, len(old)) <= 1.2)
        if safe and new != old:
            changed[i] = {"before": old, "after": new}
        elif not safe:
            kept.append(i)


batches = [ids[k:k + 8] for k in range(0, len(ids), 8)]
with ThreadPoolExecutor(4) as ex:
    list(ex.map(run, batches))
for i, c in changed.items():
    tr[i] = c["after"]
json.dump(tr, open(WORK / "translations.json", "w"), ensure_ascii=False, indent=1)
json.dump(changed, open(WORK / "polish_changes.json", "w"), ensure_ascii=False, indent=1)
print("revised", len(changed), "rejected by safety check", len(kept), kept)
