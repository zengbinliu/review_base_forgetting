"""试卷 / 面试套题：基于已学知识点 + LLM 生成。"""
from __future__ import annotations

import json
from typing import Any, List, Optional

from app.db import db_session
from app.ebbinghaus import now_iso, stage_label
from app.llm import LLMError, generate_exam_paper, generate_interview_questions
from app import services


KIND_EXAM = "exam"
KIND_INTERVIEW = "interview"


def list_learned_items() -> list[dict]:
    """可选知识点：已有内容或已出过题的条目。"""
    with db_session() as conn:
        rows = conn.execute(
            """
            SELECT k.*,
                   (SELECT COUNT(*) FROM questions q WHERE q.item_id = k.id) AS q_count,
                   (SELECT COUNT(*) FROM review_logs l WHERE l.item_id = k.id) AS answer_count
            FROM knowledge_items k
            ORDER BY k.created_at DESC
            """
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        # 有正文/代码/附件题库/作答记录，都算「可选用」
        has_material = bool((d.get("content") or "").strip() or (d.get("code_snippet") or "").strip())
        if not has_material and int(d.get("q_count") or 0) == 0 and int(d.get("answer_count") or 0) == 0:
            # 仍允许选：只要有标题也行（可能只有附件）
            pass
        item = {
            "id": d["id"],
            "title": d["title"],
            "stage": int(d.get("stage") or 0),
            "stage_label": stage_label(int(d.get("stage") or 0)),
            "questions_status": d.get("questions_status"),
            "question_count": int(d.get("q_count") or 0),
            "answer_count": int(d.get("answer_count") or 0),
            "last_reviewed_at": d.get("last_reviewed_at"),
            "learned": bool(d.get("last_reviewed_at")) or int(d.get("answer_count") or 0) > 0,
        }
        out.append(item)
    return out


def _build_material_bundle(item_ids: list[int]) -> tuple[str, list[dict]]:
    """汇总多个知识点材料。"""
    parts: list[str] = []
    metas: list[dict] = []
    for iid in item_ids:
        item = services.get_item(iid, include_material=False)
        if not item:
            continue
        # 需要正文时再取一次带材料
        full = services.get_item(iid, include_material=True) or item
        summary = services.build_attachment_summary(iid, max_chars=2500)
        block = f"### 知识点#{full['id']} {full['title']}\n"
        block += f"正文：\n{(full.get('content') or '（无）')[:2500]}\n"
        if full.get("code_snippet"):
            block += f"\n代码：\n{full['code_snippet'][:2000]}\n"
        if summary:
            block += f"\n附件摘要：\n{summary[:2000]}\n"
        # 附带已有题干，帮助试卷避开重复
        stems = [q.get("stem") for q in (full.get("questions") or []) if q.get("stem")]
        if stems:
            block += "\n已有复习题（请出新题，勿高度重复）：\n"
            block += "\n".join(f"- {s[:160]}" for s in stems[:12])
            block += "\n"
        parts.append(block)
        metas.append({"id": full["id"], "title": full["title"]})
    return "\n\n".join(parts), metas


def _row_to_set(row) -> dict:
    d = dict(row)
    try:
        item_ids = json.loads(d.get("item_ids_json") or "[]")
    except json.JSONDecodeError:
        item_ids = []
    try:
        questions = json.loads(d.get("questions_json") or "[]")
    except json.JSONDecodeError:
        questions = []
    try:
        answers = json.loads(d.get("answers_json") or "{}")
    except json.JSONDecodeError:
        answers = {}
    if not isinstance(answers, dict):
        answers = {}
    answered = 0
    correct_n = 0
    for i, _q in enumerate(questions if isinstance(questions, list) else []):
        a = answers.get(str(i)) or answers.get(i)
        if isinstance(a, dict) and (a.get("user_answer") or "").strip():
            answered += 1
            if a.get("correct"):
                correct_n += 1
    return {
        "id": d["id"],
        "kind": d["kind"],
        "title": d["title"],
        "item_ids": item_ids if isinstance(item_ids, list) else [],
        "expand": bool(d.get("expand")),
        "question_count": int(d.get("question_count") or 0),
        "status": d.get("status") or "ready",
        "error": d.get("error"),
        "questions": questions if isinstance(questions, list) else [],
        "answers": {str(k): v for k, v in answers.items()},
        "answered_count": answered,
        "correct_count": correct_n,
        "created_at": d.get("created_at"),
    }


def list_sets(kind: str) -> list[dict]:
    with db_session() as conn:
        rows = conn.execute(
            """
            SELECT * FROM practice_sets
            WHERE kind = ?
            ORDER BY id DESC
            """,
            (kind,),
        ).fetchall()
    return [_row_to_set(r) for r in rows]


def get_set(set_id: int) -> Optional[dict]:
    with db_session() as conn:
        row = conn.execute(
            "SELECT * FROM practice_sets WHERE id = ?", (set_id,)
        ).fetchone()
    return _row_to_set(row) if row else None


def delete_set(set_id: int) -> bool:
    with db_session() as conn:
        cur = conn.execute("DELETE FROM practice_sets WHERE id = ?", (set_id,))
        return cur.rowcount > 0


async def create_exam(
    item_ids: list[int],
    *,
    expand: bool = False,
    question_count: int = 8,
    title: str = "",
) -> dict:
    return await _create_set(
        KIND_EXAM,
        item_ids,
        expand=expand,
        question_count=question_count,
        title=title,
    )


async def create_interview(
    item_ids: list[int],
    *,
    expand: bool = False,
    question_count: int = 6,
    title: str = "",
) -> dict:
    return await _create_set(
        KIND_INTERVIEW,
        item_ids,
        expand=expand,
        question_count=question_count,
        title=title,
    )


async def _create_set(
    kind: str,
    item_ids: list[int],
    *,
    expand: bool,
    question_count: int,
    title: str,
) -> dict:
    ids = []
    for x in item_ids or []:
        try:
            ids.append(int(x))
        except (TypeError, ValueError):
            continue
    ids = list(dict.fromkeys(ids))
    if not ids:
        raise ValueError("请至少选择一个知识点")

    material, metas = _build_material_bundle(ids)
    if not material.strip():
        raise ValueError("所选知识点没有可用材料")

    if not title.strip():
        names = "、".join(m["title"] for m in metas[:3])
        more = f"等{len(metas)}项" if len(metas) > 3 else ""
        prefix = "试卷" if kind == KIND_EXAM else "面试题"
        title = f"{prefix} · {names}{more}"

    count = max(4, min(int(question_count or 8), 20)) if kind == KIND_EXAM else max(
        3, min(int(question_count or 6), 15)
    )
    created = now_iso()

    with db_session() as conn:
        cur = conn.execute(
            """
            INSERT INTO practice_sets
            (kind, title, item_ids_json, expand, question_count, status, error, questions_json, created_at)
            VALUES (?, ?, ?, ?, ?, 'generating', NULL, '[]', ?)
            """,
            (
                kind,
                title.strip(),
                json.dumps(ids, ensure_ascii=False),
                1 if expand else 0,
                count,
                created,
            ),
        )
        set_id = int(cur.lastrowid)

    try:
        web_ctx = ""
        from app.config import load_settings
        from app.web_search import build_search_query, format_search_context, search_web

        settings = load_settings()
        if expand and settings.get("web_search_enabled"):
            q = " ".join(m["title"] for m in metas[:4])
            web_ctx = format_search_context(search_web(q, max_results=5))

        if kind == KIND_EXAM:
            questions = await generate_exam_paper(
                material=material,
                titles=[m["title"] for m in metas],
                count=count,
                expand=expand,
                web_search_context=web_ctx,
            )
        else:
            questions = await generate_interview_questions(
                material=material,
                titles=[m["title"] for m in metas],
                count=count,
                expand=expand,
                web_search_context=web_ctx,
            )

        with db_session() as conn:
            conn.execute(
                """
                UPDATE practice_sets
                SET status = 'ready', error = NULL, questions_json = ?, question_count = ?
                WHERE id = ?
                """,
                (json.dumps(questions, ensure_ascii=False), len(questions), set_id),
            )
    except Exception as e:
        err = f"{type(e).__name__}: {str(e).strip() or repr(e)}"
        with db_session() as conn:
            conn.execute(
                """
                UPDATE practice_sets
                SET status = 'failed', error = ?
                WHERE id = ?
                """,
                (err[:800], set_id),
            )
        if isinstance(e, (LLMError, ValueError)):
            raise
        raise LLMError(err) from e

    result = get_set(set_id)
    if not result:
        raise RuntimeError("套题保存失败")
    result["items"] = metas
    return result


async def submit_practice_answer(
    set_id: int,
    question_index: int,
    user_answer: str,
) -> dict:
    """对试卷/面试某一题作答并判分（不写入错题本，不影响艾宾浩斯）。"""
    entry = get_set(set_id)
    if not entry:
        raise ValueError("套题不存在")
    if entry.get("status") != "ready":
        raise ValueError("套题尚未生成完成，无法作答")
    questions = entry.get("questions") or []
    if question_index < 0 or question_index >= len(questions):
        raise ValueError("题目序号无效")
    answer = (user_answer or "").strip()
    if not answer:
        raise ValueError("答案不能为空")

    q = questions[question_index]
    qtype = (q.get("qtype") or "short").lower()
    options = q.get("options") or []
    if isinstance(options, str):
        try:
            options = json.loads(options)
        except json.JSONDecodeError:
            options = []

    from app.llm import grade_answer

    # 面试题按简答语义判分
    grade_qtype = "short" if qtype in ("interview", "scenario") else qtype
    result = await grade_answer(
        q.get("stem") or "",
        q.get("reference_answer") or "",
        answer,
        qtype=grade_qtype,
        options=options if isinstance(options, list) else [],
    )

    reviewed = now_iso()
    record = {
        "user_answer": answer,
        "correct": bool(result.get("correct")),
        "feedback": result.get("feedback") or "",
        "reference_answer": result.get("reference_answer")
        or q.get("reference_answer")
        or "",
        "explanation": result.get("explanation") or q.get("explanation") or "",
        "extension": result.get("extension") or q.get("extension") or "",
        "reviewed_at": reviewed,
    }

    answers = dict(entry.get("answers") or {})
    answers[str(question_index)] = record

    with db_session() as conn:
        conn.execute(
            "UPDATE practice_sets SET answers_json = ? WHERE id = ?",
            (json.dumps(answers, ensure_ascii=False), set_id),
        )

    updated = get_set(set_id) or entry
    return {
        **record,
        "question_index": question_index,
        "set_id": set_id,
        "kind": entry.get("kind"),
        "answered_count": updated.get("answered_count", 0),
        "correct_count": updated.get("correct_count", 0),
        "total_count": len(questions),
        "saved": True,
    }


def clear_practice_answers(set_id: int) -> dict:
    entry = get_set(set_id)
    if not entry:
        raise ValueError("套题不存在")
    with db_session() as conn:
        conn.execute(
            "UPDATE practice_sets SET answers_json = ? WHERE id = ?",
            ("{}", set_id),
        )
    return get_set(set_id) or entry
