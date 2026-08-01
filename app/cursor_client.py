"""Cursor Cloud Agents 客户端（对齐 askreolink：requests + 轮询）。"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, Optional, Tuple

import requests

CURSOR_API_BASE = "https://api.cursor.com/v1"
TERMINAL_STATUSES = {"FINISHED", "ERROR", "CANCELLED", "EXPIRED"}
CREATE_TIMEOUT = 120
POLL_TIMEOUT = 60
MAX_WAIT_SECONDS = 180
POLL_INTERVAL = 2.0


def _session() -> requests.Session:
    # 不走系统代理，避免 ConnectError / 代理中断
    s = requests.Session()
    s.trust_env = False
    return s


def _headers(api_key: str) -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


def verify_cursor_api_key(api_key: str) -> Dict[str, str]:
    with _session() as s:
        resp = s.get(
            f"{CURSOR_API_BASE}/me",
            headers=_headers(api_key),
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        return {
            "api_key_name": str(data.get("apiKeyName") or ""),
            "user_email": str(data.get("userEmail") or ""),
        }


def create_no_repo_agent(
    api_key: str,
    prompt_text: str,
    model_id: str = "auto",
    name: str = "ebbinghaus-review",
) -> Tuple[str, str]:
    payload: Dict[str, Any] = {
        "prompt": {"text": prompt_text},
        "name": name[:100],
    }
    normalized = (model_id or "").strip().lower()
    if normalized and normalized not in {"", "default"}:
        payload["model"] = {"id": model_id.strip()}

    with _session() as s:
        try:
            resp = s.post(
                f"{CURSOR_API_BASE}/agents",
                headers=_headers(api_key),
                json=payload,
                timeout=CREATE_TIMEOUT,
            )
        except requests.exceptions.RequestException as e:
            raise RuntimeError(
                f"无法连接 api.cursor.com: {type(e).__name__}: {e or repr(e)}"
            ) from e

    if resp.status_code >= 400:
        raise RuntimeError(f"创建 Cursor Agent 失败 {resp.status_code}: {resp.text[:500]}")
    data = resp.json()
    agent = data.get("agent") or {}
    run = data.get("run") or {}
    agent_id = str(agent.get("id") or "").strip()
    run_id = str(run.get("id") or agent.get("latestRunId") or "").strip()
    if not agent_id or not run_id:
        raise RuntimeError(f"Cursor Agent 未返回 agent/run ID，响应={str(data)[:400]}")
    return agent_id, run_id


def get_run(api_key: str, agent_id: str, run_id: str) -> Dict[str, Any]:
    with _session() as s:
        resp = s.get(
            f"{CURSOR_API_BASE}/agents/{agent_id}/runs/{run_id}",
            headers=_headers(api_key),
            timeout=POLL_TIMEOUT,
        )
    if resp.status_code >= 400:
        raise RuntimeError(f"查询 Cursor Run 失败 {resp.status_code}: {resp.text[:300]}")
    return resp.json()


def archive_agent(api_key: str, agent_id: str) -> None:
    try:
        with _session() as s:
            s.post(
                f"{CURSOR_API_BASE}/agents/{agent_id}/archive",
                headers=_headers(api_key),
                timeout=30,
            )
    except Exception:
        pass


def wait_for_run_result(api_key: str, agent_id: str, run_id: str) -> str:
    deadline = time.time() + MAX_WAIT_SECONDS
    last_status = ""
    while time.time() < deadline:
        run = get_run(api_key, agent_id, run_id)
        status = str(run.get("status") or "").strip()
        last_status = status or last_status
        if status in TERMINAL_STATUSES:
            if status == "FINISHED":
                result = str(run.get("result") or "").strip()
                if result:
                    return result
                raise RuntimeError("Cursor Agent 已完成但未返回 result")
            raise RuntimeError(f"Cursor Agent 运行失败，状态={status}")
        time.sleep(POLL_INTERVAL)
    raise RuntimeError(f"Cursor Agent 等待超时（最后状态={last_status or 'unknown'}）")


def generate_via_cursor_agent_sync(
    api_key: str,
    prompt_text: str,
    model_id: str = "auto",
) -> str:
    agent_id = ""
    try:
        agent_id, run_id = create_no_repo_agent(api_key, prompt_text, model_id=model_id)
        return wait_for_run_result(api_key, agent_id, run_id)
    finally:
        if agent_id:
            archive_agent(api_key, agent_id)


async def generate_via_cursor_agent(
    api_key: str,
    prompt_text: str,
    model_id: str = "auto",
) -> str:
    return await asyncio.to_thread(
        generate_via_cursor_agent_sync, api_key, prompt_text, model_id
    )
