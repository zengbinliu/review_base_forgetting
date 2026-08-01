"""大模型：出题与语义判分（支持 Cursor crsr_ Key / OpenAI 兼容接口）。"""
from __future__ import annotations

import json
import re
from typing import Any, List, Optional

from app.config import get_api_key, load_settings
from app.cursor_client import generate_via_cursor_agent
from app.quiz_rules import (
    QTYPE_CHOICE,
    QTYPE_JUDGE,
    QTYPE_LABELS,
    QTYPE_SCENARIO,
    QTYPE_SHORT,
    VALID_QTYPES,
    decide_question_count,
)


class LLMError(Exception):
    pass


def _is_cursor_key(api_key: str) -> bool:
    return api_key.strip().startswith("crsr_")


def _extract_json(text: str) -> Any:
    """从模型输出中提取 JSON；容忍 markdown 围栏与常见格式问题。"""
    if text is None:
        raise LLMError("模型返回为空")
    text = str(text).strip()
    if not text:
        raise LLMError("模型返回为空")

    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```\s*$", "", text)

    candidates = [text]
    # 优先取第一个完整对象/数组片段
    for m in re.finditer(r"(\{[\s\S]*\}|\[[\s\S]*\])", text):
        candidates.append(m.group(1))

    errors: list[str] = []
    for cand in candidates:
        for variant in _json_variants(cand):
            try:
                return json.loads(variant)
            except json.JSONDecodeError as e:
                errors.append(f"{e}")
                continue

    # 判分场景兜底：从文本里抠 correct / feedback
    grade = _fallback_grade_fields(text)
    if grade is not None:
        return grade

    detail = errors[-1] if errors else "unknown"
    raise LLMError(f"无法解析模型返回的 JSON（{detail}）: {text[:400]}")


def _json_variants(raw: str) -> list[str]:
    """生成若干可尝试的 JSON 变体。"""
    s = raw.strip()
    variants = [s]
    # 去掉尾随逗号：{"a":1,} / [1,2,]
    variants.append(re.sub(r",\s*([}\]])", r"\1", s))
    # 智能引号 → 标准引号
    variants.append(
        s.replace("“", '"')
        .replace("”", '"')
        .replace("‘", "'")
        .replace("’", "'")
    )
    # 单引号键值（简单场景）
    if "'" in s and '"' not in s:
        variants.append(s.replace("'", '"'))
    # 去重且保序
    seen = set()
    out = []
    for v in variants:
        if v not in seen:
            seen.add(v)
            out.append(v)
    return out


def _fallback_grade_fields(text: str) -> Optional[dict]:
    """当 JSON 损坏时，尽量恢复判分结果。"""
    correct: Optional[bool] = None
    m = re.search(r'"?correct"?\s*[:=]\s*(true|false)', text, re.IGNORECASE)
    if m:
        correct = m.group(1).lower() == "true"
    else:
        # 中文兜底
        if re.search(r"(判断)?正确|答对|可以给分", text) and not re.search(
            r"不正确|答错|有误|不够", text
        ):
            correct = True
        elif re.search(r"不正确|答错|有误|错误|不够准确", text):
            correct = False

    feedback = ""
    fm = re.search(
        r'"feedback"\s*:\s*"(.*)"\s*,?\s*(?:"|\})',
        text,
        re.DOTALL | re.IGNORECASE,
    )
    if fm:
        feedback = fm.group(1)
    else:
        fm2 = re.search(r'"feedback"\s*:\s*"(.*?)"', text, re.DOTALL | re.IGNORECASE)
        if fm2:
            feedback = fm2.group(1)
        else:
            # 无引号 feedback
            fm3 = re.search(
                r'"?feedback"?\s*[:=]\s*[「"\']?(.*?)[」"\']?\s*(?:,|\n|$)',
                text,
                re.IGNORECASE,
            )
            if fm3:
                feedback = fm3.group(1).strip().rstrip("}").strip()

    if correct is None:
        return None
    feedback = (
        feedback.replace('\\"', '"').replace("\\n", "\n").strip()
        or ("回答正确。" if correct else "回答不够准确，请复习相关知识点。")
    )
    return {"correct": correct, "feedback": feedback}


def _messages_to_prompt(messages: List[dict]) -> str:
    parts = []
    for m in messages:
        role = m.get("role", "user")
        content = m.get("content", "")
        parts.append(f"[{role}]\n{content}")
    parts.append(
        "\n重要：不要写代码、不要调用工具、不要修改文件。"
        "只输出任务要求的文本结果。"
    )
    return "\n\n".join(parts)


def _friendly_http_error(status: int, body: str, *, base_url: str = "", model: str = "") -> str:
    text = (body or "").strip()
    low = text.lower()
    # Cloudflare / 网关 HTML 502
    if status in (502, 503, 504) or "bad gateway" in low or "<!doctype html" in low:
        return (
            f"LLM 网关暂时不可用（HTTP {status}）。"
            f"当前地址：{base_url or '-'}，模型：{model or '-'}。"
            "这通常是上游服务抖动/过载，请稍后重试；"
            "也可在设置里换用 gpt-5.4-mini / gpt-5.4 等更轻量模型。"
        )
    # 截断 JSON/文本错误
    if text.startswith("{") or text.startswith("["):
        return f"LLM API 错误 {status}: {text[:400]}"
    # 去掉 HTML 标签后取摘要
    plain = re.sub(r"<[^>]+>", " ", text)
    plain = re.sub(r"\s+", " ", plain).strip()
    if plain:
        return f"LLM API 错误 {status}: {plain[:240]}"
    return f"LLM API 错误 {status}"


async def chat_completion(
    messages: list,
    temperature: float = 0.3,
    *,
    json_object: bool = False,
) -> str:
    settings = load_settings()
    api_key = get_api_key()
    if not api_key or api_key.startswith("sk-your-key"):
        raise LLMError("未配置 LLM_API_KEY，请在设置页或 .env 中填写。")

    model = (settings.get("llm_model") or "auto").strip()
    base_url = (settings.get("llm_base_url") or "https://api.openai.com/v1").rstrip("/")
    print(f"[llm] base_url={base_url} model={model} key=...{(api_key[-4:] if api_key else '')}")

    if _is_cursor_key(api_key):
        try:
            return await generate_via_cursor_agent(
                api_key, _messages_to_prompt(messages), model_id=model
            )
        except Exception as e:
            detail = str(e).strip() or repr(e)
            raise LLMError(
                f"Cursor Agent 调用失败: {type(e).__name__}: {detail}"
            ) from e

    import asyncio
    import requests

    url = f"{base_url}/chat/completions"
    payload: dict = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
    }
    if json_object:
        payload["response_format"] = {"type": "json_object"}
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    def _once(use_json_object: bool) -> "requests.Response":
        body = dict(payload)
        if not use_json_object:
            body.pop("response_format", None)
        session = requests.Session()
        session.trust_env = False
        return session.post(url, headers=headers, json=body, timeout=180)

    last_err = ""
    # 网关抖动：对 502/503/504 自动重试
    for attempt in range(1, 4):
        try:
            use_jo = bool(json_object and attempt == 1)
            resp = await asyncio.to_thread(_once, use_jo)
            # response_format 不被支持时降级再试一次（不算重试次数浪费）
            if (
                use_jo
                and resp.status_code >= 400
                and "response_format" in (resp.text or "")
            ):
                resp = await asyncio.to_thread(_once, False)
        except requests.exceptions.Timeout as e:
            last_err = f"LLM 请求超时（模型 {model}）。请稍后重试或换用更快模型。"
            if attempt < 3:
                await asyncio.sleep(1.2 * attempt)
                continue
            raise LLMError(last_err) from e
        except requests.exceptions.RequestException as e:
            last_err = f"LLM 网络错误: {type(e).__name__}: {e or repr(e)}"
            if attempt < 3:
                await asyncio.sleep(1.2 * attempt)
                continue
            raise LLMError(last_err) from e

        if resp.status_code in (502, 503, 504):
            last_err = _friendly_http_error(
                resp.status_code, resp.text, base_url=base_url, model=model
            )
            print(f"[llm] retry {attempt}/3 after {resp.status_code}")
            if attempt < 3:
                await asyncio.sleep(1.5 * attempt)
                continue
            raise LLMError(last_err)

        if resp.status_code >= 400:
            raise LLMError(
                _friendly_http_error(
                    resp.status_code, resp.text, base_url=base_url, model=model
                )
            )

        try:
            data = resp.json()
        except Exception as e:
            raise LLMError(
                _friendly_http_error(resp.status_code, resp.text, base_url=base_url, model=model)
            ) from e
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as e:
            raise LLMError(f"LLM 响应格式异常: {data}") from e

    raise LLMError(last_err or "LLM 调用失败")


def _normalize_question(item: dict) -> Optional[dict]:
    if not isinstance(item, dict):
        return None
    stem = str(item.get("stem") or item.get("question") or "").strip()
    qtype = str(item.get("qtype") or item.get("type") or QTYPE_SHORT).strip().lower()
    if qtype in ("选择题", "单选", "mcq", "multiple_choice"):
        qtype = QTYPE_CHOICE
    elif qtype in ("判断题", "判断", "true_false", "tf", "boolean"):
        qtype = QTYPE_JUDGE
    elif qtype in ("场景题", "场景", "case", "application"):
        qtype = QTYPE_SCENARIO
    elif qtype in ("简答题", "简答", "qa", "open"):
        qtype = QTYPE_SHORT
    if qtype not in VALID_QTYPES:
        qtype = QTYPE_SHORT

    ref = str(
        item.get("reference_answer")
        or item.get("answer")
        or item.get("reference")
        or ""
    ).strip()

    options: list = []
    raw_opts = item.get("options")
    if isinstance(raw_opts, list):
        options = [str(o).strip() for o in raw_opts if str(o).strip()]
    elif isinstance(raw_opts, dict):
        # {"A":"..","B":".."} → ["A. ..", ...]
        for k in sorted(raw_opts.keys()):
            options.append(f"{k}. {raw_opts[k]}".strip())

    if qtype == QTYPE_CHOICE:
        if len(options) < 2:
            return None
        # reference_answer 应为选项字母或完整选项文本
        if not ref and options:
            return None
    elif qtype == QTYPE_JUDGE:
        # 统一成 正确/错误
        low = ref.lower().replace(" ", "")
        if low in ("true", "t", "yes", "y", "对", "是", "正确", "√"):
            ref = "正确"
        elif low in ("false", "f", "no", "n", "错", "否", "错误", "×", "x"):
            ref = "错误"
        else:
            if "正确" in ref and "错误" not in ref:
                ref = "正确"
            elif "错误" in ref:
                ref = "错误"
            else:
                return None
        options = ["正确", "错误"]
    else:
        if not ref:
            return None

    if not stem or not ref:
        return None
    return {
        "stem": stem,
        "qtype": qtype,
        "reference_answer": ref,
        "options": options,
        "options_json": json.dumps(options, ensure_ascii=False) if options else "",
    }


async def generate_questions(
    title: str,
    content: str,
    code_snippet: str = "",
    attachment_summary: str = "",
    count: Optional[int] = None,
    web_search_context: str = "",
    existing_stems: Optional[List[str]] = None,
) -> list:
    material = f"标题：{title}\n\n正文：\n{content or '（无）'}"
    if code_snippet:
        material += f"\n\n代码：\n{code_snippet}"
    if attachment_summary:
        material += f"\n\n附件摘要：\n{attachment_summary}"
    if web_search_context:
        material += f"\n\n联网检索摘要（仅供补充，冲突时以本地知识点为准）：\n{web_search_context}"

    if count is None:
        count = decide_question_count(title, content, code_snippet, attachment_summary)
    count = max(2, min(int(count), 5))

    priority = (
        "出题时以「本地知识点」为主；联网摘要仅用于补充背景、对比或扩展场景，"
        "不得编造与本地材料矛盾的结论。"
        if web_search_context
        else "仅依据下方本地知识点出题，不要臆造材料中未出现的关键业务规则。"
    )

    avoid_block = ""
    stems = [s.strip() for s in (existing_stems or []) if s and str(s).strip()]
    if stems:
        listed = "\n".join(f"- {s[:200]}" for s in stems[:40])
        avoid_block = (
            "\n已有题目（请出新题，避免重复或高度相似）：\n" + listed + "\n"
        )

    prompt = f"""你是一位严谨的学习教练。请根据下列材料，出恰好 {count} 道复习题。
{priority}
{avoid_block}
题型要求（尽量多样化，至少覆盖 2 种题型）：
- short：简答题（开放作答）
- choice：单选题（必须给 4 个 options，reference_answer 填正确选项字母如 "B" 或完整选项文本）
- judge：判断题（reference_answer 只能是 "正确" 或 "错误"）
- scenario：场景题（给出实际场景，让用户说明做法/原因，开放作答）

严格返回 JSON 数组，不要 Markdown，格式示例：
[
  {{"qtype":"choice","stem":"题目","options":["A. ...","B. ...","C. ...","D. ..."],"reference_answer":"B"}},
  {{"qtype":"judge","stem":"说法…","reference_answer":"正确"}},
  {{"qtype":"scenario","stem":"场景描述…问：你会怎么做？","reference_answer":"要点…"}},
  {{"qtype":"short","stem":"简答题…","reference_answer":"要点…"}}
]

材料：
{material[:14000]}
"""
    raw = await chat_completion(
        [
            {"role": "system", "content": "你只输出合法 JSON，不输出其他说明。"},
            {"role": "user", "content": prompt},
        ],
        temperature=0.4,
    )
    data = _extract_json(raw)
    if not isinstance(data, list) or not data:
        raise LLMError("模型未返回有效题目列表")

    questions = []
    for item in data[:count]:
        q = _normalize_question(item)
        if q:
            questions.append(q)
    if not questions:
        raise LLMError("解析后无有效题目")
    return questions


def _normalize_practice_question(item: dict, *, interview: bool = False) -> Optional[dict]:
    if not isinstance(item, dict):
        return None
    if interview:
        stem = str(item.get("stem") or item.get("question") or "").strip()
        ref = str(
            item.get("reference_answer")
            or item.get("answer")
            or item.get("key_points")
            or ""
        ).strip()
        if not stem or not ref:
            return None
        followups = item.get("followups") or item.get("follow_ups") or []
        if isinstance(followups, str):
            followups = [followups]
        if not isinstance(followups, list):
            followups = []
        followups = [str(x).strip() for x in followups if str(x).strip()][:4]
        return {
            "qtype": "interview",
            "stem": stem,
            "reference_answer": ref,
            "explanation": str(item.get("explanation") or item.get("focus") or "").strip(),
            "extension": str(item.get("extension") or "").strip(),
            "followups": followups,
            "difficulty": str(item.get("difficulty") or "中级").strip() or "中级",
            "source_hint": str(item.get("source_hint") or "").strip(),
        }

    q = _normalize_question(item)
    if not q:
        return None
    q["explanation"] = str(item.get("explanation") or "").strip()
    q["extension"] = str(item.get("extension") or "").strip()
    q["source_hint"] = str(item.get("source_hint") or "").strip()
    return q


async def generate_exam_paper(
    *,
    material: str,
    titles: List[str],
    count: int = 8,
    expand: bool = False,
    web_search_context: str = "",
) -> list:
    count = max(4, min(int(count), 20))
    title_line = "、".join(titles[:8]) if titles else "所选知识点"
    if expand:
        scope = (
            "以本地知识点为核心，允许适度拓展相邻概念、易混点与工程实践场景；"
            "拓展内容须与主题相关，并在 source_hint 标明「拓展」。"
        )
    else:
        scope = "严格依据本地知识点出题，不要编造材料中未出现的关键结论。"

    web_block = ""
    if web_search_context:
        web_block = f"\n联网摘要（仅供拓展参考）：\n{web_search_context}\n"

    prompt = f"""你是资深出题老师。请基于下列「已学知识点」生成一份综合试卷，恰好 {count} 道题。
覆盖知识点：{title_line}
出题范围：{scope}

题型尽量多样（short / choice / judge / scenario），可跨知识点综合。
每题额外提供：
- explanation：正确答案简要解释
- extension：1-2 句相关拓展
- source_hint：来源提示（如「知识点：XXX」或「拓展」）

严格返回 JSON 数组：
[
  {{"qtype":"choice","stem":"...","options":["A. ...","B. ...","C. ...","D. ..."],"reference_answer":"B","explanation":"...","extension":"...","source_hint":"..."}},
  {{"qtype":"short","stem":"...","reference_answer":"...","explanation":"...","extension":"...","source_hint":"..."}}
]

材料：
{material[:12000]}
{web_block}
"""
    raw = await chat_completion(
        [
            {"role": "system", "content": "你只输出合法 JSON 数组，不输出其他说明。"},
            {"role": "user", "content": prompt},
        ],
        temperature=0.45,
    )
    data = _extract_json(raw)
    if not isinstance(data, list) or not data:
        raise LLMError("模型未返回有效试卷题目")
    questions = []
    for item in data[:count]:
        q = _normalize_practice_question(item, interview=False)
        if q:
            questions.append(q)
    if len(questions) < max(3, count // 2):
        raise LLMError("试卷有效题目过少，请重试")
    return questions


async def generate_interview_questions(
    *,
    material: str,
    titles: List[str],
    count: int = 6,
    expand: bool = False,
    web_search_context: str = "",
) -> list:
    count = max(3, min(int(count), 15))
    title_line = "、".join(titles[:8]) if titles else "所选知识点"
    if expand:
        scope = (
            "以本地知识点为核心，可结合真实面试场景做适度拓展（项目深挖、对比选型、排障）；"
            "拓展须相关，并在 source_hint 标明「拓展」。"
        )
    else:
        scope = "严格围绕本地知识点设计面试问题，不要跑题。"

    web_block = ""
    if web_search_context:
        web_block = f"\n联网摘要（仅供拓展参考）：\n{web_search_context}\n"

    prompt = f"""你是资深技术面试官。请基于下列「已学知识点」生成 {count} 道面试题。
考察方向：{title_line}
出题范围：{scope}

要求：
- 像真实面试口语化提问
- 覆盖原理、对比、落地实践、排障中的至少两类
- difficulty：初级/中级/高级
- reference_answer：候选人应答要点（条理清晰）
- explanation：本题考察点
- followups：1-3 个追问
- extension：可补充的关联知识
- source_hint：来源提示

严格返回 JSON 数组：
[
  {{
    "stem":"面试官提问…",
    "difficulty":"中级",
    "reference_answer":"要点1；要点2…",
    "explanation":"考察…",
    "followups":["追问1","追问2"],
    "extension":"…",
    "source_hint":"知识点：XXX"
  }}
]

材料：
{material[:12000]}
{web_block}
"""
    raw = await chat_completion(
        [
            {"role": "system", "content": "你只输出合法 JSON 数组，不输出其他说明。"},
            {"role": "user", "content": prompt},
        ],
        temperature=0.5,
    )
    data = _extract_json(raw)
    if not isinstance(data, list) or not data:
        raise LLMError("模型未返回有效面试题")
    questions = []
    for item in data[:count]:
        q = _normalize_practice_question(item, interview=True)
        if q:
            questions.append(q)
    if len(questions) < max(2, count // 2):
        raise LLMError("面试题有效数量过少，请重试")
    return questions


def _choice_matches(user_answer: str, reference_answer: str, options: list) -> bool:
    ua = user_answer.strip()
    ref = reference_answer.strip()
    if not ua:
        return False
    if ua.upper() == ref.upper():
        return True
    if ua == ref:
        return True
    # 用户选了 "B. xxx"，参考是 "B"
    if len(ref) == 1 and ua.upper().startswith(ref.upper()):
        return True
    # 用户只写字母，参考是完整选项
    if len(ua) == 1:
        letter = ua.upper()
        for opt in options:
            if opt.upper().startswith(letter + ".") or opt.upper().startswith(letter + " "):
                if opt == ref or letter == ref.upper():
                    return True
                if ref in opt or opt in ref:
                    return True
    return False


def _judge_matches(user_answer: str, reference_answer: str) -> Optional[bool]:
    ua = user_answer.strip().lower().replace(" ", "")
    mapping_true = {"正确", "对", "是", "true", "t", "yes", "y", "√"}
    mapping_false = {"错误", "错", "否", "false", "f", "no", "n", "×", "x"}
    if ua in mapping_true:
        user_norm = "正确"
    elif ua in mapping_false:
        user_norm = "错误"
    else:
        return None
    return user_norm == reference_answer.strip()


def _format_reference_answer(
    reference_answer: str,
    qtype: str = QTYPE_SHORT,
    options: Optional[list] = None,
) -> str:
    """把参考答案整理成可读文本（选择题尽量展开完整选项）。"""
    ref = (reference_answer or "").strip()
    options = options or []
    if not ref:
        return ""
    if (qtype or "").lower() == QTYPE_CHOICE and options:
        letter = ref.upper()[:1] if len(ref) == 1 else ""
        if letter and letter.isalpha():
            for opt in options:
                ou = opt.upper().strip()
                if ou.startswith(letter + ".") or ou.startswith(letter + " "):
                    return opt.strip()
        # 已是完整选项或其它写法
        for opt in options:
            if ref == opt or ref in opt or opt in ref:
                return opt.strip()
    return ref


def _ensure_wrong_feedback(
    feedback: str,
    *,
    correct: bool,
    reference_answer: str,
    qtype: str = QTYPE_SHORT,
    options: Optional[list] = None,
) -> str:
    """答错时保证反馈中带上正确答案。"""
    feedback = (feedback or "").strip()
    if correct:
        return feedback or "回答正确。"
    pretty = _format_reference_answer(reference_answer, qtype, options)
    if not pretty:
        return feedback or "回答不够准确，请复习相关知识点。"
    # 已包含则不重复追加
    if pretty in feedback or f"正确答案" in feedback:
        return feedback
    base = feedback or "回答不够准确。"
    return f"{base}\n正确答案：{pretty}"


def _clean_teach_text(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = re.sub(r"^(解释|说明|拓展|知识拓展)[：:]\s*", "", text)
    return text.strip()


async def grade_answer(
    stem: str,
    reference_answer: str,
    user_answer: str,
    qtype: str = QTYPE_SHORT,
    options: Optional[list] = None,
    web_search_context: str = "",
) -> dict:
    options = options or []
    qtype = (qtype or QTYPE_SHORT).lower()
    label = QTYPE_LABELS.get(qtype, "题目")
    pretty_ref = _format_reference_answer(reference_answer, qtype, options)

    # 本地可判定的题型先得到 correct，再统一请模型给解释与拓展
    local_correct: Optional[bool] = None
    if qtype == QTYPE_JUDGE:
        local_correct = _judge_matches(user_answer, reference_answer)
    elif qtype == QTYPE_CHOICE and _choice_matches(
        user_answer, reference_answer, options
    ):
        local_correct = True

    opts_text = ""
    if options:
        opts_text = "选项：\n" + "\n".join(options)

    web_block = ""
    if web_search_context:
        web_block = (
            f"\n联网检索摘要（仅供参考，冲突时以参考答案与题目为准）：\n{web_search_context}\n"
        )

    known = ""
    if local_correct is not None:
        known = (
            f"\n已知判分结果：correct={str(local_correct).lower()}。"
            "请保持该结果，重点写 explanation 与 extension。\n"
        )

    prompt = f"""请根据语义判断用户答案是否正确（意思对即可；选择题核对选项即可）。
题型：{label}
优先依据「参考答案」；联网摘要仅作补充背景。
{known}
严格返回一个 JSON 对象，字段只能是：
{{
  "correct": true或false,
  "feedback": "一两句中文总评",
  "explanation": "针对正确答案的解释：为什么对、关键依据/原理是什么（2-5句）",
  "extension": "相关知识拓展：易混点、实践建议或相邻概念（2-4句）"
}}
要求：
1. 无论 correct 真假，都必须给出有实质内容的 explanation 与 extension，不要空字符串。
2. 不要使用未转义的英文双引号；代码或引号请改用「」或 Markdown 代码围栏。
3. feedback 保持简短；详细内容放在 explanation / extension。

题目：{stem}
{opts_text}
参考答案：{reference_answer}
用户答案：{user_answer}
{web_block}
"""
    raw = await chat_completion(
        [
            {
                "role": "system",
                "content": "你只输出合法 JSON 对象，不要 Markdown 外壳，不要其它说明。",
            },
            {"role": "user", "content": prompt},
        ],
        temperature=0.2,
        json_object=True,
    )
    data = _extract_json(raw)
    if not isinstance(data, dict):
        raise LLMError("判分结果不是对象")

    if local_correct is not None:
        correct = bool(local_correct)
    else:
        correct = bool(data.get("correct"))

    feedback = str(data.get("feedback") or "").strip() or (
        "回答正确。" if correct else "回答不够准确，请复习相关知识点。"
    )
    feedback = _ensure_wrong_feedback(
        feedback,
        correct=correct,
        reference_answer=reference_answer,
        qtype=qtype,
        options=options,
    )
    explanation = _clean_teach_text(
        data.get("explanation") or data.get("explain") or data.get("reason")
    )
    extension = _clean_teach_text(
        data.get("extension")
        or data.get("knowledge_extension")
        or data.get("expand")
        or data.get("extra")
    )
    if not explanation:
        explanation = (
            f"参考要点：{pretty_ref or reference_answer}"
            if (pretty_ref or reference_answer)
            else "请结合题目材料理解正确答案的依据。"
        )
    if not extension:
        extension = (
            "建议回到知识点原文，对照相邻概念再巩固一遍，并尝试用自己的话复述关键步骤。"
        )

    return {
        "correct": correct,
        "feedback": feedback,
        "reference_answer": pretty_ref or (reference_answer or "").strip(),
        "explanation": explanation,
        "extension": extension,
    }
