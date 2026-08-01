"""本机桌面 Toast 提醒（轮询到期复习）。"""
from __future__ import annotations

import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

from app.config import DATA_DIR, load_settings
from app.db import init_db
from app.ebbinghaus import today_str
from app import services

# 借用已注册的 PowerShell AUMID，无需管理员注册即可弹出系统 Toast
_PS_AUMID = (
    r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"
)


def _xml_escape(s: str) -> str:
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def _run_ps(script: str, timeout: int = 25) -> tuple[bool, str]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ps1 = DATA_DIR / "_toast_run.ps1"
    ps1.write_text(script, encoding="utf-8-sig")
    try:
        r = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(ps1),
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
        out = (r.stdout or "") + (r.stderr or "")
        return r.returncode == 0, out.strip()
    except Exception as e:
        return False, str(e)


def _show_toast_winrt(title: str, message: str) -> bool:
    """Windows 10/11 系统通知中心 Toast（使用已注册 PowerShell AUMID）。"""
    title_x = _xml_escape(title)[:80]
    msg_x = _xml_escape(message)[:220]
    aumid = _PS_AUMID.replace("'", "''")
    script = f"""
$ErrorActionPreference = 'Stop'
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null

$xml = @"
<toast>
  <visual>
    <binding template="ToastGeneric">
      <text>{title_x}</text>
      <text>{msg_x}</text>
    </binding>
  </visual>
</toast>
"@

$doc = New-Object Windows.Data.Xml.Dom.XmlDocument
$doc.LoadXml($xml)
$toast = [Windows.UI.Notifications.ToastNotification]::new($doc)
$toast.Tag = 'EbbinghausReview'
$toast.Group = 'EbbinghausReview'
$notifier = [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{aumid}')
$notifier.Show($toast)
Write-Output 'OK'
"""
    ok, out = _run_ps(script)
    if not ok or "OK" not in out:
        print(f"[notify] winrt failed: {out or 'no output'}")
        return False
    return True


def _show_toast_template(title: str, message: str) -> bool:
    """备用：ToastText02 模板。"""
    t = title.replace("'", "''")[:80]
    m = message.replace("'", "''")[:220]
    aumid = _PS_AUMID.replace("'", "''")
    script = f"""
$ErrorActionPreference = 'Stop'
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
$template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent(
  [Windows.UI.Notifications.ToastTemplateType]::ToastText02
)
$raw = [xml]$template.GetXml()
($raw.toast.visual.binding.text | Where-Object {{ $_.id -eq '1' }}).AppendChild($raw.CreateTextNode('{t}')) | Out-Null
($raw.toast.visual.binding.text | Where-Object {{ $_.id -eq '2' }}).AppendChild($raw.CreateTextNode('{m}')) | Out-Null
$doc = New-Object Windows.Data.Xml.Dom.XmlDocument
$doc.LoadXml($raw.OuterXml)
$toast = [Windows.UI.Notifications.ToastNotification]::new($doc)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{aumid}').Show($toast)
Write-Output 'OK'
"""
    ok, out = _run_ps(script)
    if not ok or "OK" not in out:
        print(f"[notify] template toast failed: {out or 'no output'}")
        return False
    return True


