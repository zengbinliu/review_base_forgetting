"""知识点与复习业务逻辑。"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.config import UPLOADS_DIR
from app.db import db_session
from app.ebbinghaus import (
    advance_after_correct,
    initial_next_review,
    is_due,
    now_iso,
    stage_label,
)
from app.llm import LLMError, generate_questions, grade_answer

TEXT_EXTS = {
    ".txt",
    ".md",
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".java",
    ".c",
    ".cpp",
    ".h",
    ".cs",
    ".go",
    ".rs",
    ".html",
    ".css",
    ".json",
    ".yml",
    ".yaml",
    ".toml",
    ".sql",
    ".sh",
    ".bat",
    ".ps1",
}


def _item_upload_dir(item_id: int) -> Path:
    d = UPLOADS_DIR / str(item_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def create_item(
    title: str,
    content: str = "",
    code_snippet: str = "",
    files: list[tuple[str, bytes, str]] | None = None,
) -> dict:
    """
    files: list of (original_name, data, relative_path)
    relative_path 用于文件夹树，单文件可为空。
    """
    created = now_iso()
    next_review = initial_next_review()
    with db_session() as conn:
        cur = conn.execute(
            """
            INSERT INTO knowledge_items
            (title, content, code_snippet, created_at, stage, next_review_at, questions_status)
            VALUES (?, ?, ?, ?, 0, ?, 'pending')
            """,
            (title.strip(), content or "", code_snippet or "", created, next_review),
        )
        item_id = int(cur.lastrowid)

    if files:
        upload_dir = _item_upload_dir(item_id)
        with db_session() as conn:
            for original_name, data, relative_path in files:
                rel = (relative_path or original_name).replace("\\", "/").lstrip("/")
                safe_parts = [p for p in rel.split("/") if p and p not in (".", "..")]
                if not safe_parts:
                    continue
                dest = upload_dir.joinpath(*safe_parts)
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(data)
                stored = str(dest.relative_to(UPLOADS_DIR)).replace("\\", "/")
                kind = _guess_kind(original_name)
                conn.execute(
                    """
                    INSERT INTO attachments (item_id, kind, path, original_name, relative_path)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (item_id, kind, stored, original_name, "/".join(safe_parts)),
                )

    return get_item(item_id)


def _guess_kind(name: str) -> str:
    ext = Path(name).suffix.lower()
    if ext in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg"}:
        return "image"
    if ext in TEXT_EXTS:
        return "text"
    return "file"


def list_items() -> list[dict]:
    with db_session() as conn:
        rows = conn.execute(
            "SELECT * FROM knowledge_items ORDER BY created_at DESC"
        ).fetchall()
    return [_enrich_item(dict(r)) for r in rows]


def list_due_items() -> list[dict]:
    now = now_iso()
    with db_session() as conn:
        rows = conn.execute(
            """
            SELECT * FROM knowledge_items
            WHERE next_review_at <= ?
            ORDER BY next_review_at ASC
            """,
            (now,),
        ).fetchall()
    return [_enrich_item(dict(r)) for r in rows]


def get_item(item_id: int, include_material: bool = True) -> dict | None:
    if include_material:
        _try_relink_orphan_logs(item_id)
    with db_session() as conn:
        row = conn.execute(
            "SELECT * FROM knowledge_items WHERE id = ?", (item_id,)
        ).fetchone()
        if not row:
            return None
        item = dict(row)
        atts = conn.execute(
            "SELECT * FROM attachments WHERE item_id = ? ORDER BY id", (item_id,)
        ).fetchall()
        qs = conn.execute(
            """
            SELECT id, item_id, stem, reference_answer, qtype, options_json, created_at, generation_batch
            FROM questions WHERE item_id = ? ORDER BY generation_batch ASC, id ASC
            """,
            (item_id,),
        ).fetchall()
    item = _enrich_item(item)
    logs = list_answer_logs(item_id, limit=80) if include_material else []
    if include_material:
        questions = [_question_with_answer(dict(q), logs) for q in qs]
        linked_ids = set()
        for qdata in questions:
            for log in qdata.get("answer_history") or []:
                if log.get("id") is not None:
                    linked_ids.add(int(log["id"]))
            la = qdata.get("latest_answer")
            if la and la.get("id") is not None:
                linked_ids.add(int(la["id"]))
        orphans = [log for log in logs if int(log["id"]) not in linked_ids]
        item["attachments"] = [dict(a) for a in atts]
        item["questions"] = questions
        item["answer_logs"] = logs
        item["orphan_answers"] = orphans[:20]
    else:
        item["attachments"] = []
        item["questions"] = [_question_public(dict(q)) for q in qs]
        item["answer_logs"] = []
        item["orphan_answers"] = []
    return item


