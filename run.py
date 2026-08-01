"""
启动本地艾宾浩斯复习服务 + 桌面提醒。

用法:
  python run.py
  python run.py --no-notify

推荐使用项目虚拟环境（已自动切换）:
  .venv\\Scripts\\python.exe run.py
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def _ensure_venv() -> None:
    """若存在 .venv 且当前解释器不是它，则自动改用 .venv 重新启动。"""
    if os.environ.get("EBBINGHAUS_SKIP_VENV") == "1":
        return
    venv_python = ROOT / ".venv" / "Scripts" / "python.exe"
    if not venv_python.exists():
        venv_python = ROOT / ".venv" / "bin" / "python"
    if not venv_python.exists():
        return
    current = Path(sys.executable).resolve()
    target = venv_python.resolve()
    if current == target:
        return
    os.environ["EBBINGHAUS_SKIP_VENV"] = "1"
    os.execv(str(target), [str(target), str(ROOT / "run.py"), *sys.argv[1:]])


_ensure_venv()

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> None:
    parser = argparse.ArgumentParser(description="艾宾浩斯本地复习 Web")
    parser.add_argument("--no-notify", action="store_true", help="禁用桌面 Toast 轮询")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args()

    try:
        import httpx  # noqa: F401
        import uvicorn  # noqa: F401
    except ImportError as e:
        print("缺少依赖，请先安装：")
        print(r"  .\.venv\Scripts\python.exe -m pip install -r requirements.txt")
        print(f"详情: {e}")
        sys.exit(1)

    from app.config import ensure_dirs, host_port
    from app.db import init_db

    ensure_dirs()
    init_db()

    host, port = host_port()
    if args.host:
        host = args.host
    if args.port:
        port = args.port

    if not args.no_notify:
        from app.notifier import start_notifier_thread

        start_notifier_thread()
        print("桌面提醒已开启（到点且有待复习时弹 Toast）")

    print(f"使用解释器: {sys.executable}")
    print(f"打开浏览器访问: http://{host}:{port}/")
    import uvicorn

    uvicorn.run("app.main:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    main()
