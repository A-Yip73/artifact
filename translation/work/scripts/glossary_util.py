"""Glossary helpers: expand entries such as 「CT（コンピュータ断層撮影）」 or 「变体：A、B」 into matchable variants."""
import re

PAREN = re.compile(r"[（(]([^（）()]*)[）)]")
GENERIC = {"検査", "測定", "試薬", "装置", "方法", "分析", "社", "法", "株式会社", "製品",
           "设备", "检查", "检验", "方法", "试剂", "公司", "美国", "日本", "测定", "产品"}


def _split(s, concat_generic):
    """Base form plus parenthesised alternatives.  A generic qualifier in parentheses is either glued on
    (Japanese side: 「生化学（検査）」 -> 生化学検査) or dropped (Chinese side: 「…公司（美国）」 -> …公司)."""
    base = PAREN.sub("", s).strip()
    out, glued = [], False
    for inner in (x.strip() for x in PAREN.findall(s)):
        if not inner:
            continue
        if inner in GENERIC:
            if concat_generic:
                out.append(base + inner); glued = True
        elif not re.search(r"语境|例如|等$", inner) and len(inner) <= 14:
            out.append(inner)
    if not glued:
        out.insert(0, base)
    res = []
    for v in out:
        res += [x.strip() for x in re.split(r"[；;]", v) if x.strip()]
    return [v for v in res if v not in GENERIC]


def ja_variants(g):
    out = _split(g["日文"], True)
    m = re.search(r"变体[:：]([^。；;]*)", g.get("备注", ""))
    if m:
        for v in re.split(r"[、，,/／]", m.group(1)):
            v = re.sub(r"[（(].*?[）)]", "", v).strip()
            if v and v not in GENERIC and re.search(r"[぀-ヿ㐀-鿿A-Za-z]", v):
                out.append(v)
    seen, res = set(), []
    for v in out:
        if len(v) >= 2 and v not in seen:
            seen.add(v); res.append(v)
    return res


def zh_variants(g):
    return [v for v in _split(g["中文"], False) if len(v) >= 2]
