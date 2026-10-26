"""Step 4: layout-preserving zh.pdf / dual.pdf with BabelDOC, translating through Claude (Anthropic SDK).

The master translation (translations.json + figures.json) is passed to Claude as reference so the
layout PDF reads the same as the bilingual edition.  usage: run_babeldoc.py OUT_DIR [--pages 1,5-6]
"""
import argparse, asyncio, csv, json, re, sys
from collections import defaultdict
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from llm import call, MODEL
from glossary_util import ja_variants, zh_variants

import babeldoc.format.pdf.high_level as high_level
from babeldoc.docvision.doclayout import DocLayoutModel
from babeldoc.format.pdf.translation_config import TranslationConfig, WatermarkOutputMode
from babeldoc.glossary import Glossary
from babeldoc.translator.translator import BaseTranslator, set_translate_rate_limiter

WORK = Path(__file__).resolve().parent.parent
ap = argparse.ArgumentParser()
ap.add_argument("out_dir")
ap.add_argument("--pages", default=None)
ap.add_argument("--src", default=str(WORK / "source_decrypted.pdf"))
args = ap.parse_args()

# ---------- translation memory from the master translation ----------
blocks = json.load(open(WORK / "blocks.json"))
tr = json.load(open(WORK / "translations.json"))
figs = json.load(open(WORK / "figures.json")) if (WORK / "figures.json").exists() else {}
TAG = re.compile(r"<[^>]+>|\{v\d+\}")
ITALIC = re.compile(r"</?i>")
pairs = []
for b in blocks:
    if b["id"] in tr:
        pairs.append((TAG.sub("", b["text"]), ITALIC.sub("", tr[b["id"]])))
    f = figs.get(b["id"])
    if f:
        if b.get("caption"):
            pairs.append((b["caption"], ITALIC.sub("", f.get("caption_zh", ""))))
        if b.get("caption_sub"):
            pairs.append((b["caption_sub"], ITALIC.sub("", f.get("caption_sub_zh", ""))))
        for lab in f.get("labels") or []:
            if len(lab.get("ja", "")) >= 2:
                pairs.append((lab["ja"], ITALIC.sub("", lab["zh"])))


def bigrams(s):
    s = re.sub(r"\s+", "", s)
    return {s[i:i + 2] for i in range(len(s) - 1)}


index = defaultdict(set)
pair_bg = []
for k, (ja, _) in enumerate(pairs):
    bg = bigrams(ja)
    pair_bg.append(bg)
    for g in bg:
        index[g].add(k)


def lookup(text, top=2):
    bg = bigrams(TAG.sub("", text))
    if len(bg) < 3:
        return []
    score = defaultdict(int)
    for g in bg:
        for k in index.get(g, ()):
            score[k] += 1
    ranked = sorted(score.items(), key=lambda kv: -kv[1])[:top]
    return [k for k, s in ranked if s / len(bg) >= 0.5]


GLOSS_CSV = WORK / "glossary_babeldoc.csv"
with open(GLOSS_CSV, "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f); w.writerow(["source", "target"])
    for g in csv.DictReader(open(WORK.parent / "glossary.csv", encoding="utf-8-sig")):
        zh = zh_variants(g)
        for v in ja_variants(g):
            if zh and not v.isascii():          # leave abbreviations such as CT / MRI untouched
                w.writerow([v, zh[0]])

SYSTEM = ("你是日译中译者，专长是检验医学与体外诊断（IVD），正在为一份 PDF 生成保留原版式的中文版。"
          "严格遵守用户消息里的结构规则和输出格式（JSON 数组）。"
          "如果给出了「参考译文」，那是本文档已经审定的精读版译文：请尽量沿用它的措辞和术语，"
          "只根据输入切分的片段截取对应部分，并保留输入中的占位符和标签。"
          "版式版空间有限：参考译文里「中文（日文/英文）」形式的首次出现括注可以省略，只保留中文；"
          "【待核：…】标记要保留。不要添加输入里没有的标签。语体接近中文生物学专著，避免翻译腔。"
          "不要输出 JSON 以外的任何内容。")


class ClaudeTranslator(BaseTranslator):
    name = "claude"

    def __init__(self, lang_in, lang_out):
        super().__init__(lang_in, lang_out, ignore_cache=False)
        self.model = MODEL
        self.add_cache_impact_parameters("model", MODEL)
        self.add_cache_impact_parameters("tm", "v2")

    def do_translate(self, text, rate_limit_params=None):
        refs = lookup(text)
        hint = "".join(f"\n参考译文：{pairs[k][1]}" for k in refs)
        return call(SYSTEM, f"把下面这段日文译成简体中文，只输出译文。{hint}\n\n{text}",
                    tag="bd:plain", effort="low", max_tokens=4000).strip()

    def do_llm_translate(self, text, rate_limit_params=None):
        if text is None:
            return None
        refs = []
        try:
            items = json.loads(text.split("## Here is the input:")[-1].strip())
            for it in items:
                for k in lookup(it.get("input", "")):
                    if k not in refs:
                        refs.append(k)
        except ValueError:
            pass
        ref_block = ""
        if refs:
            ref_block = "\n\n## 参考译文（精读版定稿）\n" + "\n".join(
                f"- 原文：{pairs[k][0][:400]}\n  译文：{pairs[k][1][:400]}" for k in refs[:12])
        return call(SYSTEM, text + ref_block, tag="bd:llm", effort="low", max_tokens=16000).strip()


async def main():
    high_level.init()
    set_translate_rate_limiter(8)
    translator = ClaudeTranslator("ja", "zh")
    config = TranslationConfig(
        input_file=args.src, font=None, pages=args.pages, output_dir=args.out_dir,
        translator=translator, term_extraction_translator=translator, debug=False,
        lang_in="ja", lang_out="zh", no_dual=False, no_mono=False, qps=8,
        doc_layout_model=DocLayoutModel.load_onnx(),
        watermark_output_mode=WatermarkOutputMode.NoWatermark,
        glossaries=[Glossary.from_csv(GLOSS_CSV, "zh")],
        auto_extract_glossary=False, pool_max_workers=8,
        working_dir=str(WORK / "babeldoc_tmp"),
    )
    async for event in high_level.async_translate(config):
        if event["type"] == "error":
            print("ERROR", event["error"], flush=True)
            break
        if event["type"] == "finish":
            print("FINISH", event["translate_result"], flush=True)
            break
        if event["type"] == "progress_update" and event.get("stage_current", 0) == event.get("stage_total", -1):
            print("stage done:", event.get("stage"), flush=True)


asyncio.run(main())
