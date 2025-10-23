# -*- coding: utf-8 -*-
r"""
DuckCoding quota watcher
- Polls the DuckCoding quota API every 60 seconds for a single token
- When remaining quota > ¥5, shows a Windows notification (toast via win10toast if available,
  otherwise falls back to a Win32 MessageBox)

Background tips:
- Run with pythonw.exe to avoid a console window, e.g.:
    pythonw d:\\User_Files\\Program Files\\DuckCodingAlert\\duckcoding_quota_watcher.py
- Or create a Windows Task Scheduler task to run every minute.

Requires: requests
Optional: win10toast (recommended)

pip install requests win10toast
"""
from __future__ import annotations
import json
import re
import time
import ctypes
from typing import Any, Dict, Optional
import os
import subprocess
from pathlib import Path
import sys
import warnings
try:
    import winsound  # type: ignore
except Exception:
    winsound = None  # type: ignore

try:
    import requests  # type: ignore
except Exception as e:  # pragma: no cover
    raise SystemExit("Please install requests: pip install requests") from e

# Ensure UTF-8 console output for symbols like '¥' when possible
try:
    sys.stdout.reconfigure(encoding='utf-8')  # type: ignore[attr-defined]
    sys.stderr.reconfigure(encoding='utf-8')  # type: ignore[attr-defined]
except Exception:
    pass

# ========== CONFIG ==========
API_URL = "https://jp.duckcoding.com/api/usage/token/"
# Fallback token only used if auto-fetch and env var both fail
TOKEN_FALLBACK = "sk-123456"
POLL_INTERVAL_SEC = 60
THRESHOLD_YEN = 5.0
# Notification behavior
NOTIFY_LIMIT_BEFORE_BLOCK = 5  # After this many notifications, show blocking dialog and exit
SOUND_ALIAS_PRIMARY = "SystemQuestion"  # Less common than SystemNotification
SOUND_ALIAS_FALLBACK = "SystemAsterisk"
BEEP_FREQUENCY_HZ = 1200
BEEP_DURATION_MS = 250
# ============================

# Runtime toggles (set by CLI)
FORCE_MESSAGEBOX = False

# Toast notifier (optional)
_toaster = None
try:
    # Suppress pkg_resources deprecation warning emitted by win10toast import
    warnings.filterwarnings(
        'ignore',
        message='pkg_resources is deprecated',
        category=UserWarning,
    )
    from win10toast import ToastNotifier  # type: ignore
    _toaster = ToastNotifier()
except Exception:
    _toaster = None

_money_re = re.compile(r"[-+]?(?:\d+(?:,\d{3})*|\d+)(?:\.\d+)?")


def _parse_money(value: Any) -> float:
    """Parse strings like "¥149.64" or numbers to float (Yen)."""
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value)
    m = _money_re.search(s)
    if not m:
        return 0.0
    return float(m.group(0).replace(",", ""))


def _notify(title: str, msg: str) -> None:
    def _play_sound() -> None:
        # Try Windows system notification sound, then message beep, then kernel beep
        try:
            if winsound is not None:
                try:
                    # Use a less common system alias for distinctiveness
                    winsound.PlaySound(SOUND_ALIAS_PRIMARY, winsound.SND_ALIAS | winsound.SND_ASYNC)  # type: ignore
                    return
                except Exception:
                    try:
                        winsound.PlaySound(SOUND_ALIAS_FALLBACK, winsound.SND_ALIAS | winsound.SND_ASYNC)  # type: ignore
                        return
                    except Exception:
                        try:
                            winsound.MessageBeep(getattr(winsound, 'MB_ICONQUESTION', 0x00000020))  # type: ignore
                            return
                        except Exception:
                            pass
            # Fallback kernel beep
            try:
                ctypes.windll.kernel32.Beep(BEEP_FREQUENCY_HZ, BEEP_DURATION_MS)
            except Exception:
                pass
        except Exception:
            pass

    if _toaster is not None and not FORCE_MESSAGEBOX:
        try:
            _toaster.show_toast(title, msg, duration=5, threaded=True)
            _play_sound()
            # Let it display for a moment
            for _ in range(50):
                if not _toaster.notification_active():
                    break
                time.sleep(0.1)
            return
        except Exception:
            pass
    # Fallback: Win32 MessageBox (information icon, OK)
    try:
        _play_sound()
        ctypes.windll.user32.MessageBoxW(0, msg, title, 0x00000040)
    except Exception:
        pass


def _extract_remaining(data: Dict[str, Any]) -> float:
    """Try multiple shapes to extract remaining Yen from API data."""
    # 1) Common 'totals' block
    totals = data.get("totals") or {}
    for k in ("remaining", "remaining_yen", "remain", "remain_yen"):
        if k in totals:
            return _parse_money(totals[k])

    # 2) Top-level fields
    for k in ("remaining", "remaining_yen", "remain", "remain_yen"):
        if k in data:
            return _parse_money(data[k])

    # 3) credit summary
    credit = data.get("credit") or {}
    for k in ("remaining", "remaining_yen"):
        if k in credit:
            return _parse_money(credit[k])

    # 4) any string that looks like money under 'summary' or 'stats'
    for blk_key in ("summary", "stats", "balance", "limits"):
        blk = data.get(blk_key) or {}
        for v in blk.values():
            val = _parse_money(v)
            if val > 0:
                # Best guess
                return val

    # 5) compute from total/used if present
    total = None
    used = None
    for k in ("total_yen", "total", "total_amount"):
        if k in data:
            total = _parse_money(data[k])
            break
    for k in ("used_yen", "used", "used_amount"):
        if k in data:
            used = _parse_money(data[k])
            break
    if total is not None and used is not None:
        return max(0.0, float(total) - float(used))

    return 0.0


