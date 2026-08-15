"""表达分析 - 文本规则层。

实时检测多个维度的表达问题：
1. 口癖词（然后/就是/那个…）
2. 模糊/不确定表述（可能/应该/好像…）—— 单独维度，不混入口癖
3. 重复用词（n-gram 检测，覆盖中文无空格场景）
4. 不自信表述（我不太确定/说错了/怎么说呢…）
5. 句子过长（一口气说太多，听者难抓住重点）
6. 语速（配合时长使用）
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

# 常见中文口癖词典（按权重分级）
FILLER_WORDS: dict[str, int] = {
    # 高权重：明显的语言瑕疵
    "然后": 3, "就是": 3, "那个": 3, "这个": 3, "嗯": 2, "呃": 2, "啊": 1,
    # 中权重：连接词滥用
    "其实": 2, "基本上": 2, "一般来说": 1, "就是说": 3,
}

# 模糊/不确定表述（独立于口癖）
HEDGING_WORDS: dict[str, int] = {
    "可能": 2, "应该": 2, "好像": 2, "也许": 1, "大概": 1, "不一定": 2,
    "差不多": 2, "大概是": 2, "我猜": 2, "貌似": 2,
}

# 不自信表述（自我怀疑/求认可）
UNCERTAIN_PHRASES: dict[str, int] = {
    "我不太确定": 3, "说错了": 2, "怎么说呢": 3, "我也不太清楚": 3,
    "应该是吧": 3, "差不多吧": 2, "我说得对吗": 3, "不知道对不对": 3,
    "可能说得不對": 3, "我记得好像是": 2,
}

# 停用词（不算重复用词）
_STOPWORDS = {"的", "了", "是", "在", "我", "你", "他", "她", "它", "我们", "你们", "他们", "和", "与", "或", "及", "一", "个", "有", "就", "都", "也", "很", "会", "能", "要", "这", "那", "不", "人", "说", "去", "来"}


@dataclass
class AnalysisResult:
    """单段文本的实时分析结果。"""

    text: str
    filler_hits: list[dict] = field(default_factory=list)        # [{word, count, weight}]
    hedge_hits: list[dict] = field(default_factory=list)         # [{word, count}] 模糊表述
    uncertain_hits: list[dict] = field(default_factory=list)     # [{word, count}] 不自信表述
    repeated_words: list[dict] = field(default_factory=list)     # [{word, count}] 重复用词
    long_sentences: list[str] = field(default_factory=list)      # 过长的句子原文
    word_count: int = 0
    unique_word_count: int = 0
    repetition_rate: float = 0.0
    has_warning: bool = False                                     # 是否需要高亮
    warning_level: str = "normal"                                # normal/filler/repeat


def _count_hits(text: str, words: dict[str, int]) -> list[dict]:
    """统计词典命中（含权重时带 weight），去掉互为子串的冗余项（保最长匹配）。"""
    hits = []
    for word, weight in sorted(words.items(), key=lambda x: -len(x[0])):
        if any(word in h["word"] for h in hits):
            continue  # 已被更长的词条覆盖
        count = text.count(word)
        if count > 0:
            hit = {"word": word, "count": count}
            if weight is not None:
                hit["weight"] = weight
            hits.append(hit)
    return hits


def _detect_repeated_ngrams(text: str, n: int = 2, min_count: int = 2) -> list[dict]:
    """n-gram 重复检测：找出一句内反复出现的 2~4 字片段。

    中文没有空格，传统"分词"在无词典场景下不可行；n-gram 滑窗
    统计片段出现次数是简单可靠的替代方案。
    """
    # 只保留汉字（去标点、数字、英文）
    han = re.findall(r"[\u4e00-\u9fa5]+", text)
    flat = "".join(han)
    if len(flat) < n * min_count:
        return []

    counter: Counter = Counter()
    for size in (2, 3, 4):
        for i in range(len(flat) - size + 1):
            gram = flat[i:i + size]
            # 跳过包含停用字组合的无意义片段
            if all(ch in _STOPWORDS for ch in gram):
                continue
            # 跳过口癖/模糊词本身（已单独统计）
            if any(gram == w or w in gram for w in {**FILLER_WORDS, **HEDGING_WORDS} if abs(len(w) - size) <= 1):
                continue
            counter[gram] += 1

    # 过滤：出现次数达标 & 不是更长片段的子串（保最长）
    candidates = [(g, c) for g, c in counter.items() if c >= min_count and len(g) >= 2]
    # 按次数降序、长度降序，去掉互为子串的冗余项
    candidates.sort(key=lambda x: (-x[1], -len(x[0])))
    result: list[dict] = []
    seen: list[str] = []
    for gram, cnt in candidates:
        if any(gram in s for s in seen):
            continue
        # 排除纯叠词感（如"哈哈哈哈"会被识别为重复，但这属于语气，跳过单字叠词）
        result.append({"word": gram, "count": cnt})
        seen.append(gram)
        if len(result) >= 5:
            break
    return result


def analyze_text(text: str) -> AnalysisResult:
    """分析一段文本，返回实时反馈。"""
    result = AnalysisResult(text=text)
    if not text.strip():
        return result

    # 1. 口癖词检测
    result.filler_hits = _count_hits(text, FILLER_WORDS)

    # 2. 模糊表述检测
    result.hedge_hits = _count_hits(text, HEDGING_WORDS)

    # 3. 不自信表述
    result.uncertain_hits = _count_hits(text, UNCERTAIN_PHRASES)

    # 4. 重复用词（n-gram）
    result.repeated_words = _detect_repeated_ngrams(text)

    # 5. 句子过长（> 60 汉字，一口气说太多）
    for sent in re.split(r"[。！？!?；;\n]", text):
        han_len = len(re.findall(r"[\u4e00-\u9fa5]", sent))
        if han_len > 60:
            result.long_sentences.append(sent.strip())

    # 6. 词重复率（unique / total，汉字 bigram 计）
    han_all = "".join(re.findall(r"[\u4e00-\u9fa5]", text))
    bigrams = [han_all[i:i+2] for i in range(len(han_all) - 1)]
    bigrams = [b for b in bigrams if not all(ch in _STOPWORDS for ch in b)]
    result.word_count = len(bigrams)
    result.unique_word_count = len(set(bigrams))
    if result.word_count > 0:
        result.repetition_rate = 1 - result.unique_word_count / result.word_count

    # 7. 综合判定警告级别
    high_filler = any(h["weight"] >= 3 and h["count"] >= 2 for h in result.filler_hits)
    has_repeat = bool(result.repeated_words)
    if high_filler and has_repeat:
        result.warning_level = "repeat"
        result.has_warning = True
    elif high_filler or has_repeat or result.uncertain_hits:
        result.warning_level = "filler"
        result.has_warning = True

    return result


def compute_speech_rate(text: str, duration_sec: float) -> float:
    """计算语速（字/分钟）。"""
    if duration_sec <= 0:
        return 0.0
    chars = len(re.findall(r"[\u4e00-\u9fa5]", text))
    return round(chars / duration_sec * 60, 1)


def rate_speech_rate(rate: float) -> str:
    """语速评级。"""
    if rate == 0:
        return "未知"
    if rate < 120:
        return "偏慢"
    if rate > 220:
        return "偏快"
    return "适中"
