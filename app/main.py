"""FastAPI 主应用。"""
from __future__ import annotations

from pathlib import Path
from typing import Any, List, Optional

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.config import (
    ensure_dirs,
    get_api_key,
    api_key_configured,
    effective_llm_config,
    load_settings,
    save_settings,
    set_api_key,
)
from app.db import init_db
from app.llm import LLMError
from app import services

STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(title="艾宾浩斯复习", version="1.0.0")


@app.on_event("startup")
def on_startup() -> None:
    ensure_dirs()
    init_db()


class AnswerBody(BaseModel):
    question_id: int
    user_answer: str = Field(min_length=1)
    finish_session: bool = False
    session_had_wrong: bool = False


class WrongAnswerBody(BaseModel):
    user_answer: str = Field(min_length=1)


class SettingsBody(BaseModel):
    llm_base_url: Optional[str] = None
    llm_model: Optional[str] = None
    llm_api_key: Optional[str] = None
    desktop_notify: Optional[bool] = None
    browser_notify: Optional[bool] = None
    notify_hour: Optional[int] = None
    notify_minute: Optional[int] = None
    web_search_enabled: Optional[bool] = None
    web_search_for_grade: Optional[bool] = None


async def _bg_generate(item_id: int) -> None:
    try:
        await services.generate_questions_for_item(item_id)
    except Exception:
        pass


@app.get("/api/health")
def health() -> dict:
    return {"ok": True}


@app.get("/api/settings")
def get_settings() -> dict:
    s = load_settings()
    eff = effective_llm_config()
    return {
        "llm_base_url": s["llm_base_url"],
        "llm_model": s["llm_model"],
        "api_key_configured": eff["api_key_configured"],
        "api_key_type": eff["api_key_type"],
        "api_key_suffix": eff["api_key_suffix"],
        "desktop_notify": s["desktop_notify"],
        "browser_notify": s["browser_notify"],
        "notify_hour": s["notify_hour"],
        "notify_minute": s["notify_minute"],
        "web_search_enabled": bool(s.get("web_search_enabled")),
        "web_search_for_grade": bool(s.get("web_search_for_grade")),
        "effective": eff,
    }


@app.put("/api/settings")
def put_settings(body: SettingsBody) -> dict:
    updates: dict[str, Any] = {}
    if body.llm_base_url is not None:
        updates["llm_base_url"] = body.llm_base_url.strip()
    if body.llm_model is not None:
        updates["llm_model"] = body.llm_model.strip()
    if body.desktop_notify is not None:
        updates["desktop_notify"] = body.desktop_notify
    if body.browser_notify is not None:
        updates["browser_notify"] = body.browser_notify
    if body.notify_hour is not None:
        updates["notify_hour"] = max(0, min(23, body.notify_hour))
    if body.notify_minute is not None:
        updates["notify_minute"] = max(0, min(59, body.notify_minute))
    if body.web_search_enabled is not None:
        updates["web_search_enabled"] = body.web_search_enabled
    if body.web_search_for_grade is not None:
        updates["web_search_for_grade"] = body.web_search_for_grade
    if body.llm_api_key is not None and body.llm_api_key.strip():
        set_api_key(body.llm_api_key.strip())
    if updates:
        save_settings(updates)
    return get_settings()


@app.get("/api/items")
def api_list_items() -> dict:
    return {"items": services.list_items()}


@app.get("/api/items/due")
def api_due_items() -> dict:
    payload = services.collect_due_notify()
    return {
        "items": payload["items"],
        "count": len(payload["items"]),
        "wrong_items": payload["wrong"],
        "wrong_count": len(payload["wrong"]),
        "total_count": payload["count"],
        "titles": payload["titles"][:5],
        "fingerprint": payload["fingerprint"],
    }


@app.post("/api/items")
async def api_create_item(
    background_tasks: BackgroundTasks,
    title: str = Form(...),
    content: str = Form(""),
    code_snippet: str = Form(""),
    files: Optional[List[UploadFile]] = File(None),
    relative_paths: Optional[List[str]] = Form(None),
) -> dict:
    if not title.strip():
        raise HTTPException(400, "标题不能为空")

    file_payload: list[tuple[str, bytes, str]] = []
    if files:
        paths = relative_paths or []
        for i, f in enumerate(files):
            data = await f.read()
            name = f.filename or f"file_{i}"
            rel = paths[i] if i < len(paths) else name
            file_payload.append((name, data, rel))

    item = services.create_item(
        title=title,
        content=content,
        code_snippet=code_snippet,
        files=file_payload or None,
    )
    background_tasks.add_task(_bg_generate, item["id"])
    return {"item": item}