def list_answer_logs(item_id: int, limit: int = 20) -> list:
    with db_session() as conn:
        rows = conn.execute(
            """
            SELECT l.id, l.item_id, l.question_id, l.user_answer, l.is_correct,
                   l.feedback, l.reviewed_at, l.question_stem,
                   COALESCE(q.stem, l.question_stem) AS stem, q.qtype
            FROM review_logs l
            LEFT JOIN questions q ON q.id = l.question_id
            WHERE l.item_id = ?
            ORDER BY l.reviewed_at DESC, l.id DESC
            LIMIT ?
            """,
            (item_id, limit),
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["is_correct"] = bool(d.get("is_correct"))
        out.append(d)
    return out


def _stem_match_score(stem: str, blob: str) -> int:
    """题干与作答/反馈文本的最长公共片段长度（去空白标点），用于孤儿记录回挂。"""
    import re

    stem = (stem or "").strip()
    blob = blob or ""
    if not stem or not blob:
        return 0
    stem_c = re.sub(r"[\s\W_]+", "", stem, flags=re.UNICODE)
    blob_c = re.sub(r"[\s\W_]+", "", blob, flags=re.UNICODE)
    if not stem_c or not blob_c:
        return 0
    best = 0
    # 从长到短找命中片段
    for n in range(min(20, len(stem_c)), 5, -1):
        for i in range(0, len(stem_c) - n + 1):
            if stem_c[i : i + n] in blob_c:
                return n
    return best


def _try_relink_orphan_logs(item_id: int) -> None:
    """尽量把 question_id 被清空的历史作答重新挂回当前题目。"""
    with db_session() as conn:
        orphans = conn.execute(
            """
            SELECT id, user_answer, feedback, question_stem
            FROM review_logs
            WHERE item_id = ? AND question_id IS NULL
            ORDER BY reviewed_at DESC, id DESC
            """,
            (item_id,),
        ).fetchall()
        if not orphans:
            return
        questions = [
            dict(r)
            for r in conn.execute(
                "SELECT id, stem FROM questions WHERE item_id = ?", (item_id,)
            ).fetchall()
        ]
        if not questions:
            return
        used_qids: set[int] = set()
        # 从新到旧：每道题只回挂最近一次能唯一匹配的作答
        for log in orphans:
            snap = (log["question_stem"] or "").strip()
            match = None
            if snap:
                hits = [q for q in questions if (q["stem"] or "").strip() == snap]
                if len(hits) == 1:
                    match = hits[0]
            if match is None:
                blob = f"{log['user_answer'] or ''}\n{log['feedback'] or ''}\n{snap}"
                scored = []
                for q in questions:
                    if int(q["id"]) in used_qids:
                        continue
                    score = _stem_match_score(q["stem"], blob)
                    if score >= 6:
                        scored.append((score, q))
                if scored:
                    scored.sort(key=lambda x: (-x[0], int(x[1]["id"])))
                    # 最高分需明显优于第二名，避免误挂
                    if len(scored) == 1 or scored[0][0] >= scored[1][0] + 2:
                        match = scored[0][1]
            if not match:
                continue
            qid = int(match["id"])
            used_qids.add(qid)
            conn.execute(
                """
                UPDATE review_logs
                SET question_id = ?, question_stem = ?
                WHERE id = ? AND question_id IS NULL
                """,
                (qid, match["stem"], log["id"]),
            )


def _enrich_item(item: dict) -> dict:
    item["stage_label"] = stage_label(int(item.get("stage") or 0))
    item["attachment_count"] = _count_attachments(int(item["id"]))
    item["question_count"] = _count_questions(int(item["id"]))
    return item


def _count_questions(item_id: int) -> int:
    with db_session() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM questions WHERE item_id = ?", (item_id,)
        ).fetchone()
    return int(row["c"]) if row else 0


