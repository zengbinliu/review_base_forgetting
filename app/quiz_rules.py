"""按内容长度决定出题数量，并支持多题型。"""
from __future__ import annotations

# 题型
QTYPE_SHORT = "short"  # 简答
QTYPE_CHOICE = "choice"  # 单选
QTYPE_JUDGE = "judge"  # 判断
QTYPE_SCENARIO = "scenario"  # 场景应用

QTYPE_LABELS = {
    QTYPE_SHORT: "简答题",
    QTYPE_CHOICE: "选择题",
    QTYPE_JUDGE: "判断题",
    QTYPE_SCENARIO: "场景题",
}

VALID_QTYPES = set(QTYPE_LABELS.keys())


def material_length(title: str, content: str, code_snippet: str, attachment_summary: str) -> int:
    return len(title or "") + len(content or "") + len(code_snippet or "") + len(
        attachment_summary or ""
    )


def decide_question_count(
    title: str = "",
    content: str = "",
    code_snippet: str = "",
    attachment_summary: str = "",
) -> int:
    """
    按材料总长度自动定题量：
    - 很短 (<200)：2
    - 短 (200–800)：3
    - 中 (800–2000)：4
    - 长 (>2000)：5
    """
    n = material_length(title, content, code_snippet, attachment_summary)
    if n < 200:
        return 2
    if n < 800:
        return 3
    if n < 2000:
        return 4
    return 5
