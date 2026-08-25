"""表达分析 - 文本规则层。

实时检测多个维度的表达问题：
1. 口癖候选（明确填充停顿 + 有上下文证据的连接词）
2. 模糊/不确定表述（可能/应该/好像…）—— 单独维度，不混入口癖
3. 重复用词（n-gram 检测，覆盖中文无空格场景）
4. 保留或自我修正措辞（我不太确定/说错了/怎么说呢…）
5. ASR 文本长句（仅作文本结构观察）
6. 语速（配合时长使用）
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from difflib import SequenceMatcher

# 常见中文口癖候选（按权重分级）。
#
# “这个/那个/其实/然后”在大多数句子中都承担正常语义，不能用
# ``text.count`` 直接当作口癖。它们只在独立成分或相邻重复时计入；
# “嗯/呃”则是较明确的填充停顿，可直接计入。
FILLER_WORDS: dict[str, int] = {
    "嗯": 3,
    "呃": 3,
    "就是说": 3,
    "就是": 3,
    "那个": 3,
    "这个": 3,
    "然后": 2,
    "所以说": 2,
    "其实": 1,
    "啊": 1,
}

_STRONG_FILLERS = {"嗯", "呃"}
_CONTEXTUAL_FILLERS = set(FILLER_WORDS) - _STRONG_FILLERS
_FILLER_BOUNDARIES = set(" \t\r\n，,。！!？?；;：:、…—-~")

# 只把紧邻范围内的再次出现视为口语重说/重启候选。跨越更长距离的
# 主题词复现是正常语篇衔接，不应进入连贯性扣分。
_REPEAT_LOOKBACK = 4

_SEMANTIC_CONTRADICTIONS = (
    ("挽回", "造成"),
    ("增加", "减少"),
    ("提升", "下降"),
    ("盈利", "亏损"),
    ("完成", "未完成"),
    ("通过", "未通过"),
    ("支持", "反对"),
)

# 模糊/不确定表述（独立于口癖）
HEDGING_WORDS: dict[str, int] = {
    "可能": 2, "应该": 2, "好像": 2, "也许": 1, "大概": 1, "不一定": 2,
    "差不多": 2, "大概是": 2, "我猜": 2, "貌似": 2,
}

# 保留或自我修正措辞
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
    uncertain_hits: list[dict] = field(default_factory=list)     # [{word, count}] 保留/自我修正措辞
    repeated_words: list[dict] = field(default_factory=list)     # [{word, count}] 重复用词
    consecutive_repetition_hits: list[dict] = field(default_factory=list)
    """只记录定稿文本中的紧邻重复（口吃/重启），不是普通词语复用。"""
    expression_breaks: list[dict] = field(default_factory=list)
    """听者可感知的局部表达断裂；原因归类不决定是否记录。"""
    long_sentences: list[str] = field(default_factory=list)      # 过长的句子原文
    word_count: int = 0
    unique_word_count: int = 0
    repetition_rate: float = 0.0
    has_warning: bool = False                                     # 是否需要高亮
    warning_level: str = "normal"                                # normal/filler/repeat


# 这些词的词法叠词是正常中文表达，不应被当作口吃。刻意重复的
# “然后然后”“我我我”等不在此集合中，仍会被报告。
_NORMAL_REDUPLICATION = {
    "看看", "想想", "试试", "说说", "聊聊", "听听", "问问", "走走", "坐坐",
    "人人", "家家", "年年", "天天", "常常", "往往", "渐渐", "慢慢", "刚刚",
    "仅仅", "处处", "点点", "种种", "多多", "好好", "大大", "早早", "足足",
    "时时", "稍稍", "轻轻", "默默", "悄悄", "一一", "清清楚楚", "明明白白",
}
# 本地 ASR 会在短暂停顿处主动补句号/问号。检测连续重启时，这些
# 机器插入的标点与空格一样只视为分隔符，否则“只有在。只有在。”
# 这类真实重复会被漏掉。
_CONSECUTIVE_REPEAT_SEPARATORS = " \t\r\n，,。.!！?？、；;：:…—-"
_CONSECUTIVE_REPEAT_PATTERN = re.compile(
    rf"(?P<unit>[\u4e00-\u9fff]{{1,4}})"
    rf"(?:[{re.escape(_CONSECUTIVE_REPEAT_SEPARATORS)}]*(?P=unit))+"
)


def detect_consecutive_repetitions(text: str) -> list[dict]:
    """检测定稿文本中的紧邻重复字/词。

    这是有意收窄的口吃信号：只匹配同一字或同一短词连续出现至少两次，
    可跨一个逗号/空格；不扫描 ASR partial，也不把远距离的主题词复现算入。
    常见正常叠词（如“看看”“慢慢”）明确排除。
    """
    if not text or not text.strip():
        return []

    matches: list[dict] = []
    occupied: list[tuple[int, int]] = []
    # 长匹配优先，避免“然后然后”被拆成“后后”等重叠结果。
    candidates = sorted(
        _CONSECUTIVE_REPEAT_PATTERN.finditer(text),
        key=lambda match: (-(match.end() - match.start()), match.start()),
    )
    for match in candidates:
        start, end = match.span()
        if any(start < used_end and end > used_start for used_start, used_end in occupied):
            continue
        unit = match.group("unit")
        # 至少两次；分隔符不计入展示的重复次数。
        remainder = match.group(0)[len(unit):]
        count = 1 + len(re.findall(rf"[{re.escape(_CONSECUTIVE_REPEAT_SEPARATORS)}]*{re.escape(unit)}", remainder))
        if count < 2:
            continue
        # “看 看”在自然语句中很少是口吃，但“我 我”应当提示；
        # 仅对标准叠词做排除。完全相同的短词组即使跨越 ASR 自动补的
        # 句号也仍是连续重启，不能按普通语篇衔接忽略。
        matched_text = match.group(0)
        if (
            len(unit) > 2
            and not any(mark in matched_text for mark in "。.!！？?")
            and re.search(
                rf"[{re.escape(_CONSECUTIVE_REPEAT_SEPARATORS)}]",
                matched_text,
            )
        ):
            # “项目规划，项目规划需要……”可能是正常主语衔接；只有当
            # ASR 用句末标点切开两个完全相同短语时才升级为连续重启。
            continue
        if count == 2 and (
            unit in _NORMAL_REDUPLICATION
            or any(unit * 2 in normal for normal in _NORMAL_REDUPLICATION)
        ) and not re.search(
            rf"[{re.escape(_CONSECUTIVE_REPEAT_SEPARATORS)}]", match.group(0)
        ):
            continue
        # AABB 是常见中文构词（高高兴兴、认认真真），不是说话重启。
        # 两个相邻的 AA 块必须一起排除，否则会分别误报成两个单字口吃。
        if len(unit) == 1 and count == 2:
            left = max(0, start - 2)
            right = min(len(text), end + 2)
            window = text[left:right]
            if re.search(r"([\u4e00-\u9fff])\1([\u4e00-\u9fff])\2", window):
                continue
        matches.append({
            "word": unit,
            "count": count,
            "start": start,
            "end": end,
            "excerpt": match.group(0),
        })
        occupied.append((start, end))
    matches.sort(key=lambda hit: (-hit["count"], -len(hit["word"]), hit["word"]))
    return matches[:5]


_EXPRESSION_SENTENCE_PATTERN = re.compile(r"[^。.!！？?；;\n]+(?:[。.!！？?；;\n]+|$)")
_NON_FINAL_SUFFIXES = (
    "不光是", "不只是", "因为", "所以", "如果", "虽然", "但是", "然而",
    "或者", "还是", "以及", "为了", "通过", "关于", "对于", "需要", "应该",
    "能够", "会有", "是", "有", "在", "把", "被", "让", "给", "向", "从",
    "和", "与", "或", "及", "的", "地", "得",
)
_SHORT_RESPONSE_FRAGMENTS = {"对", "好", "是的", "没错", "嗯", "啊"}
_ENUMERATION_HEADING_PATTERN = re.compile(
    r"^(?:第[一二三四五六七八九十\d]+(?:点|个|项|方面)?|首先|其次|再次|最后)$"
)


def _expression_clauses(text: str) -> list[dict]:
    """按 ASR 的句末停顿切分，同时保留原文位置。"""
    clauses: list[dict] = []
    for match in _EXPRESSION_SENTENCE_PATTERN.finditer(text or ""):
        raw = match.group(0)
        leading = len(raw) - len(raw.lstrip())
        stripped = raw.strip()
        if not stripped:
            continue
        content = re.sub(r"[。.!！？?；;\n]+$", "", stripped).strip(" ，,、：:")
        normalized = _semantic_normalize(content)
        if not normalized:
            continue
        terminators = re.findall(r"[。.!！？?；;\n]+$", stripped)
        clauses.append({
            "text": content,
            "normalized": normalized,
            "start": match.start() + leading,
            "end": match.end(),
            "terminator": terminators[0] if terminators else "",
        })
    return clauses


def _shared_prefix_length(left: str, right: str) -> int:
    length = 0
    for left_char, right_char in zip(left, right):
        if left_char != right_char:
            break
        length += 1
    return length


def _ends_as_unfinished_fragment(value: str) -> bool:
    return any(value.endswith(suffix) for suffix in _NON_FINAL_SUFFIXES)


def _overlaps(span: tuple[int, int], occupied: list[tuple[int, int]]) -> bool:
    start, end = span
    return any(start < used_end and end > used_start for used_start, used_end in occupied)


def detect_expression_breaks(text: str) -> list[dict]:
    """保守检测定稿文本中听者可感知的局部表达断裂。

    这里评价的是表达结果，而不是猜测说话人的心理或口误原因。只有句末
    停顿与未完句、短片段链或同句式立即替换同时出现时才命中；普通逗号
    并列不会被当作断裂。同一片段若已被紧邻重复规则覆盖，也不会再次计数。
    """
    if not text or not text.strip():
        return []

    clauses = _expression_clauses(text)
    if len(clauses) < 2:
        return []

    candidates: list[dict] = []

    # 同一句式在句末停顿后立即换词重说，例如“原来审计。原来采购。”。
    for previous, current in zip(clauses, clauses[1:]):
        previous_tail = re.split(r"[，,、：:]", previous["text"])[-1].strip()
        left = _semantic_normalize(previous_tail)
        right = current["normalized"]
        if previous["terminator"] and any(mark in previous["terminator"] for mark in "！？!?"):
            continue
        shared = _shared_prefix_length(left, right)
        if (
            left != right
            and 3 <= len(left) <= 12
            and 3 <= len(right) <= 12
            and shared >= 2
            and len(left) - shared >= 1
            and len(right) - shared >= 1
        ):
            tail_offset = previous["text"].rfind(previous_tail)
            event_start = previous["start"] + max(0, tail_offset)
            candidates.append({
                "event_id": f"self_correction:{event_start}:{current['end']}",
                "kind": "self_correction",
                "recovered": True,
                "weight": 0.6,
                "start": event_start,
                "end": current["end"],
                "excerpt": text[event_start:current["end"]].strip(),
                "description": "前一句很快被后一句替换，听者需要重新理解",
                "priority": 3,
            })

    # 连续短句只有在形成片段链，或带有明显未完结词尾时才计入。
    index = 0
    while index < len(clauses):
        run: list[dict] = []
        cursor = index
        while cursor < len(clauses):
            clause = clauses[cursor]
            if (
                len(clause["normalized"]) > 6
                or any(mark in clause["terminator"] for mark in "！？!?")
            ):
                break
            run.append(clause)
            cursor += 1
        if len(run) >= 2:
            has_unfinished = any(
                _ends_as_unfinished_fragment(clause["normalized"])
                for clause in run[:-1]
            )
            has_single_char = any(len(clause["normalized"]) == 1 for clause in run)
            enumeration_count = sum(
                bool(_ENUMERATION_HEADING_PATTERN.fullmatch(clause["normalized"]))
                for clause in run
            )
            if (
                enumeration_count < 2
                and (len(run) >= 3 or has_unfinished or has_single_char)
            ):
                candidates.append({
                    "event_id": f"fragmented_clause:{run[0]['start']}:{run[-1]['end']}",
                    "kind": "fragmented_clause",
                    "recovered": cursor < len(clauses),
                    "weight": min(1.2, 0.6 + 0.2 * (len(run) - 2)),
                    "start": run[0]["start"],
                    "end": run[-1]["end"],
                    "excerpt": text[run[0]["start"]:run[-1]["end"]].strip(),
                    "description": "一句话被拆成多个短片段，理解过程被打断",
                    "priority": 2,
                })
        index = max(cursor, index + 1)

    # 较长片段也可能在“应该是。/需要有。/或者。”等位置提前中断。
    for previous, current in zip(clauses, clauses[1:]):
        left = previous["normalized"]
        if (
            len(left) <= 14
            and previous["text"] not in _SHORT_RESPONSE_FRAGMENTS
            and not any(mark in previous["terminator"] for mark in "！？!?")
            and _ends_as_unfinished_fragment(left)
        ):
            candidates.append({
                "event_id": f"unfinished_clause:{previous['start']}:{current['end']}",
                "kind": "unfinished_clause",
                "recovered": True,
                "weight": 0.7,
                "start": previous["start"],
                "end": current["end"],
                "excerpt": text[previous["start"]:current["end"]].strip(),
                "description": "句子在尚未表达完整的位置停住后继续",
                "priority": 1,
            })

    repeated_hits = detect_consecutive_repetitions(text)
    repeated_spans = [
        (int(hit["start"]), int(hit["end"]))
        for hit in repeated_hits
        if isinstance(hit.get("start"), int) and isinstance(hit.get("end"), int)
    ]
    selected: list[dict] = []
    occupied: list[tuple[int, int]] = []
    for candidate in sorted(
        candidates,
        key=lambda item: (-item["priority"], -(item["end"] - item["start"]), item["start"]),
    ):
        span = (candidate["start"], candidate["end"])
        if _overlaps(span, occupied) or (
            candidate["priority"] < 3 and _overlaps(span, repeated_spans)
        ):
            continue
        candidate.pop("priority", None)
        selected.append(candidate)
        occupied.append(span)

    # 紧邻重复本身也是一次听者可感知的重启。若它已经落在上述断裂片段
    # 中，只作为同一事件的佐证；否则补成一个独立事件，不在评分层重复扣分。
    for hit in repeated_hits:
        span = (int(hit["start"]), int(hit["end"]))
        overlapping = next(
            (
                event for event in selected
                if _overlaps(span, [(int(event["start"]), int(event["end"]))])
            ),
            None,
        )
        if overlapping is not None:
            overlapping.setdefault("supporting_evidence", []).append("紧邻重复")
            continue
        count = int(hit.get("count", 2))
        selected.append({
            "event_id": f"consecutive_repeat:{span[0]}:{span[1]}",
            "kind": "consecutive_repeat",
            "recovered": True,
            "weight": min(0.8, 0.5 + 0.15 * max(0, count - 2)),
            "start": span[0],
            "end": span[1],
            "excerpt": str(hit.get("excerpt", "")).strip(),
            "description": "紧邻内容重复，听者感受到一次表达重启",
            "supporting_evidence": ["紧邻重复"],
        })

    selected.sort(key=lambda item: item["start"])
    return selected


def _semantic_normalize(text: str) -> str:
    """归一化可比文本，同时保留数字/英文以识别新增数据。"""
    normalized = unicodedata.normalize("NFKC", text or "").lower()
    # 标点和空白不影响语义相似度，但数字、英文词必须保留。
    return "".join(
        char for char in normalized
        if re.match(r"[\u4e00-\u9fffA-Za-z0-9%]", char)
    )


def _semantic_ngrams(text: str) -> set[str]:
    """提取中文局部片段；短句不依赖外部分词模型。"""
    normalized = _semantic_normalize(text)
    grams: set[str] = set()
    for size in (2, 3, 4):
        grams.update(normalized[index:index + size] for index in range(len(normalized) - size + 1))
    return grams


def _semantic_shared_phrases(previous: str, current: str) -> list[str]:
    shared = _semantic_ngrams(previous) & _semantic_ngrams(current)
    # 只展示最长的几个片段，避免同时展示“推动”“成功推动”“成功推动了”。
    ordered = sorted(shared, key=lambda value: (-len(value), value))
    selected: list[str] = []
    for phrase in ordered:
        if any(phrase in existing for existing in selected):
            continue
        selected.append(phrase)
        if len(selected) >= 4:
            break
    return selected


def _shared_content_bigrams(previous: str, current: str) -> set[str]:
    """提取两句共同的非数字内容词片段，辅助识别同一数据结论的改写。"""
    def _content_bigrams(value: str) -> set[str]:
        normalized = _semantic_normalize(value)
        normalized = re.sub(r"\d+(?:\.\d+)?%?", "", normalized)
        normalized = re.sub(r"(?:百分之|万元|亿元|万|亿|元)", "", normalized)
        return {
            normalized[index:index + 2]
            for index in range(len(normalized) - 1)
        }

    return _content_bigrams(previous) & _content_bigrams(current)


def _split_semantic_sentences(text: str) -> list[str]:
    """把一个 ASR final 块拆成可比较句子，同时保留原句标点。"""
    return [
        sentence.strip()
        for sentence in re.split(r"(?<=[。！？!?；;\n])", text or "")
        if sentence.strip()
    ]


def _detect_semantic_repetition_single(
    current_sentence: str,
    previous_sentences: list[str] | tuple[str, ...],
    *,
    max_history: int = 8,
) -> dict | None:
    """保守比较一条句子与最近历史句，命中时返回句对。

    这是低延迟文本启发式，不调用大模型。除了相似度，还检查新增信息：
    新数字/百分比、明显更长的新增片段会直接取消命中，避免把“同一结论
    + 新证据/结果”误报成重复。
    """
    current = _semantic_normalize(current_sentence)
    if len(current) < 8:
        return None
    current_numbers = set(re.findall(r"\d+(?:\.\d+)?%?", current))
    current_grams = _semantic_ngrams(current)
    if len(current_grams) < 3:
        return None

    best: tuple[float, str, float, float] | None = None
    for previous_sentence in list(previous_sentences)[-max_history:]:
        previous = _semantic_normalize(previous_sentence)
        if len(previous) < 8:
            continue
        if any(
            (left in previous and right in current)
            or (right in previous and left in current)
            for left, right in _SEMANTIC_CONTRADICTIONS
        ):
            continue
        previous_numbers = set(re.findall(r"\d+(?:\.\d+)?%?", previous))
        # 不同数字通常就是新增的结果/证据（“提升10%”→“提升18%”）。
        if current_numbers != previous_numbers:
            continue
        previous_grams = _semantic_ngrams(previous)
        if not previous_grams:
            continue
        sequence_ratio = SequenceMatcher(None, previous, current, autojunk=False).ratio()
        union = previous_grams | current_grams
        gram_jaccard = len(previous_grams & current_grams) / len(union) if union else 0.0
        gram_overlap = len(previous_grams & current_grams) / len(current_grams)
        similarity = max(sequence_ratio, (gram_jaccard + gram_overlap) / 2)
        # 当前句新增了太多内容时，认为是补充而非重复。
        matcher = SequenceMatcher(None, previous, current, autojunk=False)
        matched_chars = sum(block.size for block in matcher.get_matching_blocks())
        added_ratio = max(0.0, 1.0 - matched_chars / len(current))
        # 数字结论常因中文语序变化导致字符相似度偏低，例如
        # “为公司挽回损失1000万元”与“挽回了1000万元损失”。数字相同、
        # 至少三个内容片段相同且没有大量新增信息时，仍视为同一结论改写。
        shared_content = _shared_content_bigrams(previous, current)
        reordered_numeric_claim = (
            bool(current_numbers)
            and current_numbers == previous_numbers
            and sequence_ratio >= 0.58
            and len(shared_content) >= 3
            and added_ratio <= 0.38
        )
        if not reordered_numeric_claim and (
            similarity < 0.72 or gram_overlap < 0.56 or added_ratio > 0.30
        ):
            continue
        candidate = (similarity, previous_sentence, gram_overlap, added_ratio)
        if best is None or candidate[0] > best[0]:
            best = candidate

    if best is None:
        return None
    similarity, previous_sentence, gram_overlap, added_ratio = best
    return {
        "current_sentence": current_sentence,
        "previous_sentence": previous_sentence,
        "similarity": round(similarity, 3),
        "shared_phrases": _semantic_shared_phrases(previous_sentence, current_sentence),
        "new_information_ratio": round(added_ratio, 3),
    }


def detect_semantic_repetitions(
    current_text: str,
    previous_sentences: list[str] | tuple[str, ...],
    *,
    max_history: int = 8,
) -> list[dict]:
    """检测一个 ASR final 块内及其与历史块之间的重复意思。

    本地识别器可能一次定稿多句。旧实现把整个块当成一句，因此用户在
    同一块里完整重复一句也不会命中。这里先拆句，再按说话顺序把当前块
    已出现的句子加入临时历史；新增数据或证据仍由单句规则排除。
    """
    history = [
        sentence
        for block in previous_sentences
        for sentence in _split_semantic_sentences(block)
    ]
    matches: list[dict] = []
    for sentence in _split_semantic_sentences(current_text):
        match = _detect_semantic_repetition_single(
            sentence,
            history,
            max_history=max_history,
        )
        if match:
            matches.append(match)
        history.append(sentence)
    return matches


def detect_semantic_repetition(
    current_sentence: str,
    previous_sentences: list[str] | tuple[str, ...],
    *,
    max_history: int = 8,
) -> dict | None:
    """兼容单条协议：返回当前定稿块里最近一次“重复意思”句对。"""
    matches = detect_semantic_repetitions(
        current_sentence,
        previous_sentences,
        max_history=max_history,
    )
    return matches[-1] if matches else None


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


def _is_contextual_filler(
    text: str,
    start: int,
    end: int,
    word: str,
) -> bool:
    """只在有文本证据时把多义词当作口癖。

    可观察证据限于：该词两侧都是边界，或同一词在只相隔标点/空白的
    位置紧邻重复。这是保守识别，宁可少报，不把“这个问题”之类正常
    指代误报为口癖。
    """
    left_boundary = start == 0 or text[start - 1] in _FILLER_BOUNDARIES
    right_boundary = end == len(text) or text[end] in _FILLER_BOUNDARIES
    if left_boundary and right_boundary:
        return True

    separator = rf"[{re.escape(''.join(_FILLER_BOUNDARIES))}]*"
    previous_same = re.search(rf"{re.escape(word)}{separator}$", text[:start])
    next_same = re.match(rf"{separator}{re.escape(word)}", text[end:])
    return previous_same is not None or next_same is not None


def _count_filler_hits(text: str) -> list[dict]:
    """上下文化统计口癖候选，并优先保留更长词条。"""
    hits: list[dict] = []
    occupied: list[tuple[int, int]] = []
    for word, weight in sorted(FILLER_WORDS.items(), key=lambda item: -len(item[0])):
        count = 0
        occurrences = list(re.finditer(re.escape(word), text))
        for match in occurrences:
            start, end = match.span()
            if any(start < used_end and end > used_start for used_start, used_end in occupied):
                continue
            if word in _STRONG_FILLERS or _is_contextual_filler(text, start, end, word):
                count += 1
        occupied.extend(match.span() for match in occurrences)
        if count:
            hits.append({"word": word, "count": count, "weight": weight})
    return hits


def _positioned_bigrams(text: str) -> list[tuple[int, str]]:
    """转为带原始汉字位置的 bigram 序列，排除纯停用字组合。"""
    flat = "".join(re.findall(r"[\u4e00-\u9fa5]", text))
    return [
        (index, flat[index:index + 2])
        for index in range(len(flat) - 1)
        if not all(char in _STOPWORDS for char in flat[index:index + 2])
    ]


def _near_repetition_rate(positioned_bigrams: list[tuple[int, str]]) -> float:
    """紧邻重说事件占 bigram 数的比例。

    比如“我们我们”、“才才才”会命中，而一个主题词在后文正常再次
    出现不会命中。分母与文本长度同比增长，因此不会像全文词汇多样性
    那样随演讲时长自动恶化。
    """
    if not positioned_bigrams:
        return 0.0
    last_seen: dict[str, int] = {}
    repeat_events = 0
    for index, bigram in positioned_bigrams:
        previous = last_seen.get(bigram)
        if previous is not None and index - previous <= _REPEAT_LOOKBACK:
            repeat_events += 1
        last_seen[bigram] = index
    return repeat_events / len(positioned_bigrams)


def _detect_repeated_ngrams(text: str, n: int = 2, min_count: int = 2) -> list[dict]:
    """n-gram 重复检测：找出局部反复出现的 2~4 字片段。

    中文没有空格，传统"分词"在无词典场景下不可行；n-gram 滑窗
    统计只计入相距不超过固定局部范围的再次出现，避免把长文中
    跨段落的主题词复现标记成口语重启。
    """
    # 只保留汉字（去标点、数字、英文）
    han = re.findall(r"[\u4e00-\u9fa5]+", text)
    flat = "".join(han)
    if len(flat) < n * min_count:
        return []

    counter: Counter = Counter()
    for size in (2, 3, 4):
        last_seen: dict[str, int] = {}
        for i in range(len(flat) - size + 1):
            gram = flat[i:i + size]
            # 跳过包含停用字组合的无意义片段
            if all(ch in _STOPWORDS for ch in gram):
                continue
            # 跳过口癖/模糊词本身（已单独统计）
            if any(gram == w or w in gram for w in {**FILLER_WORDS, **HEDGING_WORDS} if abs(len(w) - size) <= 1):
                continue
            previous = last_seen.get(gram)
            if previous is not None and i - previous <= _REPEAT_LOOKBACK:
                counter[gram] += 1
            last_seen[gram] = i

    # counter 记录的是局部“额外出现”次数，加 1 还原为展示次数。
    candidates = [
        (gram, repeat_count + 1)
        for gram, repeat_count in counter.items()
        if repeat_count + 1 >= min_count
    ]
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

    # 1. 口癖候选检测（多义词要求上下文证据）
    result.filler_hits = _count_filler_hits(text)

    # 2. 模糊表述检测
    result.hedge_hits = _count_hits(text, HEDGING_WORDS)

    # 3. 保留或自我修正措辞
    result.uncertain_hits = _count_hits(text, UNCERTAIN_PHRASES)

    # 4. 重复用词（n-gram）：保留为客观文本事实，但不单独升级强提醒。
    result.repeated_words = _detect_repeated_ngrams(text)

    # 5. 紧邻连续重复：只在定稿文本分析，避免 ASR partial 重写误报。
    result.consecutive_repetition_hits = detect_consecutive_repetitions(text)

    # 6. 局部表达断裂：评价听者实际经历的句子中断，不猜测心理原因。
    result.expression_breaks = detect_expression_breaks(text)

    # 7. ASR 文本长句（> 60 汉字），仅作文本结构观察。
    for sent in re.split(r"[。！？!?；;\n]", text):
        han_len = len(re.findall(r"[\u4e00-\u9fa5]", sent))
        if han_len > 60:
            result.long_sentences.append(sent.strip())

    # 8. 紧邻用词重复率（汉字 bigram 重说事件 / 有效 bigram 数）
    positioned_bigrams = _positioned_bigrams(text)
    bigrams = [bigram for _, bigram in positioned_bigrams]
    result.word_count = len(bigrams)
    result.unique_word_count = len(set(bigrams))
    if result.word_count > 0:
        result.repetition_rate = _near_repetition_rate(positioned_bigrams)

    # 9. 综合判定警告级别。普通词语复用（repeated_words）只是观察值，
    # 不再作为强提醒依据；真正连续重复才升级为 repeat。
    high_filler = any(h["weight"] >= 3 and h["count"] >= 2 for h in result.filler_hits)
    has_repeat = bool(result.consecutive_repetition_hits)
    if has_repeat:
        result.warning_level = "repeat"
        result.has_warning = True
    elif high_filler or result.uncertain_hits:
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