def _question_public(q: dict) -> dict:
    """对外返回题目（不含参考答案，用于答题页）。"""
    import json

    from app.quiz_rules import QTYPE_LABELS

    options = []
    raw = q.get("options_json") or ""
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                options = parsed
        except json.JSONDecodeError:
            options = []
    qtype = q.get("qtype") or "short"
    return {
        "id": q["id"],
        "item_id": q.get("item_id"),
        "stem": q["stem"],
        "qtype": qtype,
        "qtype_label": QTYPE_LABELS.get(qtype, "简答题"),
        "options": options,
        "created_at": q.get("created_at"),
        "generation_batch": int(q.get("generation_batch") or 1),
    }


def _log_matches_question(log: dict, q: dict) -> bool:
    qid = log.get("question_id")
    if qid is not None and int(qid) == int(q["id"]):
        return True
    stem = (q.get("stem") or "").strip()
    snap = (log.get("question_stem") or "").strip()
    if stem and snap and snap == stem:
        return True
    return False


def _question_with_answer(q: dict, logs: list | None = None) -> dict:
    """详情/学习页：含参考答案，并附上该题最近作答。"""
    data = _question_public(q)
    data["reference_answer"] = q.get("reference_answer") or ""
    latest = None
    history = []
    for log in logs or []:
        if _log_matches_question(log, q):
            history.append(log)
            if latest is None:
                latest = log
    data["latest_answer"] = latest
    data["answer_history"] = history[:5]
    return data


def _count_attachments(item_id: int) -> int:
    with db_session() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM attachments WHERE item_id = ?", (item_id,)
        ).fetchone()
    return int(row["c"]) if row else 0


def delete_item(item_id: int) -> bool:
    with db_session() as conn:
        row = conn.execute(
            "SELECT id FROM knowledge_items WHERE id = ?", (item_id,)
        ).fetchone()
        if not row:
            return False
        conn.execute("DELETE FROM knowledge_items WHERE id = ?", (item_id,))
    upload_dir = UPLOADS_DIR / str(item_id)
    if upload_dir.exists():
        shutil.rmtree(upload_dir, ignore_errors=True)
    return True


def build_attachment_summary(item_id: int, max_chars: int = 8000) -> str:
    parts: list[str] = []
    with db_session() as conn:
        atts = conn.execute(
            "SELECT * FROM attachments WHERE item_id = ?", (item_id,)
        ).fetchall()
    used = 0
    for a in atts:
        path = UPLOADS_DIR / a["path"]
        header = f"[{a['kind']}] {a['original_name']}"
        if a["kind"] == "image":
            parts.append(f"{header}（图片附件，请结合标题与正文出题）")
            continue
        if a["kind"] in ("text", "file") and path.suffix.lower() in TEXT_EXTS:
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            chunk = text[: max(0, max_chars - used)]
            parts.append(f"{header}:\n{chunk}")
            used += len(chunk)
            if used >= max_chars:
                break
        else:
            parts.append(f"{header}（二进制/非文本文件）")
    return "\n\n".join(parts)


