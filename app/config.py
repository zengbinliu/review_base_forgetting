"""应用配置：路径、环境变量、可持久化设置。"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"
UPLOADS_DIR = DATA_DIR / "uploads"
DB_PATH = DATA_DIR / "app.db"
SETTINGS_PATH = DATA_DIR / "settings.json"
ENV_PATH = ROOT_DIR / ".env"

load_dotenv(ENV_PATH, override=True)

EBBINGHAUS_INTERVALS_DAYS = [1, 2, 4, 7, 15, 30]

_FALLBACK_BASE = "https://api.deepseek.com/v1"
_FALLBACK_MODEL = "deepseek-v4-flash"


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)


def _write_env_value(key: str, value: str) -> None:
    """更新 .env 中某一行，并同步到当前进程环境变量。"""
    value = (value or "").strip()
    lines: list[str] = []
    if ENV_PATH.exists():
        lines = ENV_PATH.read_text(encoding="utf-8").splitlines()
    found = False
    new_lines: list[str] = []
    prefix = f"{key}="
    for line in lines:
        if line.startswith(prefix):
            new_lines.append(f"{prefix}{value}")
            found = True
        else:
            new_lines.append(line)
    if not found:
        new_lines.append(f"{prefix}{value}")
    ENV_PATH.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    os.environ[key] = value


def get_api_key() -> str:
    """只读取/刷新 API Key，不覆盖已由 settings.json 生效的 base_url/model。"""
    if ENV_PATH.exists():
        try:
            for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
                if line.startswith("LLM_API_KEY="):
                    key = line.split("=", 1)[1].strip().strip('"').strip("'")
                    os.environ["LLM_API_KEY"] = key
                    return key
        except OSError:
            pass
    return os.getenv("LLM_API_KEY", "").strip()


def api_key_configured() -> bool:
    key = get_api_key()
    if not key:
        return False
    if key.startswith("sk-your-key"):
        return False
    return True


def set_api_key(key: str) -> None:
    """将 API Key 写回 .env（保留其他行）。"""
    _write_env_value("LLM_API_KEY", key.strip())


def load_settings() -> dict:
    """
    读取运行时配置。
    优先级：data/settings.json > .env > 内置默认值。
    读到后同步 llm_* 到 os.environ，保证整进程一致。
    """
    ensure_dirs()
    # 先读 .env 作默认，但不要 override 掉进程里已由 settings 写入的值
    load_dotenv(ENV_PATH, override=False)
    settings = {
        "llm_base_url": os.getenv("LLM_BASE_URL", _FALLBACK_BASE),
        "llm_model": os.getenv("LLM_MODEL", _FALLBACK_MODEL),
        "desktop_notify": True,
        "browser_notify": True,
        "notify_hour": 9,
        "notify_minute": 0,
        "web_search_enabled": False,
        "web_search_for_grade": False,
    }
    if SETTINGS_PATH.exists():
        try:
            stored = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
            if isinstance(stored, dict):
                for k, v in stored.items():
                    if k in {
                        "llm_base_url",
                        "llm_model",
                        "desktop_notify",
                        "browser_notify",
                        "notify_hour",
                        "notify_minute",
                        "web_search_enabled",
                        "web_search_for_grade",
                    }:
                        settings[k] = v
        except (json.JSONDecodeError, OSError):
            pass

    settings["llm_base_url"] = str(settings.get("llm_base_url") or _FALLBACK_BASE).strip().rstrip(
        "/"
    )
    settings["llm_model"] = str(settings.get("llm_model") or _FALLBACK_MODEL).strip()

    os.environ["LLM_BASE_URL"] = settings["llm_base_url"]
    os.environ["LLM_MODEL"] = settings["llm_model"]
    return settings


def save_settings(updates: dict) -> dict:
    """保存到 settings.json，并同步 LLM 相关项到 .env。"""
    settings = load_settings()
    allowed = {
        "llm_base_url",
        "llm_model",
        "desktop_notify",
        "browser_notify",
        "notify_hour",
        "notify_minute",
        "web_search_enabled",
        "web_search_for_grade",
    }
    for k, v in updates.items():
        if k in allowed:
            settings[k] = v

    settings["llm_base_url"] = str(settings.get("llm_base_url") or _FALLBACK_BASE).strip().rstrip(
        "/"
    )
    settings["llm_model"] = str(settings.get("llm_model") or _FALLBACK_MODEL).strip()

    SETTINGS_PATH.write_text(
        json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # 与 .env 双向对齐，防止下次启动/其它逻辑读到旧值
    _write_env_value("LLM_BASE_URL", settings["llm_base_url"])
    _write_env_value("LLM_MODEL", settings["llm_model"])

    return load_settings()


def effective_llm_config() -> dict[str, Any]:
    """当前实际用于出题/判分的 LLM 配置摘要。"""
    s = load_settings()
    key = get_api_key()
    return {
        "llm_base_url": s["llm_base_url"],
        "llm_model": s["llm_model"],
        "api_key_configured": api_key_configured(),
        "api_key_suffix": (key[-6:] if len(key) >= 6 else ""),
        "api_key_type": "cursor" if key.startswith("crsr_") else "openai",
    }


def host_port() -> tuple[str, int]:
    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "8765"))
    return host, port
