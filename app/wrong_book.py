"""错题本业务逻辑（独立艾宾浩斯计划）。"""
from __future__ import annotations

import json

from app.db import db_session
from app.ebbinghaus import (
    advance_after_correct,
    initial_next_review,
    is_due,
    now_iso,
    stage_label,
)
from app.llm import grade_answer
from app.quiz_rules import QTYPE_LABELS


def _public_question_from_row(row: dict) -> dict:
    options = []
    raw = row.get("options_json") or ""
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                options = parsed
        except json.JSONDecodeError:
            options = []
    qtype = row.get("qtype") or "short"
    return {
        "id": row["question_id"] if "question_id" in row else row.get("id"),
        "item_id": row.get("item_id"),
        "stem": row.get("stem") or "",
        "qtype": qtype,
        "qtype_label": QTYPE_LABELS.get(qtype, "简答题"),
        "options": options,
        "created_at": row.get("created_at") or "",
        "generation_batch": int(row.get("generation_batch") or 1),
    }


def upsert_wrong_book(item_id: int, question_id: int) -> dict:
    """答错时加入错题本；已存在则重新激活并重置到第 1 档。"""
    created = now_iso()
    next_at = initial_next_review()
    with db_session() as conn:
        row = conn.execute(
            "SELECT id FROM wrong_book WHERE question_id = ?", (question_id,)
        ).fetchone()
        if row:
            conn.execute(
                """
                UPDATE wrong_book
                SET active = 1, stage = 0, next_review_at = ?, item_id = ?
                WHERE question_id = ?
                """,
                (next_at, item_id, question_id),
            )
            wid = int(row["id"])
        else:
            cur = conn.execute(
                """
                INSERT INTO wrong_book
                (item_id, question_id, stage, next_review_at, created_at, active)
                VALUES (?, ?, 0, ?, ?, 1)
                """,
                (item_id, question_id, next_at, created),
            )
            wid = int(cur.lastrowid)
    return get_wrong_entry(wid) or {}


def get_wrong_entry(wrong_id: int) -> dict | None:
    with db_session() as conn:
        row = conn.execute(
            """
            SELECT w.*, k.title AS item_title, q.stem, q.qtype, q.options_json, q.generation_batch
            FROM wrong_book w
            JOIN knowledge_items k ON k.id = w.item_id
            JOIN questions q ON q.id = w.question_id
            WHERE w.id = ?
            """,
            (wrong_id,),
        ).fetchone()
    if not row:
        return None
    return _enrich_wrong(dict(row))


def _enrich_wrong(row: dict) -> dict:
    qrow = {
        "question_id": row["question_id"],
        "item_id": row["item_id"],
        "stem": row.get("stem") or "",
        "qtype": row.get("qtype") or "short",
        "options_json": row.get("options_json") or "",
        "generation_batch": row.get("generation_batch") or 1,
    }
    return {
        "id": row["id"],
        "item_id": row["item_id"],
        "question_id": row["question_id"],
        "item_title": row.get("item_title") or "",
        "stage": int(row.get("stage") or 0),
        "stage_label": stage_label(int(row.get("stage") or 0)),
        "next_review_at": row["next_review_at"],
        "last_reviewed_at": row.get("last_reviewed_at"),
        "created_at": row.get("created_at"),
        "active": bool(row.get("active", 1)),
        "question": _public_question_from_row(qrow),
    }


def list_wrong_book(active_only: bool = True) -> list:
    sql = """
        SELECT w.*, k.title AS item_title, q.stem, q.qtype, q.options_json, q.generation_batch
        FROM wrong_book w
        JOIN knowledge_items k ON k.id = w.item_id
        JOIN questions q ON q.id = w.question_id
    """
    if active_only:
        sql += " WHERE w.active = 1"
    sql += " ORDER BY w.next_review_at ASC, w.id ASC"
    with db_session() as conn:
        rows = conn.execute(sql).fetchall()
    return [_enrich_wrong(dict(r)) for r in rows]


