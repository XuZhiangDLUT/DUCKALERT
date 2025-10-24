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
from typing import Any, Dict, Optional, Tuple, List
from dataclasses import dataclass
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

# Ensure UTF-8 console output for symbols like '¥' and Chinese labels when possible
# Also adjust Windows console code page to UTF-8 to avoid mojibake like "涓撶敤绂忓埄"
try:
    sys.stdout.reconfigure(encoding='utf-8')  # type: ignore[attr-defined]
    sys.stderr.reconfigure(encoding='utf-8')  # type: ignore[attr-defined]
except Exception:
    pass

# On Windows consoles, force UTF-8 code page so PowerShell/CMD can display Chinese correctly
try:
    import platform
    if platform.system() == 'Windows':
        try:
            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
            ctypes.windll.kernel32.SetConsoleCP(65001)
        except Exception:
            pass
except Exception:
    pass

# ========== CONFIG ==========
API_URL = "https://jp.duckcoding.com/api/usage/token/"
# Fallback token only used if auto-fetch and env var both fail
TOKEN_FALLBACK = "sk-123456"
POLL_INTERVAL_SEC = 60
THRESHOLD_YEN = 3.0
# Notification behavior
NOTIFY_LIMIT_BEFORE_BLOCK = 5  # After this many notifications, show blocking dialog and exit
SOUND_ALIAS_PRIMARY = "SystemQuestion"  # Less common than SystemNotification
SOUND_ALIAS_FALLBACK = "SystemAsterisk"
BEEP_FREQUENCY_HZ = 1200
BEEP_DURATION_MS = 250
# ============================

# Runtime toggles (set by CLI)
FORCE_MESSAGEBOX = False
FORCE_TOAST = False

# Cache for benefit tokens to avoid launching a browser every loop
_BENEFIT_TOKEN_CACHE: Dict[str, str] = {}
_BENEFIT_TOKEN_CACHE_TS: float = 0.0
_BENEFIT_TOKEN_CACHE_TTL_SEC: float = 600.0  # 10 minutes

# Interactive ack file and Phase-B thresholds
ACK_FILE = Path(__file__).with_name('duckcoding_ack.txt')
PHASE_B_THRESHOLDS: List[float] = [50.0, 20.0, 10.0, 5.0]

# Toast notifier (optional). Disabled by default to avoid rare WNDPROC/WPARAM console noise on some hosts.
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

# Default to Windows toast notifications (non-blocking). Fallback to MessageBox if needed.
USE_TOAST_BY_DEFAULT = True

_money_re = re.compile(r"[-+]?(?:\d+(?:,\d{3})*|\d+)(?:\.\d+)?")