async def generate_questions_for_item(item_id: int) -> dict:
    item = get_item(item_id)
    if not item:
        raise ValueError("知识点不存在")

    with db_session() as conn:
        before_count = int(
            conn.execute(
                "SELECT COUNT(*) AS c FROM questions WHERE item_id = ?", (item_id,)
            ).fetchone()["c"]
        )
        conn.execute(
            "UPDATE knowledge_items SET questions_status = ?, questions_error = NULL WHERE id = ?",
            ("generating", item_id),
        )

    try:
        summary = build_attachment_summary(item_id)
        web_ctx = ""
        from app.config import load_settings
        from app.web_search import build_search_query, format_search_context, search_web

        settings = load_settings()
        if settings.get("web_search_enabled"):
            query = build_search_query(item["title"], item.get("content") or "")
            web_ctx = format_search_context(search_web(query, max_results=5))

        with db_session() as conn:
            row = conn.execute(
                "SELECT COALESCE(MAX(generation_batch), 0) AS m FROM questions WHERE item_id = ?",
                (item_id,),
            ).fetchone()
            next_batch = int(row["m"] or 0) + 1
            old_stems = [
                (r["stem"] or "").strip()
                for r in conn.execute(
                    "SELECT stem FROM questions WHERE item_id = ? ORDER BY id",
                    (item_id,),
                ).fetchall()
            ]

        questions = await generate_questions(
            title=item["title"],
            content=item["content"],
            code_snippet=item.get("code_snippet") or "",
            attachment_summary=summary,
            web_search_context=web_ctx,
            existing_stems=old_stems,
        )
        created = now_iso()
        added = 0
        skipped_dup = 0
        existing_set = {s for s in old_stems if s}
        with db_session() as conn:
            # 追加新一批题目，绝不删除旧题
            for q in questions:
                stem = (q.get("stem") or "").strip()
                if not stem:
                    continue
                if stem in existing_set:
                    skipped_dup += 1
                    continue
                conn.execute(
                    """
                    INSERT INTO questions
                    (item_id, stem, reference_answer, created_at, qtype, options_json, generation_batch)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        item_id,
                        stem,
                        q["reference_answer"],
                        created,
                        q.get("qtype") or "short",
                        q.get("options_json") or "",
                        next_batch,
                    ),
                )
                existing_set.add(stem)
                added += 1
            after_count = int(
                conn.execute(
                    "SELECT COUNT(*) AS c FROM questions WHERE item_id = ?", (item_id,)
                ).fetchone()["c"]
            )
            if after_count < before_count:
                raise RuntimeError(
                    f"出题异常：题目数量减少（{before_count} → {after_count}），已中止"
                )
            if added == 0:
                conn.execute(
                    "UPDATE knowledge_items SET questions_status = ?, questions_error = ? WHERE id = ?",
                    (
                        "ready" if after_count else "failed",
                        "新一批题目与已有题重复或为空，未写入；请稍后重试「再出一批题」",
                    ),
                )
            else:
                conn.execute(
                    "UPDATE knowledge_items SET questions_status = ?, questions_error = NULL WHERE id = ?",
                    ("ready", item_id),
                )
            print(
                f"[generate-questions] item={item_id} batch={next_batch} "
                f"before={before_count} added={added} skipped_dup={skipped_dup} after={after_count}"
            )

        result = get_item(item_id) or {}
        result["generate_meta"] = {
            "mode": "append",
            "generation_batch": next_batch if added else None,
            "before_count": before_count,
            "added_count": added,
            "skipped_duplicate": skipped_dup,
            "total_count": after_count,
        }
        if added == 0:
            raise ValueError(
                "未能追加新题（可能与已有题目重复）。请换个时间再试「再出一批题」。"
            )
        return result
    except Exception as e:
        err = f"{type(e).__name__}: {str(e).strip() or repr(e)}"
        with db_session() as conn:
            # 失败时不删题，只记录错误；若已是 ready 且有题则保持 ready
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM questions WHERE item_id = ?", (item_id,)
            ).fetchone()
            has_q = int(row["c"] or 0) > 0
            if isinstance(e, ValueError) and has_q:
                conn.execute(
                    "UPDATE knowledge_items SET questions_status = ?, questions_error = ? WHERE id = ?",
                    ("ready", str(e)[:500], item_id),
                )
            else:
                conn.execute(
                    "UPDATE knowledge_items SET questions_status = ?, questions_error = ? WHERE id = ?",
                    ("failed", err[:500], item_id),
                )
        raise


def get_quiz(item_id: int) -> dict:
    """
    复习取题（不含参考答案）：
    - 尚有从未作答的题目 → 只出这些（完成第一遍）
    - 全部都作答过 → 只出该知识点错题本中的题
    - 都做过且无错题 → 空列表
    """
    with db_session() as conn:
        item = conn.execute(
            "SELECT id, title, stage, next_review_at, questions_status, questions_error FROM knowledge_items WHERE id = ?",
            (item_id,),
        ).fetchone()
        if not item:
            raise ValueError("知识点不存在")
        qs = conn.execute(
            "SELECT id, item_id, stem, qtype, options_json, created_at, generation_batch FROM questions WHERE item_id = ? ORDER BY id",
            (item_id,),
        ).fetchall()
        answered_ids = {
            int(r["question_id"])
            for r in conn.execute(
                """
                SELECT DISTINCT question_id FROM review_logs
                WHERE item_id = ? AND question_id IS NOT NULL
                """,
                (item_id,),
            ).fetchall()
            if r["question_id"] is not None
        }
        # 题干快照匹配：删题重建后 id 变了，但仍算已作答
        for r in conn.execute(
            """
            SELECT question_stem FROM review_logs
            WHERE item_id = ? AND question_id IS NULL
              AND question_stem IS NOT NULL AND question_stem != ''
            """,
            (item_id,),
        ).fetchall():
            snap = (r["question_stem"] or "").strip()
            if not snap:
                continue
            for q in qs:
                if (q["stem"] or "").strip() == snap:
                    answered_ids.add(int(q["id"]))
        wrong_ids = {
            int(r["question_id"])
            for r in conn.execute(
                """
                SELECT question_id FROM wrong_book
                WHERE item_id = ? AND active = 1
                """,
                (item_id,),
            ).fetchall()
        }

    all_qs = [dict(q) for q in qs]
    unanswered = [q for q in all_qs if int(q["id"]) not in answered_ids]
    mode = "all_new"
    selected = unanswered

    if unanswered:
        mode = "first_pass"
        selected = unanswered
    elif all_qs:
        # 已做过一遍：仅做错题
        mode = "wrong_only"
        selected = [q for q in all_qs if int(q["id"]) in wrong_ids]
    else:
        mode = "empty"
        selected = []

    return {
        "item": _enrich_item(dict(item)),
        "questions": [_question_public(q) for q in selected],
        "quiz_mode": mode,
        "total_question_count": len(all_qs),
        "unanswered_count": len(unanswered),
        "wrong_count": len(wrong_ids),
    }


async def submit_answer(
    item_id: int,
    question_id: int,
    user_answer: str,
    finish_session: bool = False,
    session_had_wrong: bool = False,
) -> dict:
    import json

    from app.wrong_book import upsert_wrong_book

    with db_session() as conn:
        item = conn.execute(
            "SELECT * FROM knowledge_items WHERE id = ?", (item_id,)
        ).fetchone()
        q = conn.execute(
            "SELECT * FROM questions WHERE id = ? AND item_id = ?",
            (question_id, item_id),
        ).fetchone()
    if not item:
        raise ValueError("知识点不存在")
    if not q:
        raise ValueError("题目不存在")
    if not (user_answer or "").strip():
        raise ValueError("答案不能为空")

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

    web_ctx = ""
    from app.config import load_settings
    from app.web_search import format_search_context, search_web

    settings = load_settings()
    if settings.get("web_search_for_grade") and (qtype or "short") in ("short", "scenario"):
        web_ctx = format_search_context(
            search_web(f"{q['stem'][:120]}", max_results=3),
            max_chars=2000,
        )

    try:
        result = await grade_answer(
            q["stem"],
            q["reference_answer"],
            user_answer.strip(),
            qtype=qtype or "short",
            options=options,
            web_search_context=web_ctx,
        )
    except LLMError:
        raise

    correct = bool(result["correct"])
    feedback = result["feedback"]
    reference_answer = (result.get("reference_answer") or q["reference_answer"] or "").strip()
    explanation = (result.get("explanation") or "").strip()
    extension = (result.get("extension") or "").strip()
    # 写入日志时附带解释与拓展，便于详情页回顾
    teach_parts = []
    if explanation:
        teach_parts.append(f"解释：{explanation}")
    if extension:
        teach_parts.append(f"知识拓展：{extension}")
    if teach_parts:
        feedback = feedback + "\n" + "\n".join(teach_parts)
    reviewed = now_iso()
    due_now = is_due(item["next_review_at"])
    schedule_updated = False
    new_stage = int(item["stage"] or 0)
    next_at = item["next_review_at"]
    wrong_entry = None

    if not correct:
        wrong_entry = upsert_wrong_book(item_id, question_id)
        feedback = f"{feedback}（已加入错题本）"

    # 知识点档位：整轮答完且到期即进下一档（有错题仍推进；错题进错题本单独复习）
    if finish_session and due_now:
        new_stage, next_at = advance_after_correct(new_stage)
        schedule_updated = True
        if session_had_wrong or (not correct):
            feedback = f"{feedback}（本轮有错题已入错题本，知识点仍进入下一档）"
    elif finish_session and not due_now:
        feedback = f"{feedback}（本轮为练习，知识点日程不变）"

    question_stem = (q["stem"] or "").strip()
    with db_session() as conn:
        cur = conn.execute(
            """
            INSERT INTO review_logs
            (item_id, question_id, user_answer, is_correct, feedback, reviewed_at, question_stem)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item_id,
                question_id,
                user_answer.strip(),
                1 if correct else 0,
                feedback,
                reviewed,
                question_stem,
            ),
        )
        log_id = int(cur.lastrowid)
        saved = conn.execute(
            "SELECT question_id FROM review_logs WHERE id = ?", (log_id,)
        ).fetchone()
        if not saved or saved["question_id"] is None:
            raise RuntimeError("作答保存失败：题目关联丢失，请重启服务后重试")
        if schedule_updated:
            conn.execute(
                """
                UPDATE knowledge_items
                SET stage = ?, next_review_at = ?, last_reviewed_at = ?
                WHERE id = ?
                """,
                (new_stage, next_at, reviewed, item_id),
            )
        else:
            conn.execute(
                """
                UPDATE knowledge_items
                SET last_reviewed_at = ?
                WHERE id = ?
                """,
                (reviewed, item_id),
            )

    return {
        "correct": correct,
        "feedback": feedback,
        "reference_answer": reference_answer if not correct else reference_answer,
        "explanation": explanation,
        "extension": extension,
        "next_review_at": next_at,
        "stage": new_stage,
        "stage_label": stage_label(new_stage),
        "show_material": not correct,
        "item_id": item_id,
        "schedule_updated": schedule_updated,
        "saved": True,
        "user_answer": user_answer.strip(),
        "question_id": question_id,
        "reviewed_at": reviewed,
        "log_id": log_id,
        "added_to_wrong_book": wrong_entry is not None,
        "wrong_id": (wrong_entry or {}).get("id"),
        "session_finished": finish_session,
    }


