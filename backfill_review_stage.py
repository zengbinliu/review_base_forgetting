"""
按「已复习次数」补算艾宾浩斯档位（知识点 + 可选错题本）。
可在任意已部署本项目的电脑上运行。

推荐:
  .\\.venv\\Scripts\\python.exe backfill_review_stage.py --auto --all --include-wrong --from-zero --dry-run
  .\\.venv\\Scripts\\python.exe backfill_review_stage.py --auto --all --include-wrong --from-zero

仅知识点 / 仅错题本:
  .\\.venv\\Scripts\\python.exe backfill_review_stage.py --auto --all --from-zero
  .\\.venv\\Scripts\\python.exe backfill_review_stage.py --auto --wrong-only --from-zero
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    value = str(value).strip().replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            if fmt == "%Y-%m-%d %H:%M:%S" and len(value) >= 19:
                return datetime.strptime(value[:19], fmt)
            return datetime.strptime(value[: len(fmt) + 2] if fmt != "%Y-%m-%d" else value[:10], fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _cluster_sessions(rows, gap_minutes: int) -> int:
    sessions = 0
    last_dt: datetime | None = None
    gap_sec = max(1, gap_minutes) * 60
    for r in rows:
        dt = _parse_dt(r["reviewed_at"])
        if dt is None:
            continue
        if last_dt is None or (dt - last_dt).total_seconds() > gap_sec:
            sessions += 1
        last_dt = dt
    return sessions


def count_item_rounds(conn, item_id: int, gap_minutes: int = 20) -> tuple[int, str]:
    rows = conn.execute(
        """
        SELECT reviewed_at, feedback
        FROM review_logs
        WHERE item_id = ?
        ORDER BY reviewed_at ASC, id ASC
        """,
        (item_id,),
    ).fetchall()
    if not rows:
        return 0, "无作答记录"

    finish_markers = 0
    for r in rows:
        fb = r["feedback"] or ""
        if (
            "本轮为练习" in fb
            or "知识点日程不变" in fb
            or "知识点仍进入下一档" in fb
            or "进入下一档" in fb
        ):
            finish_markers += 1

    sessions = _cluster_sessions(rows, gap_minutes)
    times = max(finish_markers, sessions)
    detail = f"收尾标记={finish_markers}, 时间聚类={sessions} → 采用{times}"
    return times, detail


def count_wrong_rounds(conn, question_id: int, gap_minutes: int = 20) -> tuple[int, str]:
    """错题按该 question_id 的作答记录统计轮次。"""
    rows = conn.execute(
        """
        SELECT reviewed_at, feedback
        FROM review_logs
        WHERE question_id = ?
        ORDER BY reviewed_at ASC, id ASC
        """,
        (question_id,),
    ).fetchall()
    if not rows:
        return 0, "无作答记录"

    markers = 0
    for r in rows:
        fb = r["feedback"] or ""
        if (
            "仅练习" in fb
            or "日程不变" in fb
            or "进入下一档" in fb
            or "保留在错题本" in fb
        ):
            markers += 1

    # 单题复习：每条日志也可视为一次；与时间聚类取较大值更稳妥时用聚类
    sessions = _cluster_sessions(rows, gap_minutes)
    # 错题作答通常一条即一轮；若聚类偏少，用日志条数上限控制（避免把同一次连点算很多次）
    by_logs = len(rows)
    times = max(markers, sessions, 1 if by_logs else 0)
    # 若多条日志但同属一轮（间隔很近），sessions 会更准
    if sessions >= 1:
        times = max(markers, sessions)
    detail = f"标记={markers}, 时间聚类={sessions}, 日志数={by_logs} → 采用{times}"
    return times, detail


def _apply_advances(from_zero: bool, old_stage: int, old_next: str, times: int, advance_fn):
    stage = 0 if from_zero else old_stage
    nxt = old_next
    for _ in range(times):
        stage, nxt = advance_fn(stage)
    return stage, nxt


def main() -> int:
    parser = argparse.ArgumentParser(description="补算知识点/错题本艾宾浩斯档位")
    parser.add_argument("--auto", action="store_true", help="按作答记录自动统计轮次（推荐）")
    parser.add_argument("--times", type=int, default=None, help="固定推进次数")
    parser.add_argument("--ids", type=str, default="", help="知识点 id，如 2,3")
    parser.add_argument("--all", action="store_true", help="全部知识点（可加 --include-wrong）")
    parser.add_argument(
        "--include-wrong",
        action="store_true",
        help="同时补算错题本（与 --all / --ids 一起用）",
    )
    parser.add_argument(
        "--wrong-only",
        action="store_true",
        help="只补算错题本（全部 active 错题）",
    )
    parser.add_argument(
        "--wrong-ids",
        type=str,
        default="",
        help="只补算这些错题本 id，逗号分隔",
    )
    parser.add_argument("--dry-run", action="store_true", help="只打印不写库")
    parser.add_argument("--from-zero", action="store_true", help="从 stage=0 起算")
    parser.add_argument("--gap-minutes", type=int, default=20, help="时间聚类间隔分钟")
    parser.add_argument("--skip-zero", action="store_true", help="轮次为 0 则跳过")
    args = parser.parse_args()

    if args.auto and args.times is not None:
        print("错误: --auto 与 --times 不能同时使用")
        return 1
    if not args.auto and args.times is None:
        print("错误: 请使用 --auto 或 --times N")
        return 1
    if args.times is not None and args.times < 1:
        print("错误: --times 至少为 1")
        return 1

    do_items = bool(args.all or args.ids.strip()) and not args.wrong_only
    do_wrong = bool(args.wrong_only or args.include_wrong or args.wrong_ids.strip())
    if not do_items and not do_wrong:
        print("错误: 请指定 --all / --ids，或 --wrong-only / --wrong-ids，知识点+错题用 --all --include-wrong")
        return 1
    if args.all and args.ids.strip():
        print("错误: --all 与 --ids 不能同时使用")
        return 1

    from app.db import init_db, db_session
    from app.ebbinghaus import advance_after_correct, now_iso, stage_label

    init_db()
    reviewed = now_iso()
    mode = "自动按作答轮次" if args.auto else f"固定次数={args.times}"
    print(f"模式: {mode}；{'从 stage0 起算' if args.from_zero else '从当前 stage 起算'}")
    print(f"{'【演练 dry-run】' if args.dry_run else '【写入数据库】'}")
    print("-" * 60)

    changed = skipped = 0

    with db_session() as conn:
        if do_items:
            if args.ids.strip():
                id_list = [int(x.strip()) for x in args.ids.split(",") if x.strip()]
                placeholders = ",".join("?" * len(id_list))
                rows = conn.execute(
                    f"""
                    SELECT id, title, stage, next_review_at
                    FROM knowledge_items WHERE id IN ({placeholders}) ORDER BY id
                    """,
                    tuple(id_list),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT id, title, stage, next_review_at
                    FROM knowledge_items ORDER BY id
                    """
                ).fetchall()

            print(f"[知识点] {len(rows)} 条")
            for r in rows:
                old_stage = int(r["stage"] or 0)
                old_next = r["next_review_at"]
                item_id = int(r["id"])
                title = (r["title"] or "")[:40]
                if args.auto:
                    times, detail = count_item_rounds(conn, item_id, args.gap_minutes)
                else:
                    times, detail = int(args.times), f"固定 times={args.times}"

                if times <= 0:
                    print(f"  知识#{item_id} {title} 跳过（{detail}）")
                    skipped += 1
                    continue

                stage, nxt = _apply_advances(
                    args.from_zero, old_stage, old_next, times, advance_after_correct
                )
                print(
                    f"  知识#{item_id} {title}\n"
                    f"    {detail}\n"
                    f"    {old_stage}({stage_label(old_stage)}) @ {old_next}\n"
                    f"    -> +{times} -> {stage}({stage_label(stage)}) @ {nxt}"
                )
                if not args.dry_run:
                    conn.execute(
                        """
                        UPDATE knowledge_items
                        SET stage=?, next_review_at=?, last_reviewed_at=? WHERE id=?
                        """,
                        (stage, nxt, reviewed, item_id),
                    )
                changed += 1

        if do_wrong:
            if args.wrong_ids.strip():
                wids = [int(x.strip()) for x in args.wrong_ids.split(",") if x.strip()]
                placeholders = ",".join("?" * len(wids))
                wrongs = conn.execute(
                    f"""
                    SELECT w.id, w.item_id, w.question_id, w.stage, w.next_review_at, w.active,
                           k.title AS item_title
                    FROM wrong_book w
                    LEFT JOIN knowledge_items k ON k.id = w.item_id
                    WHERE w.id IN ({placeholders})
                    ORDER BY w.id
                    """,
                    tuple(wids),
                ).fetchall()
            else:
                wrongs = conn.execute(
                    """
                    SELECT w.id, w.item_id, w.question_id, w.stage, w.next_review_at, w.active,
                           k.title AS item_title
                    FROM wrong_book w
                    LEFT JOIN knowledge_items k ON k.id = w.item_id
                    WHERE w.active = 1
                    ORDER BY w.id
                    """
                ).fetchall()

            print(f"[错题本] {len(wrongs)} 条")
            for w in wrongs:
                old_stage = int(w["stage"] or 0)
                old_next = w["next_review_at"]
                wid = int(w["id"])
                qid = int(w["question_id"])
                title = (w["item_title"] or f"item={w['item_id']}")[:40]
                if args.auto:
                    times, detail = count_wrong_rounds(conn, qid, args.gap_minutes)
                else:
                    times, detail = int(args.times), f"固定 times={args.times}"

                if times <= 0:
                    print(f"  错题#{wid} {title} q={qid} 跳过（{detail}）")
                    skipped += 1
                    continue

                stage, nxt = _apply_advances(
                    args.from_zero, old_stage, old_next, times, advance_after_correct
                )
                print(
                    f"  错题#{wid} {title} (q={qid})\n"
                    f"    {detail}\n"
                    f"    {old_stage}({stage_label(old_stage)}) @ {old_next}\n"
                    f"    -> +{times} -> {stage}({stage_label(stage)}) @ {nxt}"
                )
                if not args.dry_run:
                    conn.execute(
                        """
                        UPDATE wrong_book
                        SET stage=?, next_review_at=?, last_reviewed_at=? WHERE id=?
                        """,
                        (stage, nxt, reviewed, wid),
                    )
                changed += 1

    print("-" * 60)
    print(
        f"完成：处理 {changed} 条，跳过 {skipped} 条"
        + ("（未写库）" if args.dry_run else "。")
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