@dataclass
class QuotaDetails:
    name: str = ""
    total_yen: float = 0.0
    used_yen: float = 0.0
    used_percent: float = 0.0
    remaining_yen: float = 0.0


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

    def _toast_via_subprocess(title: str, body: str) -> bool:
        """Fire-and-forget toast in a separate Python process to avoid WNDPROC noise or blocking."""
        try:
            py = sys.executable or 'python'
            # Use a small Python one-liner to show toast non-threaded inside the child process
            code = (
                "import sys,time; "
                "from win10toast import ToastNotifier; "
                "ToastNotifier().show_toast(sys.argv[1], sys.argv[2], duration=5, threaded=False)"
            )
            # Hide window if possible
            creationflags = 0
            try:
                creationflags = 0x08000000  # CREATE_NO_WINDOW
            except Exception:
                creationflags = 0
            subprocess.Popen(
                [py, '-c', code, str(title), str(body)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                cwd=str(Path(__file__).parent),
                creationflags=creationflags,
            )
            return True
        except Exception:
            return False

    # Prefer non-blocking Windows toast by default
    _play_sound()
    if not FORCE_MESSAGEBOX and (FORCE_TOAST or USE_TOAST_BY_DEFAULT):
        ok = _toast_via_subprocess(title, msg)
        if ok:
            return
        # If toast failed, fall back to MessageBox

    # Fallback: Windows MessageBox (blocking). Keep it visible and foreground.
    try:
        MB_OK = 0x00000000
        MB_ICONINFORMATION = 0x00000040
        MB_SYSTEMMODAL = 0x00001000
        MB_SETFOREGROUND = 0x00010000
        MB_TOPMOST = 0x00040000
        flags = MB_OK | MB_ICONINFORMATION | MB_SYSTEMMODAL | MB_SETFOREGROUND | MB_TOPMOST
        ctypes.windll.user32.MessageBoxW(0, msg, title, flags)
    except Exception:
        try:
            ctypes.windll.user32.MessageBoxW(0, msg, title, 0x00000040)
        except Exception:
            pass


def _read_ack_flag() -> int:
    """Read ack flag from ACK_FILE; return 1 if user acknowledged, else 0.
    Missing/invalid content treated as 0. Does not raise.
    """
    try:
        if not ACK_FILE.exists():
            return 0
        txt = ACK_FILE.read_text(encoding='utf-8', errors='ignore').strip()
        return 1 if txt[:1] == '1' else 0
    except Exception:
        return 0


def _write_ack_flag(val: int) -> None:
    """Write 0/1 to ACK_FILE; best-effort, ignore errors."""
    try:
        ACK_FILE.write_text('1' if val else '0', encoding='utf-8')
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


def _extract_details(data: Dict[str, Any]) -> QuotaDetails:
    """Heuristically extract name/total/used/percent/remaining from API data.
    Some benefit tokens reject direct API calls; this is a best-effort fallback.
    """
    q = QuotaDetails()

    # Name hints
    for k in ("name", "title", "token_name", "label"):
        if k in data and str(data[k]).strip():
            q.name = str(data[k]).strip()
            break

    # Try totals block first
    totals = data.get("totals") or data.get("total") or {}
    if isinstance(totals, dict):
        if "total" in totals or "total_yen" in totals:
            q.total_yen = _parse_money(totals.get("total", totals.get("total_yen")))
        if "used" in totals or "used_yen" in totals:
            q.used_yen = _parse_money(totals.get("used", totals.get("used_yen")))
        if "remaining" in totals or "remaining_yen" in totals:
            q.remaining_yen = _parse_money(totals.get("remaining", totals.get("remaining_yen")))
        if "progress" in totals or "percent" in totals:
            q.used_percent = float(_parse_money(totals.get("progress", totals.get("percent"))))

    # Top-level fallbacks
    if q.total_yen <= 0:
        for k in ("total_yen", "total", "limit", "credit_total"):
            if k in data:
                q.total_yen = _parse_money(data[k]); break
    if q.used_yen <= 0:
        for k in ("used_yen", "used", "consumed", "usage"):
            if k in data:
                q.used_yen = _parse_money(data[k]); break
    if q.remaining_yen <= 0:
        q.remaining_yen = _extract_remaining(data)

    # Percent if missing
    if q.used_percent <= 0 and q.total_yen > 0 and q.used_yen >= 0:
        q.used_percent = round((q.used_yen / q.total_yen) * 100.0, 1)

    # If total still unknown but used+remain known, compute total
    if q.total_yen <= 0 and (q.used_yen >= 0 and q.remaining_yen >= 0):
        q.total_yen = round(q.used_yen + q.remaining_yen, 2)

    return q


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


def fetch_details_api(token: str) -> QuotaDetails:
    """Best-effort extraction from API JSON for name/total/used/remaining."""
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "User-Agent": "DuckCodingQuotaWatcher/1.0",
    }
    resp = requests.get(API_URL, headers=headers, timeout=10)
    resp.raise_for_status()
    payload = resp.json()
    data = payload.get("data", payload)
    return _extract_details(data)


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


def _fetch_details_via_site(token: str) -> Optional[QuotaDetails]:
    """Use Playwright (Node) to read full quota details from the UI for a token."""
    try:
        script_path = Path(__file__).with_name("scripts") / "query_details_from_site.js"
        if not script_path.exists():
            return None
        out = subprocess.check_output(
            ["node", str(script_path), token],
            stderr=subprocess.STDOUT,
            timeout=75,
            text=True,
            cwd=str(Path(__file__).parent),
        ).strip()
        data = json.loads(out)
        q = QuotaDetails(
            name=str(data.get("name", "") or ""),
            total_yen=float(_parse_money(data.get("total_yen"))),
            used_yen=float(_parse_money(data.get("used_yen"))),
            used_percent=float(_parse_money(data.get("used_percent"))),
            remaining_yen=float(_parse_money(data.get("remaining_yen"))),
        )
        # Normalize percent if > 1 (assumes already in percentage)
        if q.used_percent > 1.0 and q.used_percent <= 100.0:
            q.used_percent = float(q.used_percent)
        return q
    except Exception as e:
        try:
            msg = getattr(e, "output", "") or str(e)
        except Exception:
            msg = str(e)
        print("[DuckCoding] UI scrape details failed:", msg)
        return None