@app.get("/api/items/{item_id}")
def api_get_item(item_id: int, for_study: bool = False) -> dict:
    item = services.get_item(item_id, include_material=True)
    if not item:
        raise HTTPException(404, "知识点不存在")
    # 复习前默认也可查看材料（错题学习流程用 for_study）；列表详情始终可看
    return {"item": item}


@app.delete("/api/items/{item_id}")
def api_delete_item(item_id: int) -> dict:
    if not services.delete_item(item_id):
        raise HTTPException(404, "知识点不存在")
    return {"ok": True}


@app.post("/api/items/{item_id}/generate-questions")
async def api_generate(item_id: int) -> dict:
    try:
        item = await services.generate_questions_for_item(item_id)
        meta = item.pop("generate_meta", None) if isinstance(item, dict) else None
        return {"item": item, "generate_meta": meta}
    except ValueError as e:
        # 知识点不存在 vs 未能追加新题
        msg = str(e)
        if "不存在" in msg:
            raise HTTPException(404, msg) from e
        raise HTTPException(400, msg) from e
    except LLMError as e:
        print(f"[generate-questions] LLMError item={item_id}: {e}")
        raise HTTPException(502, str(e)) from e
    except Exception as e:
        import sqlite3

        print(f"[generate-questions] Error item={item_id}: {type(e).__name__}: {e!r}")
        if isinstance(e, sqlite3.IntegrityError) or "FOREIGN KEY" in str(e):
            raise HTTPException(
                500,
                "出题失败：数据库外键冲突。请关掉所有旧的 python run.py 窗口后重新启动，"
                "再点「再出一批题」（当前版本只追加题目，不会删除旧题）。",
            ) from e
        raise HTTPException(500, f"{type(e).__name__}: {e}") from e


@app.get("/api/items/{item_id}/quiz")
def api_quiz(item_id: int) -> dict:
    try:
        return services.get_quiz(item_id)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e


