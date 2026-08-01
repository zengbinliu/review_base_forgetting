"""艾宾浩斯遗忘曲线日程计算。"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from app.config import EBBINGHAUS_INTERVALS_DAYS


def now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat(sep=" ")


def today_str() -> str:
    return date.today().isoformat()


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    value = value.strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def initial_next_review() -> str:
    """新建知识点：次日进入第一次复习（阶段 0 → 间隔 1 天）。"""
    d = date.today() + timedelta(days=EBBINGHAUS_INTERVALS_DAYS[0])
    return datetime.combine(d, datetime.min.time().replace(hour=9)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )


def advance_after_correct(stage: int) -> tuple[int, str]:
    """答对：进入下一档；已到最后一档则仍按 30 天循环。"""
    next_stage = min(stage + 1, len(EBBINGHAUS_INTERVALS_DAYS) - 1)
    days = EBBINGHAUS_INTERVALS_DAYS[next_stage]
    nxt = date.today() + timedelta(days=days)
    return next_stage, datetime.combine(
        nxt, datetime.min.time().replace(hour=9)
    ).strftime("%Y-%m-%d %H:%M:%S")


def reset_after_wrong() -> tuple[int, str]:
    """答错：回到第 0 档，次日再复习。"""
    days = EBBINGHAUS_INTERVALS_DAYS[0]
    nxt = date.today() + timedelta(days=days)
    return 0, datetime.combine(nxt, datetime.min.time().replace(hour=9)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )


def is_due(next_review_at: str, now: datetime | None = None) -> bool:
    now = now or datetime.now()
    dt = parse_dt(next_review_at)
    if dt is None:
        return True
    return dt <= now


def stage_label(stage: int) -> str:
    if stage < 0:
        stage = 0
    if stage >= len(EBBINGHAUS_INTERVALS_DAYS):
        stage = len(EBBINGHAUS_INTERVALS_DAYS) - 1
    return f"第{stage + 1}档（间隔 {EBBINGHAUS_INTERVALS_DAYS[stage]} 天）"