def fetch_remaining_yen_best(token: str) -> float:
    """Prefer UI-scraped remaining (matches website) then fall back to API heuristic."""
    val = _fetch_remaining_yen_via_site(token)
    if isinstance(val, (int, float)) and val >= 0:
        return float(val)
    return fetch_remaining_yen(token)


def fetch_details_best(token: str) -> QuotaDetails:
    """Prefer Playwright UI scrape for authoritative values; fall back to API."""
    via_ui = _fetch_details_via_site(token)
    if isinstance(via_ui, QuotaDetails):
        return via_ui
    try:
        return fetch_details_api(token)
    except Exception:
        # Last resort: build from remaining-only
        r = fetch_remaining_yen_best(token)
        return QuotaDetails(remaining_yen=r)


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


def _auto_fetch_all_benefit_tokens() -> Dict[str, str]:
    """Return mapping like {'Claude Code 专用福利': 'sk-...', 'CodeX 专用福利': 'sk-...', 'Gemini CLI 专用福利': 'sk-...'}"""
    try:
        script_path = Path(__file__).with_name("scripts") / "fetch_benefit_tokens.js"
        if not script_path.exists():
            return {}
        out = subprocess.check_output(
            ["node", str(script_path)],
            stderr=subprocess.STDOUT,
            timeout=60,
            text=True,
            cwd=str(Path(__file__).parent),
        ).strip()
        data = json.loads(out)
        if isinstance(data, dict):
            # Ensure keys are strings
            return {str(k): str(v) for k, v in data.items() if isinstance(v, str)}
        return {}
    except Exception as e:
        try:
            msg = getattr(e, "output", "") or str(e)
        except Exception:
            msg = str(e)
        print("[DuckCoding] Auto-fetch all tokens failed:", msg)
        return {}


def _canonical_label(s: str) -> str:
    t = (s or "").replace(" ", "").lower()
    if "codex" in t:
        return "CodeX 专用福利"
    if "claude" in t:
        return "Claude Code 专用福利"
    if "gemini" in t:
        return "Gemini CLI 专用福利"
    return ""


def resolve_token() -> str:
    """Resolve DuckCoding token with precedence: benefits page -> fallback.
    This token is used as the primary one for alerting (CodeX 专用福利 by default).
    """
    # Try benefits page first (prefer CodeX 专用福利)
    global _BENEFIT_TOKEN_CACHE, _BENEFIT_TOKEN_CACHE_TS
    now = time.time()
    if not _BENEFIT_TOKEN_CACHE or (now - _BENEFIT_TOKEN_CACHE_TS) > _BENEFIT_TOKEN_CACHE_TTL_SEC:
        _BENEFIT_TOKEN_CACHE = _auto_fetch_all_benefit_tokens()
        _BENEFIT_TOKEN_CACHE_TS = now
    # Normalize keys possibly returned by JS
    normalized = { _canonical_label(k): v for k, v in _BENEFIT_TOKEN_CACHE.items() }
    codex = normalized.get("CodeX 专用福利")
    if codex and codex.startswith("sk-"):
        print("[DuckCoding] Using CodeX 专用福利 token from benefits page")
        return codex

    # Fallback: try single-page fetch (still from benefits page)
    auto = _auto_fetch_token_via_playwright()
    if auto:
        print("[DuckCoding] Using token auto-fetched from benefits page (CodeX 专用福利)")
        return auto

    print("[DuckCoding] Using fallback token (benefits not available)")
    return TOKEN_FALLBACK


def get_benefit_tokens() -> Dict[str, str]:
    """Get cached map of benefit tokens; refresh if cache expired. Keys normalized to canonical labels."""
    global _BENEFIT_TOKEN_CACHE, _BENEFIT_TOKEN_CACHE_TS
    now = time.time()
    if not _BENEFIT_TOKEN_CACHE or (now - _BENEFIT_TOKEN_CACHE_TS) > _BENEFIT_TOKEN_CACHE_TTL_SEC:
        _BENEFIT_TOKEN_CACHE = _auto_fetch_all_benefit_tokens()
        _BENEFIT_TOKEN_CACHE_TS = now
    out: Dict[str, str] = {}
    for k, v in (_BENEFIT_TOKEN_CACHE or {}).items():
        canonical = _canonical_label(k)
        if canonical and isinstance(v, str) and v.startswith('sk-'):
            out[canonical] = v
    return out

# Safer print that respects current console encoding and avoids mojibake
_def_print_encoding = None

def _safe_print(s: str) -> None:
    global _def_print_encoding
    try:
        if _def_print_encoding is None:
            _def_print_encoding = sys.stdout.encoding or 'utf-8'
        sys.stdout.write(s.encode(_def_print_encoding, errors='replace').decode(_def_print_encoding, errors='replace') + "\n")
        sys.stdout.flush()
    except Exception:
        try:
            print(s)
        except Exception:
            pass


