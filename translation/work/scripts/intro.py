"""Write the 3–5 sentence 译者导读 (clearly labelled as not part of the original) from the finished translation."""
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from llm import call

WORK = Path(__file__).resolve().parent.parent
blocks = json.load(open(WORK / "blocks.json"))
tr = json.load(open(WORK / "translations.json"))
abstract = "\n".join(tr[b["id"]] for b in blocks if b["page"] == 1 and b["id"] in tr)
heads = "\n".join(tr[b["id"]] for b in blocks if b["type"] in ("h1", "h2") and b["id"] in tr)
summary = "\n".join(tr[b["id"]] for b in blocks if b["page"] >= 94 and b["type"] == "p" and b["id"] in tr)
prompt = f"""以下是一份日文报告中译本的摘要、章节标题和最后一章（总结）的译文。请为中国大陆 IVD 企业读者写一段「译者导读」，3–5 句话，分 1–2 段：
说明这份报告是什么、覆盖哪些技术领域、对 IVD 从业者最有参考价值的章节和看点。
要求：语体接近中文专著的前言；只能依据下面给出的内容，不得添加原文没有的事实或评价；不要使用标题、列表和 Markdown；只输出导读正文。

【摘要】
{abstract}

【章节标题】
{heads}

【第10章 总结】
{summary}"""
text = call("你是检验医学领域的资深编辑。", prompt, tag="intro", effort="medium", max_tokens=8000).strip()
(WORK / "intro.txt").write_text(text)
print(text)