def _show_popup_form(title: str, message: str) -> bool:
    """最后回退：置顶小窗（一定可见，不依赖通知权限）。"""
    t = title.replace("'", "''")[:80]
    m = message.replace("'", "''")[:300]
    # 独立进程弹出，避免阻塞服务；不 Sleep 太久
    script = f"""
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$form = New-Object System.Windows.Forms.Form
$form.Text = '{t}'
$form.Width = 420
$form.Height = 180
$form.StartPosition = 'CenterScreen'
$form.TopMost = $true
$form.FormBorderStyle = 'FixedDialog'
$form.MaximizeBox = $false
$form.MinimizeBox = $false
$label = New-Object System.Windows.Forms.Label
$label.Text = '{m}'
$label.AutoSize = $false
$label.Dock = 'Fill'
$label.Padding = New-Object System.Windows.Forms.Padding(16)
$label.Font = New-Object System.Drawing.Font('Microsoft YaHei UI', 10)
$btn = New-Object System.Windows.Forms.Button
$btn.Text = '知道了'
$btn.Dock = 'Bottom'
$btn.Height = 36
$btn.Add_Click({{ $form.Close() }})
$form.Controls.Add($label)
$form.Controls.Add($btn)
$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = 12000
$timer.Add_Tick({{ $form.Close() }})
$timer.Start()
[void]$form.ShowDialog()
"""
    try:
        subprocess.Popen(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-WindowStyle",
                "Normal",
                "-Command",
                script,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    except Exception as e:
        print(f"[notify] popup failed: {e}")
        return False


def show_toast(title: str, message: str) -> bool:
    print(f"[notify] {title}: {message}")
    if _show_toast_winrt(title, message):
        return True
    if _show_toast_template(title, message):
        return True
    # 通知中心不可用时，至少弹窗让用户看见
    return _show_popup_form(title, message)


def check_and_notify_once(*, force: bool = False) -> dict:
    """
    到点后若有待复习则弹桌面提醒。
    - 同一天同一批到期内容只提醒一次；新增到期项会再次提醒。
    - force=True 用于设置页「测试提醒」，忽略时刻与去重。
    """
    init_db()
    settings = load_settings()
    payload = services.collect_due_notify()
    result = {
        "ok": False,
        "skipped": None,
        "count": payload["count"],
        "titles": payload["titles"],
        "method": None,
    }

    if not force and not settings.get("desktop_notify", True):
        result["skipped"] = "desktop_notify_disabled"
        return result

    now = datetime.now()
    hour = int(settings.get("notify_hour", 9))
    minute = int(settings.get("notify_minute", 0))
    if not force and (now.hour, now.minute) < (hour, minute):
        result["skipped"] = f"before_notify_time_{hour:02d}:{minute:02d}"
        return result

    if not force and payload["count"] <= 0:
        result["skipped"] = "no_due_items"
        return result

    today = today_str()
    last_date, last_fp = services.get_notify_desktop_state()
    fp = payload["fingerprint"] or "empty"
    if not force and last_date == today and last_fp == fp:
        result["skipped"] = "already_notified_same_due_set"
        return result

    if payload["count"] <= 0 and force:
        title = "艾宾浩斯复习 · 提醒测试"
        message = "桌面提醒通路正常（当前暂无待复习项）"
    else:
        titles = "、".join(payload["titles"][:3]) or "待复习"
        more = (
            f" 等 {payload['count']} 项"
            if payload["count"] > 3
            else f"（共 {payload['count']} 项）"
        )
        title = "艾宾浩斯复习 · 今日提醒"
        message = f"待复习：{titles}{more}"

    shown = show_toast(title, message)
    if shown and not force:
        services.set_notify_desktop_state(today, fp)
    result["ok"] = shown
    if not shown:
        result["skipped"] = "toast_failed"
    return result


def start_notifier_thread(interval_sec: int = 60) -> threading.Thread:
    def loop() -> None:
        time.sleep(5)
        while True:
            try:
                r = check_and_notify_once()
                if r.get("ok"):
                    print(f"[notifier] sent toast, count={r.get('count')}")
                elif r.get("skipped") and not str(r.get("skipped")).startswith(
                    "before_notify_time"
                ) and r.get("skipped") not in {
                    "no_due_items",
                    "already_notified_same_due_set",
                    "desktop_notify_disabled",
                }:
                    print(f"[notifier] skip={r.get('skipped')}")
            except Exception as e:
                print(f"[notifier] error: {e}")
            time.sleep(interval_sec)

    t = threading.Thread(target=loop, name="desktop-notifier", daemon=True)
    t.start()
    return t