def _print_cycle_header() -> None:
    # Kept for backward compatibility (no longer used directly)
    try:
        ts = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())
    except Exception:
        ts = str(time.time())
    _safe_print(f"--- 时间 {ts} ――― DuckCoding 额度 ―――")


def _quota_tag(label: str, q: QuotaDetails) -> str:
    # Tag only for CodeX 专用福利，显示基准阈值简单状态
    if label == "CodeX 专用福利":
        try:
            return f"[>¥{THRESHOLD_YEN:.0f}]" if (q.remaining_yen or 0.0) > THRESHOLD_YEN else f"[≤¥{THRESHOLD_YEN:.0f}]"
        except Exception:
            return ""
    return ""


def _print_quota_snapshot(details_map: Dict[str, QuotaDetails], order: List[str]) -> None:
    # Pretty header with current local time, to visually separate each poll
    try:
        ts = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())
    except Exception:
        ts = str(time.time())
    header = f"--- 时间 {ts} ――― DuckCoding 额度 ―――"
    _safe_print("\n" + header)

    # Alignment
    name_width = max((len(lbl) for lbl in order), default=0)
    name_width = max(10, min(name_width, 24))

    for label in order:
        q = details_map.get(label, QuotaDetails())
        used_pct_str = f"{q.used_percent:.1f}%" if q.used_percent > 0 else "—"
        tag = _quota_tag(label, q)
        tag_str = f"  {tag}" if tag else ""
        line = (
            f"  • {label:<{name_width}} | 总 ¥{q.total_yen:8.2f} | 用 ¥{q.used_yen:8.2f} ({used_pct_str:>5}) | 余 ¥{q.remaining_yen:8.2f}{tag_str}"
        )
        _safe_print(line)

    _safe_print("-" * max(40, len(header)))


def _print_details(label: str, q: QuotaDetails) -> None:
    # Backward-compat single-line printer (unused in snapshot)
    used_pct_str = f"{q.used_percent:.1f}%" if q.used_percent > 0 else "—"
    line = (
        f"[DuckCoding] {label} | 总 ¥{q.total_yen:.2f} | 用 ¥{q.used_yen:.2f} ({used_pct_str}) | 余 ¥{q.remaining_yen:.2f}"
    )
    _safe_print(line)