def fetch_remaining_yen(token: str) -> float:
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "User-Agent": "DuckCodingQuotaWatcher/1.0",
    }
    resp = requests.get(API_URL, headers=headers, timeout=10)
    resp.raise_for_status()
    payload = resp.json()
    data = payload.get("data", payload)
    return _extract_remaining(data)


def _fetch_remaining_yen_via_site(token: str) -> Optional[float]:
    """Use Playwright (Node) to read remaining Yen from check.duckcoding.com UI."""
    try:
        script_path = Path(__file__).with_name("scripts") / "query_remaining_from_site.js"
        if not script_path.exists():
            return None
        out = subprocess.check_output(
            ["node", str(script_path), token],
            stderr=subprocess.STDOUT,
            timeout=60,
            text=True,
            cwd=str(Path(__file__).parent),
        ).strip()
        m = re.search(r"([-+]?\d+(?:\.\d+)?)", out)
        return float(m.group(1)) if m else None
    except Exception as e:
        try:
            msg = getattr(e, "output", "") or str(e)
        except Exception:
            msg = str(e)
        print("[DuckCoding] UI scrape remaining failed:", msg)
        return None


def fetch_remaining_yen_best(token: str) -> float:
    """Prefer UI-scraped remaining (matches website) then fall back to API heuristic."""
    val = _fetch_remaining_yen_via_site(token)
    if isinstance(val, (int, float)) and val >= 0:
        return float(val)
    return fetch_remaining_yen(token)


def _auto_fetch_token_via_playwright() -> Optional[str]:
    """
    Use Node + Playwright to open https://check.duckcoding.com/ and reveal
    the CodeX token automatically. Returns token string if found, else None.
    """
    try:
        script_path = Path(__file__).with_name("scripts") / "fetch_codex_token.js"
        if not script_path.exists():
            return None
        # Prefer system 'node'
        out = subprocess.check_output(
            ["node", str(script_path)],
            stderr=subprocess.STDOUT,
            timeout=45,
            text=True,
            cwd=str(Path(__file__).parent),
        ).strip()
        m = re.search(r"(sk-[A-Za-z0-9]+)", out)
        return m.group(1) if m else None
    except Exception as e:
        try:
            msg = getattr(e, "output", "") or str(e)
        except Exception:
            msg = str(e)
        print("[DuckCoding] Auto-fetch token failed:", msg)
        return None


def resolve_token() -> str:
    """Resolve DuckCoding token with precedence: env -> auto-fetch -> fallback."""
    env_token = (os.getenv("DUCKCODING_TOKEN") or "").strip()
    if env_token.startswith("sk-"):
        print("[DuckCoding] Using token from environment DUCKCODING_TOKEN")
        return env_token
    auto = _auto_fetch_token_via_playwright()
    if auto:
        print("[DuckCoding] Using token auto-fetched from CodeX 专用福利")
        return auto
    print("[DuckCoding] Using fallback token (auto-fetch/env missing)")
    return TOKEN_FALLBACK


def main() -> None:
    print("[DuckCoding] quota watcher started. Checking every", POLL_INTERVAL_SEC, "seconds")
    token = resolve_token()
    notify_count = 0
    while True:
        try:
            remaining = fetch_remaining_yen_best(token)
            print(f"[DuckCoding] Remaining: ¥{remaining:.2f}")
            # 只要高于阈值就触发一次通知（每次轮询都会触发）
            if remaining > THRESHOLD_YEN:
                _notify("DuckCoding 额度提醒", f"当前剩余额度：¥{remaining:.2f}，已超过阈值 ¥{THRESHOLD_YEN:.2f}")
                notify_count += 1
                if notify_count >= NOTIFY_LIMIT_BEFORE_BLOCK:
                    try:
                        # Blocking info icon
                        ctypes.windll.user32.MessageBoxW(
                            0,
                            f"累计提醒已达到 {NOTIFY_LIMIT_BEFORE_BLOCK} 次，程序将退出。当前余额：¥{remaining:.2f}",
                            "DuckCoding 额度提醒（终止）",
                            0x00000040,
                        )
                    except Exception:
                        pass
                    # Exit program after blocking dialog
                    return
        except Exception as e:
            print("[DuckCoding] Error:", e)
        finally:
            time.sleep(POLL_INTERVAL_SEC)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="DuckCoding quota watcher")
    parser.add_argument("--test-notify", action="store_true", help="Trigger a test notification with sound and exit")
    parser.add_argument("--force-messagebox", action="store_true", help="Force using MessageBox instead of toast for notifications")
    parser.add_argument("--once", action="store_true", help="Run a single check and exit (will notify if over threshold)")
    args = parser.parse_args()

    # Set runtime toggle for notification backend
    FORCE_MESSAGEBOX = bool(args.force_messagebox)

    if args.test_notify:
        _notify("DuckCoding 测试通知", "这是声音与弹窗测试 (带提示音)")
        sys.exit(0)

    if args.once:
        token = resolve_token()
        try:
            remaining = fetch_remaining_yen_best(token)
            print(f"[DuckCoding] Remaining: ¥{remaining:.2f}")
            if remaining > THRESHOLD_YEN:
                _notify("DuckCoding 额度提醒", f"当前剩余额度：¥{remaining:.2f}，已超过阈值 ¥{THRESHOLD_YEN:.2f}")
        except Exception as e:
            print("[DuckCoding] Error:", e)
        sys.exit(0)

    main()
