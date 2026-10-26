"""Sort the doubt list into: A 原文疑似错误 / B 原文歧义或信息不足 / C 译法说明 (not a problem in the source)."""
import json, sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from llm import call

WORK = Path(__file__).resolve().parent.parent
doubts = json.load(open(WORK / "doubts.json"))
figs = json.load(open(WORK / "figures.json"))
items = [{"key": f"{i}#{k}", "问题": d.get("问题", ""), "建议": d.get("建议", "")} for i, ds in doubts.items() for k, d in enumerate(ds)]
items += [{"key": f"{i}#{k}", "问题": d.get("问题", ""), "建议": d.get("建议", "")} for i, f in figs.items() for k, d in enumerate(f.get("doubts") or [])]
SYSTEM = """你在整理一份日译中译稿的疑点清单。把每条疑点归入一类：
A = 原文疑似错误（笔误、错字、数字或年份矛盾、人名机构名拼写错误、事实性错误）；
B = 原文歧义或信息不足（指代不清、缺上下文、术语含义可能有多种理解，需要读者或专家判断）；
C = 译法说明（只是解释译者如何处理、为何这样译、格式取舍，原文本身没有问题）。
只输出 JSON 对象：键是 key，值是 "A"、"B" 或 "C"。"""


def run(batch):
    out = call(SYSTEM, json.dumps(batch, ensure_ascii=False, indent=0), tag="classify", effort="low", max_tokens=16000)
    return json.loads(out[out.index("{"):out.rindex("}") + 1])


res = {}
with ThreadPoolExecutor(4) as ex:
    for r in ex.map(run, [items[k:k + 70] for k in range(0, len(items), 70)]):
        res.update(r)
json.dump(res, open(WORK / "doubt_classes.json", "w"), ensure_ascii=False, indent=1)
from collections import Counter
print(len(items), Counter(res.values()))