def main() -> None:
    print("[DuckCoding] quota watcher started. Checking every", POLL_INTERVAL_SEC, "seconds")
    notify_count = 0
    phase = 'A'  # A: 原逻辑（超过阈值弹窗）；B: 下降里程碑提醒
    prev_remaining: Optional[float] = None
    fired_thresholds: set[float] = set()
    phase_b_first_alert_done: bool = False  # 阶段B中，仅首次跨里程碑时弹窗+提示音

    # Ensure ack file exists with 0
    try:
        if not ACK_FILE.exists():
            _write_ack_flag(0)
    except Exception:
        pass

    while True:
        try:
            # Fetch benefit tokens (Claude Code / CodeX / Gemini CLI)
            tokens_map = get_benefit_tokens()
            order: List[str] = ["Claude Code 专用福利", "CodeX 专用福利", "Gemini CLI 专用福利"]

            # Collect details for all three benefits; always print one line per benefit
            details_map: Dict[str, QuotaDetails] = {lbl: QuotaDetails() for lbl in order}

            def _safe_fetch(token: str, label: str) -> QuotaDetails:
                try:
                    return fetch_details_best(token)
                except Exception as e:
                    _safe_print(f"[DuckCoding] {label} 查询失败: {e}")
                    return QuotaDetails()

            for label in order:
                tok = tokens_map.get(label)
                if tok:
                    details_map[label] = _safe_fetch(tok, label)

            # Ensure CodeX line uses a resolved token if benefits page didn't provide it
            if details_map["CodeX 专用福利"].remaining_yen <= 0 and not tokens_map.get("CodeX 专用福利"):
                token = resolve_token()
                details_map["CodeX 专用福利"] = _safe_fetch(token, "CodeX 专用福利")

            # Pretty snapshot
            _print_quota_snapshot(details_map, order)

            codex_remaining = details_map["CodeX 专用福利"].remaining_yen

            # Trailing separator (optional): keep minimal; header already segments rounds
            # _safe_print("")

            remaining = float(codex_remaining or 0.0)
            ack = _read_ack_flag()

            if phase == 'A':
                # 方式一：用户把 ack 文件写成 1，则立即切到阶段B
                if ack == 1:
                    print("[DuckCoding] Ack=1 detected -> 进入阶段B（里程碑提醒）")
                    phase = 'B'
                    fired_thresholds.clear()
                    prev_remaining = remaining
                    phase_b_first_alert_done = False
                else:
                    # 原有逻辑：只要高于阈值就提醒
                    if remaining > THRESHOLD_YEN:
                        _notify("DuckCoding 额度提醒", f"CodeX 剩余额度：¥{remaining:.2f}，超过阈值 ¥{THRESHOLD_YEN:.2f}")
                        notify_count += 1
                        # 方式二：弹窗次数超上限后，弹一次阻塞框，然后进入阶段B（不再退出）
                        if notify_count >= NOTIFY_LIMIT_BEFORE_BLOCK:
                            try:
                                ctypes.windll.user32.MessageBoxW(
                                    0,
                                    f"累计提醒已达到 {NOTIFY_LIMIT_BEFORE_BLOCK} 次，将进入阶段B（里程碑提醒）。当前余额：¥{remaining:.2f}",
                                    "DuckCoding 额度提醒",
                                    0x00000040,
                                )
                            except Exception:
                                pass
                            phase = 'B'
                            fired_thresholds.clear()
                            prev_remaining = remaining
                            phase_b_first_alert_done = False
                            notify_count = 0
            if phase == 'B':
                # 阶段B：监控向下跨越 50/20/10/5 的里程碑
                cur = remaining
                if prev_remaining is None:
                    prev_remaining = cur
                else:
                    prev = float(prev_remaining)
                    # 仅在阶段B的首次跨里程碑时弹窗+提示音，其余静默
                    for t in sorted(PHASE_B_THRESHOLDS, reverse=True):
                        if (prev > t >= cur) and (t not in fired_thresholds):
                            if not phase_b_first_alert_done:
                                _notify("DuckCoding 阶段B提醒", f"CodeX 剩余低于 ¥{t:.0f}，当前：¥{cur:.2f}")
                                phase_b_first_alert_done = True
                            # 记录该阈值已触发，避免重复判断
                            fired_thresholds.add(t)
                            break
                prev_remaining = cur

                # 余额跌至基础阈值以下后，重置并回到阶段A
                if cur < THRESHOLD_YEN:
                    print("[DuckCoding] CodeX 剩余额度已低于基础阈值，返回阶段A（重新开始超过阈值提醒）")
                    notify_count = 0
                    fired_thresholds.clear()
                    prev_remaining = None
                    phase_b_first_alert_done = False
                    _write_ack_flag(0)  # 重置交互文件为0
                    phase = 'A'
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
    parser.add_argument("--force-toast", action="store_true", help="Force using win10toast notifications (may cause console noise on some hosts)")
    args = parser.parse_args()

    # Set runtime toggle for notification backend
    FORCE_MESSAGEBOX = bool(args.force_messagebox)
    FORCE_TOAST = bool(args.force_toast)

    if args.test_notify:
        _notify("DuckCoding 测试通知", "这是声音与弹窗测试 (带提示音)")
        sys.exit(0)

    if args.once:
        try:
            tokens_map = get_benefit_tokens()
            order: List[str] = ["Claude Code 专用福利", "CodeX 专用福利", "Gemini CLI 专用福利"]

            details_map: Dict[str, QuotaDetails] = {lbl: QuotaDetails() for lbl in order}

            def _safe_fetch_once(token: str, label: str) -> QuotaDetails:
                try:
                    return fetch_details_best(token)
                except Exception as e:
                    _safe_print(f"[DuckCoding] {label} 查询失败: {e}")
                    return QuotaDetails()

            for label in order:
                tok = tokens_map.get(label)
                if tok:
                    details_map[label] = _safe_fetch_once(tok, label)

            if details_map["CodeX 专用福利"].remaining_yen <= 0 and not tokens_map.get("CodeX 专用福利"):
                token = resolve_token()
                details_map["CodeX 专用福利"] = _safe_fetch_once(token, "CodeX 专用福利")

            _print_quota_snapshot(details_map, order)

            remaining = float(details_map["CodeX 专用福利"].remaining_yen or 0.0)
            ack = _read_ack_flag()
            if ack == 0 and remaining > THRESHOLD_YEN:
                _notify("DuckCoding 额度提醒", f"CodeX 剩余额度：¥{remaining:.2f}，超过阈值 ¥{THRESHOLD_YEN:.2f}")
        except Exception as e:
            print("[DuckCoding] Error:", e)
        sys.exit(0)

    main()