def due_count() -> int:
    return len(list_due_items())


def get_notify_desktop_state() -> tuple[str | None, str | None]:
    with db_session() as conn:
        row = conn.execute(
            "SELECT last_desktop_date, last_desktop_fp FROM notify_state WHERE id = 1"
        ).fetchone()
    if not row:
        return None, None
    return row["last_desktop_date"], row["last_desktop_fp"] if "last_desktop_fp" in row.keys() else None


def set_notify_desktop_state(d: str, fingerprint: str = "") -> None:
    with db_session() as conn:
        conn.execute(
            """
            UPDATE notify_state
            SET last_desktop_date = ?, last_desktop_fp = ?
            WHERE id = 1
            """,
            (d, fingerprint),
        )


def set_notify_desktop_date(d: str) -> None:
    """兼容旧调用。"""
    set_notify_desktop_state(d, "")


def collect_due_notify() -> dict:
    """汇总知识点 + 错题本到期，供桌面/测试提醒使用。"""
    from app import wrong_book

    items = list_due_items()
    wrong = wrong_book.list_due_wrong()
    titles = [i["title"] for i in items[:5]]
    titles += [f"[错题]{w.get('item_title') or ''}" for w in wrong[:3]]
    fp = (
        "k:"
        + ",".join(str(i["id"]) for i in items)
        + "|w:"
        + ",".join(str(w["id"]) for w in wrong)
    )
    return {
        "items": items,
        "wrong": wrong,
        "titles": titles,
        "count": len(items) + len(wrong),
        "fingerprint": fp,
    }


def attachment_abs_path(stored_path: str) -> Path:
    return UPLOADS_DIR / stored_path