def list_due_wrong() -> list:
    now = now_iso()
    with db_session() as conn:
        rows = conn.execute(
            """
            SELECT w.*, k.title AS item_title, q.stem, q.qtype, q.options_json, q.generation_batch
            FROM wrong_book w
            JOIN knowledge_items k ON k.id = w.item_id
            JOIN questions q ON q.id = w.question_id
            WHERE w.active = 1 AND w.next_review_at <= ?
            ORDER BY w.next_review_at ASC
            """,
            (now,),
        ).fetchall()
    return [_enrich_wrong(dict(r)) for r in rows]


def due_wrong_count() -> int:
    return len(list_due_wrong())


async def submit_wrong_answer(wrong_id: int, user_answer: str) -> dict:
    entry = get_wrong_entry(wrong_id)
    if not entry or not entry.get("active"):
        raise ValueError("错题不存在或已移除")

    with db_session() as conn:
        q = conn.execute(
            "SELECT * FROM questions WHERE id = ?", (entry["question_id"],)
        ).fetchone()
        w = conn.execute("SELECT * FROM wrong_book WHERE id = ?", (wrong_id,)).fetchone()
    if not q or not w:
        raise ValueError("错题数据缺失")

    options = []
    raw = q["options_json"] if "options_json" in q.keys() else ""
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                options = parsed
        except json.JSONDecodeError:
            options = []
    qtype = q["qtype"] if "qtype" in q.keys() else "short"

    result = await grade_answer(
        q["stem"],
        q["reference_answer"],
        user_answer.strip(),
        qtype=qtype or "short",
        options=options,
    )
    correct = bool(result["correct"])
    feedback = result["feedback"]
    reference_answer = (result.get("reference_answer") or q["reference_answer"] or "").strip()
    explanation = (result.get("explanation") or "").strip()
    extension = (result.get("extension") or "").strip()
    teach_parts = []
    if explanation:
        teach_parts.append(f"解释：{explanation}")
    if extension:
        teach_parts.append(f"知识拓展：{extension}")
    if teach_parts:
        feedback = feedback + "\n" + "\n".join(teach_parts)
    reviewed = now_iso()
    due_now = is_due(w["next_review_at"])
    schedule_updated = False
    new_stage = int(w["stage"] or 0)
    next_at = w["next_review_at"]

    if due_now:
        # 与知识点一致：到期复习即进下一档（答错也推进；条目仍留在错题本）
        new_stage, next_at = advance_after_correct(new_stage)
        schedule_updated = True
        if not correct:
            feedback = f"{feedback}（答错仍进入下一档，本题保留在错题本）"
    else:
        if correct:
            feedback = f"{feedback}（该错题今日已复习过，本次仅练习，日程不变）"
        else:
            feedback = f"{feedback}（本次练习答错，日程不变）"

    with db_session() as conn:
        conn.execute(
            """
            INSERT INTO review_logs
            (item_id, question_id, user_answer, is_correct, feedback, reviewed_at, question_stem)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                entry["item_id"],
                entry["question_id"],
                user_answer.strip(),
                1 if correct else 0,
                feedback,
                reviewed,
                (q["stem"] or "").strip(),
            ),
        )
        if schedule_updated:
            conn.execute(
                """
                UPDATE wrong_book
                SET stage = ?, next_review_at = ?, last_reviewed_at = ?, active = 1
                WHERE id = ?
                """,
                (new_stage, next_at, reviewed, wrong_id),
            )
        else:
            conn.execute(
                "UPDATE wrong_book SET last_reviewed_at = ?, active = 1 WHERE id = ?",
                (reviewed, wrong_id),
            )

    return {
        "correct": correct,
        "feedback": feedback,
        "reference_answer": reference_answer,
        "explanation": explanation,
        "extension": extension,
        "next_review_at": next_at,
        "stage": new_stage,
        "stage_label": stage_label(new_stage),
        "schedule_updated": schedule_updated,
        "saved": True,
        "user_answer": user_answer.strip(),
        "wrong_id": wrong_id,
        "item_id": entry["item_id"],
        "show_material": not correct,
        "reviewed_at": reviewed,
    }