@app.post("/api/items/{item_id}/answer")
async def api_answer(item_id: int, body: AnswerBody) -> dict:
    try:
        return await services.submit_answer(
            item_id,
            body.question_id,
            body.user_answer,
            finish_session=body.finish_session,
            session_had_wrong=body.session_had_wrong,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except LLMError as e:
        raise HTTPException(502, str(e)) from e


@app.get("/api/wrong")
def api_wrong_list() -> dict:
    from app import wrong_book

    return {"items": wrong_book.list_wrong_book(active_only=True)}


@app.get("/api/wrong/due")
def api_wrong_due() -> dict:
    from app import wrong_book

    items = wrong_book.list_due_wrong()
    return {"items": items, "count": len(items)}


@app.post("/api/wrong/{wrong_id}/answer")
async def api_wrong_answer(wrong_id: int, body: WrongAnswerBody) -> dict:
    from app import wrong_book

    try:
        return await wrong_book.submit_wrong_answer(wrong_id, body.user_answer)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except LLMError as e:
        raise HTTPException(502, str(e)) from e


@app.get("/api/items/{item_id}/answers")
def api_answers(item_id: int) -> dict:
    item = services.get_item(item_id, include_material=False)
    if not item:
        raise HTTPException(404, "知识点不存在")
    return {"item_id": item_id, "logs": services.list_answer_logs(item_id, limit=50)}


@app.get("/api/attachments/{attachment_id}")
def api_attachment(attachment_id: int) -> FileResponse:
    from app.db import db_session

    with db_session() as conn:
        row = conn.execute(
            "SELECT * FROM attachments WHERE id = ?", (attachment_id,)
        ).fetchone()
    if not row:
        raise HTTPException(404, "附件不存在")
    path = services.attachment_abs_path(row["path"])
    if not path.exists():
        raise HTTPException(404, "文件缺失")
    return FileResponse(path, filename=row["original_name"])


@app.get("/api/notify/due-summary")
def api_notify_summary() -> dict:
    payload = services.collect_due_notify()
    return {
        "count": payload["count"],
        "titles": payload["titles"],
        "fingerprint": payload["fingerprint"],
    }


@app.post("/api/notify/test")
def api_notify_test() -> dict:
    """立即弹一次桌面提醒（用于验证通路）。"""
    from app.notifier import check_and_notify_once

    return check_and_notify_once(force=True)


class PracticeCreateBody(BaseModel):
    item_ids: List[int] = Field(min_length=1)
    expand: bool = False
    question_count: int = 8
    title: str = ""


class PracticeAnswerBody(BaseModel):
    question_index: int = Field(ge=0)
    user_answer: str = Field(min_length=1)


@app.get("/api/learned-items")
def api_learned_items() -> dict:
    from app import practice_sets

    return {"items": practice_sets.list_learned_items()}


@app.get("/api/exams")
def api_list_exams() -> dict:
    from app import practice_sets

    return {"items": practice_sets.list_sets(practice_sets.KIND_EXAM)}


@app.post("/api/exams")
async def api_create_exam(body: PracticeCreateBody) -> dict:
    from app import practice_sets

    try:
        item = await practice_sets.create_exam(
            body.item_ids,
            expand=body.expand,
            question_count=body.question_count or 8,
            title=body.title or "",
        )
        return {"item": item}
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except LLMError as e:
        raise HTTPException(502, str(e)) from e


@app.get("/api/exams/{exam_id}")
def api_get_exam(exam_id: int) -> dict:
    from app import practice_sets

    item = practice_sets.get_set(exam_id)
    if not item or item.get("kind") != practice_sets.KIND_EXAM:
        raise HTTPException(404, "试卷不存在")
    return {"item": item}


@app.delete("/api/exams/{exam_id}")
def api_delete_exam(exam_id: int) -> dict:
    from app import practice_sets

    item = practice_sets.get_set(exam_id)
    if not item or item.get("kind") != practice_sets.KIND_EXAM:
        raise HTTPException(404, "试卷不存在")
    practice_sets.delete_set(exam_id)
    return {"ok": True}


@app.post("/api/exams/{exam_id}/answer")
async def api_exam_answer(exam_id: int, body: PracticeAnswerBody) -> dict:
    from app import practice_sets

    item = practice_sets.get_set(exam_id)
    if not item or item.get("kind") != practice_sets.KIND_EXAM:
        raise HTTPException(404, "试卷不存在")
    try:
        return await practice_sets.submit_practice_answer(
            exam_id, body.question_index, body.user_answer
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except LLMError as e:
        raise HTTPException(502, str(e)) from e


@app.post("/api/exams/{exam_id}/reset-answers")
def api_exam_reset(exam_id: int) -> dict:
    from app import practice_sets

    item = practice_sets.get_set(exam_id)
    if not item or item.get("kind") != practice_sets.KIND_EXAM:
        raise HTTPException(404, "试卷不存在")
    try:
        return {"item": practice_sets.clear_practice_answers(exam_id)}
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.get("/api/interviews")
def api_list_interviews() -> dict:
    from app import practice_sets

    return {"items": practice_sets.list_sets(practice_sets.KIND_INTERVIEW)}


@app.post("/api/interviews")
async def api_create_interview(body: PracticeCreateBody) -> dict:
    from app import practice_sets

    try:
        item = await practice_sets.create_interview(
            body.item_ids,
            expand=body.expand,
            question_count=body.question_count or 6,
            title=body.title or "",
        )
        return {"item": item}
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except LLMError as e:
        raise HTTPException(502, str(e)) from e


@app.get("/api/interviews/{interview_id}")
def api_get_interview(interview_id: int) -> dict:
    from app import practice_sets

    item = practice_sets.get_set(interview_id)
    if not item or item.get("kind") != practice_sets.KIND_INTERVIEW:
        raise HTTPException(404, "面试套题不存在")
    return {"item": item}


@app.delete("/api/interviews/{interview_id}")
def api_delete_interview(interview_id: int) -> dict:
    from app import practice_sets

    item = practice_sets.get_set(interview_id)
    if not item or item.get("kind") != practice_sets.KIND_INTERVIEW:
        raise HTTPException(404, "面试套题不存在")
    practice_sets.delete_set(interview_id)
    return {"ok": True}


@app.post("/api/interviews/{interview_id}/answer")
async def api_interview_answer(interview_id: int, body: PracticeAnswerBody) -> dict:
    from app import practice_sets

    item = practice_sets.get_set(interview_id)
    if not item or item.get("kind") != practice_sets.KIND_INTERVIEW:
        raise HTTPException(404, "面试套题不存在")
    try:
        return await practice_sets.submit_practice_answer(
            interview_id, body.question_index, body.user_answer
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except LLMError as e:
        raise HTTPException(502, str(e)) from e


@app.post("/api/interviews/{interview_id}/reset-answers")
def api_interview_reset(interview_id: int) -> dict:
    from app import practice_sets

    item = practice_sets.get_set(interview_id)
    if not item or item.get("kind") != practice_sets.KIND_INTERVIEW:
        raise HTTPException(404, "面试套题不存在")
    try:
        return {"item": practice_sets.clear_practice_answers(interview_id)}
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


# 静态资源与 SPA
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/sw.js")
def service_worker() -> FileResponse:
    return FileResponse(
        STATIC_DIR / "sw.js",
        media_type="application/javascript",
        headers={"Service-Worker-Allowed": "/"},
    )
