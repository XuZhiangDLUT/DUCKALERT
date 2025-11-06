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
import smtplib
import ssl
from email.mime.text import MIMEText
from email.utils import formataddr
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

# HTML history time window (keep approx 12h in dashboard regardless of interval)
HISTORY_WINDOW_SEC = 12 * 3600

# Local persistent history storage (untracked)
DATA_DIR_DEFAULT = Path(__file__).with_name('data')
HISTORY_FILE_NAME = 'quota_history.csv'
BENEFIT_SERIES_FILE_NAME = 'benefit_series.csv'

# Email defaults (can be overridden by env or CLI)
EMAIL_DEFAULT_TO = os.environ.get('ALERT_EMAIL_TO', 'zhiangxu1093@gmail.com')
EMAIL_DEFAULT_HOST = os.environ.get('SMTP_HOST', 'smtp.gmail.com')
EMAIL_DEFAULT_PORT = int(os.environ.get('SMTP_PORT', '587') or 587)
EMAIL_DEFAULT_STARTTLS = str(os.environ.get('SMTP_STARTTLS', '1')).strip() not in ('0', 'false', 'no')
EMAIL_DEFAULT_SSL = str(os.environ.get('SMTP_SSL', '0')).strip() not in ('0', 'false', 'no')
EMAIL_DEFAULT_TIMEOUT = float(os.environ.get('SMTP_TIMEOUT', '18') or 18)
EMAIL_DEFAULT_USER = os.environ.get('SMTP_USER', '')
EMAIL_DEFAULT_PASS = os.environ.get('SMTP_PASS', os.environ.get('SMTP_PASSWORD', ''))
EMAIL_DEFAULT_FROM = os.environ.get('SMTP_FROM', EMAIL_DEFAULT_USER or EMAIL_DEFAULT_TO)

# Dedup same-subject emails within TTL to avoid spamming
_EMAIL_DEDUP_TTL_SEC = 3600  # 1 hour
_LAST_EMAIL_SENT: Dict[str, float] = {}

# Runtime toggles (set by CLI)
FORCE_MESSAGEBOX = False
FORCE_TOAST = False

# Runtime email/data controls (populated from CLI/env in __main__)
EMAIL_ENABLED = False
EMAIL_DRY_RUN = False
EMAIL_CFG: Optional[EmailConfig] = None
DATA_DIR_PATH: Path = DATA_DIR_DEFAULT

# Cache for benefit tokens to avoid launching a browser every loop
_BENEFIT_TOKEN_CACHE: Dict[str, str] = {}
_BENEFIT_TOKEN_CACHE_TS: float = 0.0
_BENEFIT_TOKEN_CACHE_TTL_SEC: float = 600.0  # 10 minutes (when cache is complete)
_BENEFIT_TOKEN_CACHE_TTL_SEC_INCOMPLETE: float = 60.0  # shorter TTL when partial/missing
_BENEFIT_TOKEN_CACHE_IS_COMPLETE: bool = False
_BENEFIT_REFRESH_MAX_TRIES: int = 2

# Last known-good details per label (to mask transient UI/API failures)
_LAST_GOOD_DETAILS: Dict[str, Tuple[QuotaDetails, float]] = {}
_LAST_GOOD_TTL_SEC: float = 10 * 60  # 10 minutes

# Interactive ack file and Phase-B thresholds
ACK_FILE = Path(__file__).with_name('duckcoding_ack.txt')
PHASE_B_THRESHOLDS: List[float] = [50.0, 20.0, 10.0, 5.0, 3.0]

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

# HTML Dashboard (Plotly JS) settings
# When set via --html, the watcher will continuously write/update an HTML file
# containing a Plotly-based line chart showing three benefit balances over time.
HTML_OUTPUT_PATH: Optional[Path] = None
_MAX_HISTORY_POINTS: int = 720  # keep roughly 12 hours at 60s interval
_HISTORY_T: List[float] = []  # epoch seconds per sample
_HISTORY_SERIES: Dict[str, List[float]] = {}  # label -> series of remaining_yen


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


def _is_plausible_details(q: QuotaDetails) -> bool:
    """Heuristic validity check to filter out transient scrape/API zeros.
    Consider plausible if any of total/used/remaining is positive.
    Reject clearly inconsistent values (used/remaining >> total by large margin).
    """
    try:
        t, u, r = float(q.total_yen or 0.0), float(q.used_yen or 0.0), float(q.remaining_yen or 0.0)
        if t <= 0 and u <= 0 and r <= 0:
            return False
        # If total is known (>0), bound used/remaining relative to it
        if t > 0:
            if u < 0 or r < 0:
                return False
            # Allow small rounding drift
            if u > t * 1.2 + 1.0:
                return False
            if r > t * 1.2 + 1.0:
                return False
        return True
    except Exception:
        return False


def _remember_good(label: str, q: QuotaDetails) -> None:
    try:
        if _is_plausible_details(q):
            _LAST_GOOD_DETAILS[label] = (q, time.time())
    except Exception:
        pass


def _get_last_good_if_fresh(label: str, max_age_sec: Optional[float] = None) -> Optional[QuotaDetails]:
    try:
        if label not in _LAST_GOOD_DETAILS:
            return None
        q, ts = _LAST_GOOD_DETAILS.get(label, (None, 0.0))  # type: ignore
        if not isinstance(q, QuotaDetails):
            return None
        ttl = _LAST_GOOD_TTL_SEC if max_age_sec is None else float(max_age_sec)
        if (time.time() - float(ts)) <= ttl:
            return q
        return None
    except Exception:
        return None

# -------- Email helpers (SMTP) --------

def _load_env_file(path: Path) -> Dict[str, str]:
    """Load simple KEY=VALUE lines from a .env-style file. Returns dict; ignores comments/blank lines."""
    kv: Dict[str, str] = {}
    try:
        if not path.exists():
            return kv
        for line in path.read_text(encoding='utf-8', errors='ignore').splitlines():
            s = line.strip()
            if not s or s.startswith('#'):
                continue
            if '=' not in s:
                continue
            k, v = s.split('=', 1)
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if k:
                kv[k] = v
    except Exception:
        return kv
    return kv


def _apply_env_from_files(paths: List[Path]) -> Dict[str, str]:
    """Load env vars from a list of files; last one wins. Updates os.environ and returns merged dict."""
    merged: Dict[str, str] = {}
    for p in paths:
        cur = _load_env_file(p)
        if not cur:
            continue
        merged.update(cur)
    for k, v in merged.items():
        os.environ.setdefault(k, v)
    return merged


@dataclass
class EmailConfig:
    host: str
    port: int
    starttls: bool
    use_ssl: bool
    timeout: float
    user: str
    password: str
    from_addr: str
    to_addrs: List[str]


def _resolve_email_config(args: Any) -> Optional[EmailConfig]:
    """Resolve email config from CLI args, env, and reasonable defaults.
    Returns None if required pieces are missing (user or recipients).
    """
    # Load local env files once (no-op if already loaded)
    root = Path(__file__).parent
    _apply_env_from_files([root / '.env', root / '.env.local'])

    to_str = getattr(args, 'email_to', None) or os.environ.get('ALERT_EMAIL_TO', EMAIL_DEFAULT_TO)
    to_list = [s.strip() for s in (to_str or '').split(',') if s.strip()]

    host = getattr(args, 'smtp_host', None) or os.environ.get('SMTP_HOST', EMAIL_DEFAULT_HOST)
    try:
        port = int(getattr(args, 'smtp_port', None) or os.environ.get('SMTP_PORT', str(EMAIL_DEFAULT_PORT)))
    except Exception:
        port = EMAIL_DEFAULT_PORT

    starttls_env = (getattr(args, 'smtp_starttls', None) if hasattr(args, 'smtp_starttls') else None)
    if starttls_env is None:
        starttls_env = os.environ.get('SMTP_STARTTLS')
    starttls = EMAIL_DEFAULT_STARTTLS if starttls_env is None else (str(starttls_env).strip() not in ('0','false','no'))

    ssl_env = (getattr(args, 'smtp_ssl', None) if hasattr(args, 'smtp_ssl') else None)
    if ssl_env is None:
        ssl_env = os.environ.get('SMTP_SSL')
    use_ssl_flag = EMAIL_DEFAULT_SSL if ssl_env is None else (str(ssl_env).strip() not in ('0','false','no'))

    # Heuristic: if port == 465, default to SSL regardless of starttls flag
    use_ssl = bool(use_ssl_flag or (int(port) == 465))
    # If SSL chosen, ignore starttls
    if use_ssl:
        starttls = False

    try:
        timeout_val = float(getattr(args, 'smtp_timeout', None) or os.environ.get('SMTP_TIMEOUT', str(EMAIL_DEFAULT_TIMEOUT)))
    except Exception:
        timeout_val = EMAIL_DEFAULT_TIMEOUT

    user = getattr(args, 'smtp_user', None) or os.environ.get('SMTP_USER', EMAIL_DEFAULT_USER)
    password = getattr(args, 'smtp_pass', None) or os.environ.get('SMTP_PASS') or os.environ.get('SMTP_PASSWORD', EMAIL_DEFAULT_PASS)
    from_addr = getattr(args, 'smtp_from', None) or os.environ.get('SMTP_FROM') or (user or EMAIL_DEFAULT_FROM)

    if not to_list:
        return None
    if not user:
        # Allow anonymous send only if host allows, but we default to require user for Gmail
        return None

    return EmailConfig(
        host=str(host or 'smtp.gmail.com'),
        port=int(port or 587),
        starttls=bool(starttls),
        use_ssl=bool(use_ssl),
        timeout=float(timeout_val or EMAIL_DEFAULT_TIMEOUT),
        user=str(user),
        password=str(password or ''),
        from_addr=str(from_addr or user),
        to_addrs=to_list,
    )


def _send_email(cfg: EmailConfig, subject: str, body: str, dry_run: bool = False) -> bool:
    """Send a plain-text email. Returns True on success. Best-effort; catches exceptions.
    Auto-fallback for Gmail: if configured path times out and host is smtp.gmail.com,
    try alternate port/mode (465 SSL <-> 587 STARTTLS).
    """
    try:
        if dry_run:
            print(f"[DuckCoding][EMAIL-DRY] to={cfg.to_addrs} subj={subject} body={body[:120]}...")
            return True
        msg = MIMEText(body, _subtype='plain', _charset='utf-8')
        msg['Subject'] = subject
        msg['From'] = formataddr(('DuckCoding Alert', cfg.from_addr))
        msg['To'] = ', '.join(cfg.to_addrs)

        def _send_once(c: EmailConfig) -> bool:
            mode = 'SSL' if c.use_ssl else ('STARTTLS' if c.starttls else 'PLAIN')
            print(f"[DuckCoding][EMAIL] {c.host}:{c.port} mode={mode}")
            ctx = ssl.create_default_context()
            s = None
            try:
                if c.use_ssl:
                    s = smtplib.SMTP_SSL(c.host, int(c.port), timeout=float(c.timeout), context=ctx)
                    try:
                        s.ehlo()
                    except Exception:
                        pass
                else:
                    s = smtplib.SMTP(c.host, int(c.port), timeout=float(c.timeout))
                    try:
                        s.ehlo()
                    except Exception:
                        pass
                    if c.starttls:
                        try:
                            s.starttls(context=ctx)
                            try:
                                s.ehlo()
                            except Exception:
                                pass
                        except Exception:
                            pass
                if c.user:
                    s.login(c.user, c.password)
                res = s.sendmail(c.from_addr, c.to_addrs, msg.as_string())
                # res is a dict of refused recipients; empty dict means success
                return not bool(res)
            except Exception:
                return False
            finally:
                if s is not None:
                    try:
                        s.quit()
                    except Exception:
                        try:
                            s.close()
                        except Exception:
                            pass

        try:
            _send_once(cfg)
            return True
        except Exception as e1:
            # Auto-fallback for common dual-mode providers (Gmail/QQ): 465<->587, SSL<->STARTTLS
            host_lc = (cfg.host or '').lower().strip()
            is_dual = any(k in host_lc for k in ('gmail.com', 'googlemail.com', 'qq.com'))
            if not is_dual:
                raise
            # Build alternate config
            alt_port = (587 if cfg.use_ssl else 465)
            alt = EmailConfig(
                host=cfg.host,
                port=alt_port,
                starttls=(alt_port == 587),
                use_ssl=(alt_port == 465),
                timeout=cfg.timeout,
                user=cfg.user,
                password=cfg.password,
                from_addr=cfg.from_addr,
                to_addrs=cfg.to_addrs,
            )
            try:
                print('[DuckCoding] SMTP 重试：切换为', ('SSL:465' if alt.use_ssl else 'STARTTLS:587'))
                ok_alt = _send_once(alt)
                if ok_alt:
                    return True
                else:
                    raise RuntimeError('Alt path failed to send')
            except Exception as e2:
                # Re-raise the latest error
                raise e2
    except Exception as e:
        print('[DuckCoding] 邮件发送失败:', e)
        return False


def _email_notify(subject: str, body: str, cfg: Optional[EmailConfig], dry_run: bool = False) -> None:
    """Best-effort email notify with simple dedup by subject within TTL to avoid spamming."""
    if cfg is None:
        return
    try:
        now = time.time()
        last = _LAST_EMAIL_SENT.get(subject, 0.0)
        if (now - float(last)) < _EMAIL_DEDUP_TTL_SEC:
            return
        ok = _send_email(cfg, subject, body, dry_run=dry_run)
        if ok:
            _LAST_EMAIL_SENT[subject] = now
    except Exception:
        pass


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


def _messagebox_nonblocking(title: str, body: str, flags: Optional[int] = None) -> bool:
    """Show a persistent Windows MessageBox from a child process so main loop continues.

    Default flags: MB_ICONINFORMATION | MB_TOPMOST. Avoid MB_SYSTEMMODAL to prevent system-wide stall.
    Returns True if child process launched.
    """
    try:
        if flags is None:
            # 0x00000040: MB_ICONINFORMATION, 0x00040000: MB_TOPMOST
            flags = 0x00000040 | 0x00040000
        py = sys.executable or 'python'
        code = (
            "import ctypes,sys; "
            "ctypes.windll.user32.MessageBoxW(0, sys.argv[2], sys.argv[1], int(sys.argv[3]))"
        )
        creationflags = 0
        try:
            creationflags = 0x08000000  # CREATE_NO_WINDOW
        except Exception:
            creationflags = 0
        subprocess.Popen(
            [py, '-c', code, str(title), str(body), str(int(flags))],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd=str(Path(__file__).parent),
            creationflags=creationflags,
        )
        return True
    except Exception:
        return False


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
    """Prefer Playwright UI scrape for authoritative values; fall back to API.
    If only remaining can be obtained, return a partial QuotaDetails with remaining_yen set.
    """
    via_ui = _fetch_details_via_site(token)
    if isinstance(via_ui, QuotaDetails) and _is_plausible_details(via_ui):
        return via_ui

    # Try API next
    try:
        via_api = fetch_details_api(token)
        if _is_plausible_details(via_api):
            return via_api
    except Exception:
        via_api = None  # ignored

    # Last-resort: remaining only via site (faster) or API
    r = _fetch_remaining_yen_via_site(token)
    if isinstance(r, (int, float)):
        return QuotaDetails(remaining_yen=float(r))
    try:
        r2 = fetch_remaining_yen(token)
        return QuotaDetails(remaining_yen=float(r2))
    except Exception:
        return QuotaDetails()


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
            timeout=75,
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
    if not _BENEFIT_TOKEN_CACHE or (now - _BENEFIT_TOKEN_CACHE_TS) > (_BENEFIT_TOKEN_CACHE_TTL_SEC if _BENEFIT_TOKEN_CACHE_IS_COMPLETE else _BENEFIT_TOKEN_CACHE_TTL_SEC_INCOMPLETE):
        _BENEFIT_TOKEN_CACHE = _auto_fetch_all_benefit_tokens()
        _BENEFIT_TOKEN_CACHE_TS = now
    # Normalize keys possibly returned by JS
    normalized = { _canonical_label(k): v for k, v in _BENEFIT_TOKEN_CACHE.items() }
    # If missing CodeX, try a couple of extra refreshes immediately
    if not (normalized.get("CodeX 专用福利") or "CodeX 专用福利" in normalized):
        for _ in range(_BENEFIT_REFRESH_MAX_TRIES):
            time.sleep(1.0)
            fresh = _auto_fetch_all_benefit_tokens()
            if fresh:
                _BENEFIT_TOKEN_CACHE.update(fresh)
                normalized = { _canonical_label(k): v for k, v in _BENEFIT_TOKEN_CACHE.items() }
            if normalized.get("CodeX 专用福利"):
                break
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
    """Get cached map of benefit tokens; refresh if cache expired. Keys normalized to canonical labels.
    If the set is incomplete, perform a few immediate refresh attempts and shorten the TTL for partial cache.
    """
    global _BENEFIT_TOKEN_CACHE, _BENEFIT_TOKEN_CACHE_TS, _BENEFIT_TOKEN_CACHE_IS_COMPLETE
    now = time.time()

    # Decide TTL based on completeness of previous cache
    ttl = _BENEFIT_TOKEN_CACHE_TTL_SEC if _BENEFIT_TOKEN_CACHE_IS_COMPLETE else _BENEFIT_TOKEN_CACHE_TTL_SEC_INCOMPLETE

    if not _BENEFIT_TOKEN_CACHE or (now - _BENEFIT_TOKEN_CACHE_TS) > ttl:
        _BENEFIT_TOKEN_CACHE = _auto_fetch_all_benefit_tokens()
        _BENEFIT_TOKEN_CACHE_TS = now

    def _normalize(raw: Dict[str, str]) -> Dict[str, str]:
        out: Dict[str, str] = {}
        for k, v in (raw or {}).items():
            canonical = _canonical_label(k)
            if canonical and isinstance(v, str) and v.startswith('sk-'):
                out[canonical] = v
        return out

    normalized = _normalize(_BENEFIT_TOKEN_CACHE)

    # If incomplete, try a couple of immediate refresh attempts
    needed = {"Claude Code 专用福利", "CodeX 专用福利", "Gemini CLI 专用福利"}
    if not needed.issubset(set(normalized.keys())):
        for _ in range(_BENEFIT_REFRESH_MAX_TRIES):
            time.sleep(1.0)
            fresh = _auto_fetch_all_benefit_tokens()
            if fresh:
                # Merge (prefer fresh)
                _BENEFIT_TOKEN_CACHE.update(fresh)
                normalized = _normalize(_BENEFIT_TOKEN_CACHE)
            if needed.issubset(set(normalized.keys())):
                break

    # Mark completeness and return
    _BENEFIT_TOKEN_CACHE_IS_COMPLETE = needed.issubset(set(normalized.keys()))
    return normalized

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


def _quota_tag(label: str, q: QuotaDetails, stale: bool = False, missing: bool = False) -> str:
    # Tag only for CodeX 专用福利，显示基准阈值简单状态 + 附加状态（缓存/缺失）
    if label != "CodeX 专用福利":
        return ""
    parts: List[str] = []
    try:
        parts.append((f">¥{THRESHOLD_YEN:.0f}") if ((q.remaining_yen or 0.0) > THRESHOLD_YEN) else (f"≤¥{THRESHOLD_YEN:.0f}"))
    except Exception:
        pass
    if stale:
        parts.append("缓存")
    elif missing:
        parts.append("缺失")
    if not parts:
        return ""
    return "[" + ",".join(parts) + "]"


def _print_quota_snapshot(details_map: Dict[str, QuotaDetails], order: List[str], stale: Optional[Dict[str, bool]] = None, missing: Optional[Dict[str, bool]] = None) -> None:
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

    stale = stale or {}
    missing = missing or {}

    for label in order:
        q = details_map.get(label, QuotaDetails())
        used_pct_str = f"{q.used_percent:.1f}%" if q.used_percent > 0 else "—"
        tag = _quota_tag(label, q, stale=bool(stale.get(label)), missing=bool(missing.get(label)))
        tag_str = f"  {tag}" if tag else ""
        line = (
            f"  • {label:<{name_width}} | 总 ¥{q.total_yen:8.2f} | 用 ¥{q.used_yen:8.2f} ({used_pct_str:>5}) | 余 ¥{q.remaining_yen:8.2f}{tag_str}"
        )
        _safe_print(line)

    _safe_print("-" * max(40, len(header)))


# ---------- HTML dashboard (Plotly JS) ----------

def _ensure_history_keys(order: List[str]) -> None:
    for label in order:
        if label not in _HISTORY_SERIES:
            _HISTORY_SERIES[label] = []


def _append_history(order: List[str], details_map: Dict[str, QuotaDetails]) -> None:
    """Append current snapshot into in-memory history and keep arrays aligned/trimmed (12h window)."""
    _ensure_history_keys(order)
    now = time.time()
    _HISTORY_T.append(now)
    for label in order:
        q = details_map.get(label, QuotaDetails())
        try:
            val = float(q.remaining_yen or 0.0)
        except Exception:
            val = 0.0
        _HISTORY_SERIES[label].append(val)

    # Trim by time window (12h)
    cutoff = now - float(HISTORY_WINDOW_SEC)
    trim_idx = 0
    for i, t in enumerate(_HISTORY_T):
        try:
            if float(t) >= cutoff:
                trim_idx = i
                break
        except Exception:
            continue
    else:
        trim_idx = len(_HISTORY_T)

    if trim_idx > 0:
        del _HISTORY_T[:trim_idx]
        for label in list(_HISTORY_SERIES.keys()):
            if len(_HISTORY_SERIES[label]) >= trim_idx:
                del _HISTORY_SERIES[label][:trim_idx]
            else:
                _HISTORY_SERIES[label].clear()

    # Additional cap to keep memory bounded
    if len(_HISTORY_T) > _MAX_HISTORY_POINTS:
        drop = len(_HISTORY_T) - _MAX_HISTORY_POINTS
        del _HISTORY_T[:drop]
        for label in list(_HISTORY_SERIES.keys()):
            if len(_HISTORY_SERIES[label]) >= drop:
                del _HISTORY_SERIES[label][:drop]
            else:
                _HISTORY_SERIES[label].clear()


def _render_plot_html(order: List[str]) -> str:
    """Return a standalone HTML string with a Plotly line chart.
    Uses Plotly JS from a public CDN so no extra Python deps are required.
    """
    ts_ms = [int(t * 1000) for t in _HISTORY_T]
    series_js = {}
    for label in order:
        series_js[label] = _HISTORY_SERIES.get(label, [])

    # Use JSON dumps for safe embedding
    ts_json = json.dumps(ts_ms, ensure_ascii=False)
    data_json = json.dumps(series_js, ensure_ascii=False)

    last_ts = 0
    try:
        last_ts = int(_HISTORY_T[-1]) if _HISTORY_T else 0
    except Exception:
        last_ts = 0

    html = f"""
<!doctype html>
<html lang=\"zh-CN\">
<head>
  <meta charset=\"utf-8\">
  <meta http-equiv=\"X-UA-Compatible\" content=\"IE=edge\">
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">
  <title>DuckCoding 福利余额（实时）</title>
  <script src=\"https://cdn.plot.ly/plotly-2.29.1.min.js\"></script>
  <style>
    html, body {{ margin:0; padding:0; height:100%; font-family: -apple-system, Segoe UI, Roboto, Helvetica, Arial, \"Microsoft Yahei\", sans-serif; }}
    #wrap {{ display:flex; flex-direction:column; height:100%; }}
    header {{ padding:8px 12px; border-bottom:1px solid #eee; background:#fafafa; }}
    #chart {{ flex:1 1 auto; }}
    .muted {{ color:#666; font-size:12px; }}
  </style>
</head>
<body>
  <div id=\"wrap\">
    <header>
      <div><strong>DuckCoding 福利余额（实时）</strong></div>
      <div class=\"muted\">建议配合 VS Code Live Preview/Live Server 打开本文件，保存或脚本写入时会自动刷新</div>
      <div id=\"last\" class=\"muted\"></div>
    </header>
    <div id=\"chart\"></div>
  </div>

  <script>
    const ts = {ts_json};
    const seriesMap = {data_json};

    function fmtTs(s) {{
      if (!s) return '—';
      const d = new Date(s*1000);
      const pad = (n) => String(n).padStart(2,'0');
      return `${{d.getFullYear()}}-${{pad(d.getMonth()+1)}}-${{pad(d.getDate())}} ${{pad(d.getHours())}}:${{pad(d.getMinutes())}}:${{pad(d.getSeconds())}}`;
    }}

    function buildTraces() {{
      const names = ['Claude Code 专用福利', 'CodeX 专用福利', 'Gemini CLI 专用福利'];
      const colors = {{
        'Claude Code 专用福利': '#1f77b4',
        'CodeX 专用福利': '#d62728',
        'Gemini CLI 专用福利': '#2ca02c'
      }};
      const traces = [];
      for (const name of names) {{
        const y = seriesMap[name] || [];
        traces.push({{
          name,
          x: ts.map(t => new Date(t)),
          y,
          mode: 'lines+markers',
          line: {{ width: 2, color: colors[name] || undefined }},
          marker: {{ size: 4 }}
        }});
      }}
      return traces;
    }}

    function render() {{
      const traces = buildTraces();
      const layout = {{
        margin: {{ t: 40, r: 20, b: 40, l: 50 }},
        legend: {{ orientation: 'h', y: -0.2 }},
        xaxis: {{ title: '时间' }},
        yaxis: {{ title: '余额 (¥)' }},
        title: 'DuckCoding 福利余额（实时）'
      }};
      Plotly.newPlot('chart', traces, layout, {{ responsive: true, displaylogo: false }});
      const lastTs = ts.length ? Math.floor(ts[ts.length-1]/1000) : 0;
      const lastDiv = document.getElementById('last');
      lastDiv.textContent = '最后更新时间：' + fmtTs(lastTs);
    }}

    render();
  </script>
</body>
</html>
"""
    return html


def _write_html_atomic(path: Path, content: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(content, encoding='utf-8')
        os.replace(str(tmp), str(path))
    finally:
        try:
            if tmp.exists():
                tmp.unlink(missing_ok=True)  # type: ignore[arg-type]
        except Exception:
            pass


def _update_html_dashboard(order: List[str]) -> None:
    if not HTML_OUTPUT_PATH:
        return
    try:
        html = _render_plot_html(order)
        _write_html_atomic(Path(HTML_OUTPUT_PATH), html)
    except Exception as e:
        print("[DuckCoding] HTML 写入失败:", e)


def _persist_snapshot_csv(data_dir: Path, order: List[str], details_map: Dict[str, QuotaDetails], ts: Optional[float] = None) -> Path:
    """Append a CSV row with full snapshot for all benefits into data_dir/quota_history.csv.
    Keeps all history (no trimming). File is created with header if missing.
    """
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    path = data_dir / HISTORY_FILE_NAME
    if ts is None:
        ts = time.time()
    try:
        ts_iso = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(float(ts)))
    except Exception:
        ts_iso = str(ts)

    # Build row in fixed order: timestamp columns then for each label: total, used, percent, remaining
    cols: List[str] = [ts_iso, f"{float(ts):.3f}"]
    for label in order:
        q = details_map.get(label, QuotaDetails())
        cols.extend([
            f"{float(q.total_yen or 0.0):.2f}",
            f"{float(q.used_yen or 0.0):.2f}",
            f"{float(q.used_percent or 0.0):.2f}",
            f"{float(q.remaining_yen or 0.0):.2f}",
        ])

    line = ",".join(cols) + "\n"

    if not path.exists():
        # Write header
        header = ["ts_iso", "ts_epoch"]
        for label in order:
            base = label
            header.extend([f"{base}_total", f"{base}_used", f"{base}_used_percent", f"{base}_remaining"])
        try:
            path.write_text(",".join(header) + "\n" + line, encoding='utf-8')
        except Exception:
            pass
        return path

    try:
        with path.open('a', encoding='utf-8', newline='') as f:
            f.write(line)
    except Exception:
        pass
    return path


def _curve_id_for_label(label: str) -> int:
    if label == 'Claude Code 专用福利':
        return 1
    if label == 'CodeX 专用福利':
        return 2
    if label == 'Gemini CLI 专用福利':
        return 3
    return 0


def _persist_benefit_series_csv(data_dir: Path, order: List[str], details_map: Dict[str, QuotaDetails], stale: Dict[str, bool] | None = None, missing: Dict[str, bool] | None = None, ts: Optional[float] = None) -> Path:
    """Append 3 rows (one per benefit) into data_dir/benefit_series.csv.
    Columns: year,month,day,hour,minute,second,curve_id,value,is_cached
    - value = remaining_yen
    - is_cached = 1 if value is taken from last-good cache (stale), else 0
    File is created with header if missing. Never cleared on restart.
    """
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    path = data_dir / BENEFIT_SERIES_FILE_NAME

    if ts is None:
        ts = time.time()
    try:
        lt = time.localtime(float(ts))
        y, m, d, hh, mm, ss = lt.tm_year, lt.tm_mon, lt.tm_mday, lt.tm_hour, lt.tm_min, lt.tm_sec
    except Exception:
        # Fallback: derive from epoch if localtime fails
        _ts = int(ts or time.time())
        y, m, d, hh, mm, ss = 1970, 1, 1, 0, 0, 0
        try:
            lt = time.localtime(_ts)
            y, m, d, hh, mm, ss = lt.tm_year, lt.tm_mon, lt.tm_mday, lt.tm_hour, lt.tm_min, lt.tm_sec
        except Exception:
            pass

    header = 'year,month,day,hour,minute,second,curve_id,value,is_cached,is_missing\n'

    try:
        # Header migration: if file exists and header lacks is_missing, upgrade header and append ,0 for old lines
        if path.exists():
            try:
                with path.open('r', encoding='utf-8', errors='ignore') as rf:
                    first = rf.readline()
                if first and 'is_missing' not in first:
                    tmp = path.with_suffix(path.suffix + '.tmp')
                    with path.open('r', encoding='utf-8', errors='ignore') as rf, tmp.open('w', encoding='utf-8', newline='') as wf:
                        old_header = rf.readline().strip()
                        if old_header:
                            wf.write(old_header + ',is_missing\n')
                        else:
                            wf.write(header)
                        for line in rf:
                            line = line.rstrip('\n').rstrip('\r')
                            if line:
                                wf.write(line + ',0\n')
                    os.replace(str(tmp), str(path))
            except Exception:
                pass

        new_file = not path.exists()
        with path.open('a', encoding='utf-8', newline='') as f:
            if new_file:
                f.write(header)
            for label in order:
                curve = _curve_id_for_label(label)
                q = details_map.get(label, QuotaDetails())
                try:
                    val = float(q.remaining_yen or 0.0)
                except Exception:
                    val = 0.0
                is_cached = 1 if (stale or {}).get(label) else 0
                is_missing = 1 if (missing or {}).get(label) else 0
                f.write(f"{y},{m},{d},{hh},{mm},{ss},{curve},{val:.2f},{is_cached},{is_missing}\n")
    except Exception:
        pass
    return path


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
    phase_a_email_sent: bool = False        # 阶段A中，仅发送一次邮件（直到进入B并回到A后才可再次发送）

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
            stale_map: Dict[str, bool] = {lbl: False for lbl in order}
            missing_map: Dict[str, bool] = {lbl: False for lbl in order}

            def _safe_fetch(token: str, label: str) -> QuotaDetails:
                try:
                    return fetch_details_best(token)
                except Exception as e:
                    _safe_print(f"[DuckCoding] {label} 查询失败: {e}")
                    return QuotaDetails()

            # Fetch with plausibility checks + last-good fallback
            for label in order:
                tok = tokens_map.get(label)
                if not tok:
                    missing_map[label] = True
                    continue
                q = _safe_fetch(tok, label)
                if not _is_plausible_details(q):
                    # Try fast remaining-only UI path to at least fill remaining
                    r_try = _fetch_remaining_yen_via_site(tok)
                    if isinstance(r_try, (int, float)):
                        try:
                            q.remaining_yen = float(r_try)
                        except Exception:
                            pass
                if _is_plausible_details(q):
                    details_map[label] = q
                    _remember_good(label, q)
                else:
                    last = _get_last_good_if_fresh(label)
                    if isinstance(last, QuotaDetails):
                        details_map[label] = last
                        stale_map[label] = True
                    else:
                        details_map[label] = q  # keep zeros
                        missing_map[label] = True

            # If some labels missing entirely, try refreshing benefits once and re-try those labels
            missing_labels = {lbl for lbl, is_missing in missing_map.items() if is_missing}
            if missing_labels:
                fresh_map = get_benefit_tokens()
                for label in list(missing_labels):
                    if fresh_map.get(label):
                        tok = fresh_map.get(label)
                        q2 = _safe_fetch(tok, label)
                        if _is_plausible_details(q2):
                            details_map[label] = q2
                            stale_map[label] = False
                            missing_map[label] = False
                            _remember_good(label, q2)

            # Ensure CodeX line uses a resolved token if benefits page didn't provide it
            if missing_map.get("CodeX 专用福利") and not tokens_map.get("CodeX 专用福利"):
                token = resolve_token()
                qx = _safe_fetch(token, "CodeX 专用福利")
                if _is_plausible_details(qx):
                    details_map["CodeX 专用福利"] = qx
                    stale_map["CodeX 专用福利"] = False
                    missing_map["CodeX 专用福利"] = False
                    _remember_good("CodeX 专用福利", qx)

            # Pretty snapshot
            _print_quota_snapshot(details_map, order, stale=stale_map, missing=missing_map)

            # Persist full snapshot to CSV (unbounded history)
            try:
                _persist_snapshot_csv(DATA_DIR_PATH, order, details_map)
                _persist_benefit_series_csv(DATA_DIR_PATH, order, details_map, stale=stale_map, missing=missing_map)
            except Exception as e:
                print("[DuckCoding] 历史持久化失败:", e)

            # Update HTML dashboard (if enabled)
            if HTML_OUTPUT_PATH:
                try:
                    _append_history(order, details_map)
                    _update_html_dashboard(order)
                except Exception as e:
                    print("[DuckCoding] HTML 写入失败:", e)

            codex_remaining = details_map["CodeX 专用福利"].remaining_yen

            # Trailing separator (optional): keep minimal; header already segments rounds
            # _safe_print("")

            remaining = float(codex_remaining or 0.0)
            ack = _read_ack_flag()

            # 如果本轮 CodeX 数据缺失（没有可信新数据、也无缓存可用），则跳过判定，避免状态抖动
            decision_ok = _is_plausible_details(details_map.get("CodeX 专用福利", QuotaDetails())) or bool(stale_map.get("CodeX 专用福利"))
            if not decision_ok:
                print("[DuckCoding] 跳过本轮判定：CodeX 数据缺失/未加载（保持上次状态）")
                continue

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
                        try:
                            if EMAIL_ENABLED and (not phase_a_email_sent):
                                _email_notify(
                                    "DuckCoding 额度提醒",
                                    f"CodeX 剩余额度：¥{remaining:.2f}，超过阈值 ¥{THRESHOLD_YEN:.2f}",
                                    EMAIL_CFG,
                                    dry_run=bool(EMAIL_DRY_RUN),
                                )
                                phase_a_email_sent = True
                        except Exception:
                            pass
                        notify_count += 1
                        # 方式二：弹窗次数超上限后，弹一次阻塞框，然后进入阶段B（不再退出）
                        if notify_count >= NOTIFY_LIMIT_BEFORE_BLOCK:
                            # Show a persistent dialog without blocking the main loop
                            _messagebox_nonblocking(
                                "DuckCoding 额度提醒",
                                f"累计提醒已达到 {NOTIFY_LIMIT_BEFORE_BLOCK} 次，将进入阶段B（里程碑提醒）。当前余额：¥{remaining:.2f}",
                                # 信息图标 + 置顶，不使用 SYSTEMMODAL 以避免系统级阻塞
                                flags=(0x00000040 | 0x00040000),
                            )
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
                                # 阶段B不发送邮件；仅在阶段A中一轮只发一次邮件
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
                    phase_a_email_sent = False  # 新一轮阶段A允许再次发一封邮件
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
    parser.add_argument("--html", type=str, help="Write/update a Plotly HTML dashboard at this path (open with VS Code Live Preview/Live Server)")
    # Email + data options
    parser.add_argument("--email", action="store_true", help="Enable email notifications via SMTP (see --smtp-* and --email-to)")
    parser.add_argument("--email-dry-run", action="store_true", help="Do not actually send; print an EMAIL-DRY log instead")
    parser.add_argument("--email-test", action="store_true", help="Send a one-off test email (requires --email and SMTP config)")
    parser.add_argument("--email-to", type=str, help="Recipient email(s), comma-separated (default env ALERT_EMAIL_TO or zhiangxu1093@gmail.com)")
    parser.add_argument("--smtp-user", type=str, help="SMTP username (e.g., your Gmail address)")
    parser.add_argument("--smtp-pass", type=str, help="SMTP password or App Password")
    parser.add_argument("--smtp-host", type=str, help="SMTP host (default smtp.gmail.com)")
    parser.add_argument("--smtp-port", type=str, help="SMTP port (default 587 or 465 for SSL)")
    parser.add_argument("--smtp-starttls", type=str, help="Use STARTTLS (1/0; default 1 for port 587)")
    parser.add_argument("--smtp-ssl", type=str, help="Use SSL/TLS (1/0; default 1 for port 465)")
    parser.add_argument("--smtp-timeout", type=str, help="SMTP timeout seconds (default 18)")
    parser.add_argument("--smtp-from", type=str, help="From address (default SMTP user)")
    parser.add_argument("--data-dir", type=str, help="Directory to store persistent history CSV (default: ./data)")

    args = parser.parse_args()

    # Set runtime toggle for notification backend
    FORCE_MESSAGEBOX = bool(args.force_messagebox)
    FORCE_TOAST = bool(args.force_toast)

    # Enable HTML dashboard if requested
    if getattr(args, 'html', None):
        try:
            HTML_OUTPUT_PATH = Path(args.html).resolve()
            print(f"[DuckCoding] HTML dashboard enabled: {HTML_OUTPUT_PATH}")
        except Exception:
            HTML_OUTPUT_PATH = Path(args.html)
            print(f"[DuckCoding] HTML dashboard enabled: {HTML_OUTPUT_PATH}")

    # Configure data dir
    try:
        dd = getattr(args, 'data_dir', None) or os.environ.get('DUCKCODING_DATA_DIR')
        DATA_DIR_PATH = Path(dd).resolve() if dd else DATA_DIR_DEFAULT
        print(f"[DuckCoding] Data dir: {DATA_DIR_PATH}")
    except Exception:
        DATA_DIR_PATH = DATA_DIR_DEFAULT
        print(f"[DuckCoding] Data dir: {DATA_DIR_PATH}")

    # Configure email
    EMAIL_ENABLED = bool(getattr(args, 'email', False))
    EMAIL_DRY_RUN = bool(getattr(args, 'email_dry_run', False))
    if EMAIL_ENABLED:
        try:
            EMAIL_CFG = _resolve_email_config(args)
            if EMAIL_CFG is None:
                print("[DuckCoding] Email enabled but config incomplete; will skip sending")
        except Exception as e:
            EMAIL_CFG = None
            print("[DuckCoding] Email config error:", e)

    # One-off email test
    if EMAIL_ENABLED and bool(getattr(args, 'email_test', False)):
        subj = "DuckCoding 测试邮件"
        body = "这是一封来自 DuckCoding 额度监控的测试邮件。"
        if EMAIL_CFG is None:
            print("[DuckCoding] 邮件测试失败：配置不完整")
            sys.exit(2)
        ok = _send_email(EMAIL_CFG, subj, body, dry_run=bool(EMAIL_DRY_RUN))
        print("[DuckCoding] 邮件测试", "成功" if ok else "失败")
        sys.exit(0)

    if args.test_notify:
        _notify("DuckCoding 测试通知", "这是声音与弹窗测试 (带提示音)")
        sys.exit(0)

    if args.once:
        try:
            tokens_map = get_benefit_tokens()
            order: List[str] = ["Claude Code 专用福利", "CodeX 专用福利", "Gemini CLI 专用福利"]

            details_map: Dict[str, QuotaDetails] = {lbl: QuotaDetails() for lbl in order}
            stale_map: Dict[str, bool] = {lbl: False for lbl in order}
            missing_map: Dict[str, bool] = {lbl: False for lbl in order}

            def _safe_fetch_once(token: str, label: str) -> QuotaDetails:
                try:
                    return fetch_details_best(token)
                except Exception as e:
                    _safe_print(f"[DuckCoding] {label} 查询失败: {e}")
                    return QuotaDetails()

            for label in order:
                tok = tokens_map.get(label)
                if not tok:
                    missing_map[label] = True
                    continue
                q = _safe_fetch_once(tok, label)
                if not _is_plausible_details(q):
                    r_try = _fetch_remaining_yen_via_site(tok)
                    if isinstance(r_try, (int, float)):
                        try:
                            q.remaining_yen = float(r_try)
                        except Exception:
                            pass
                if _is_plausible_details(q):
                    details_map[label] = q
                    _remember_good(label, q)
                else:
                    last = _get_last_good_if_fresh(label)
                    if isinstance(last, QuotaDetails):
                        details_map[label] = last
                        stale_map[label] = True
                    else:
                        details_map[label] = q
                        missing_map[label] = True

            if missing_map.get("CodeX 专用福利") and not tokens_map.get("CodeX 专用福利"):
                token = resolve_token()
                qx = _safe_fetch_once(token, "CodeX 专用福利")
                if _is_plausible_details(qx):
                    details_map["CodeX 专用福利"] = qx
                    stale_map["CodeX 专用福利"] = False
                    missing_map["CodeX 专用福利"] = False
                    _remember_good("CodeX 专用福利", qx)

            _print_quota_snapshot(details_map, order, stale=stale_map, missing=missing_map)

            # Persist full snapshot to CSV
            try:
                _persist_snapshot_csv(DATA_DIR_PATH, order, details_map)
                _persist_benefit_series_csv(DATA_DIR_PATH, order, details_map, stale=stale_map, missing=missing_map)
            except Exception as e:
                print("[DuckCoding] 历史持久化失败:", e)

            # Update HTML once if enabled
            if HTML_OUTPUT_PATH:
                try:
                    _append_history(order, details_map)
                    _update_html_dashboard(order)
                except Exception as e:
                    print("[DuckCoding] HTML 写入失败:", e)

            remaining = float(details_map["CodeX 专用福利"].remaining_yen or 0.0)
            ack = _read_ack_flag()
            decision_ok = _is_plausible_details(details_map.get("CodeX 专用福利", QuotaDetails())) or bool(stale_map.get("CodeX 专用福利"))
            if decision_ok and ack == 0 and remaining > THRESHOLD_YEN:
                _notify("DuckCoding 额度提醒", f"CodeX 剩余额度：¥{remaining:.2f}，超过阈值 ¥{THRESHOLD_YEN:.2f}")
                try:
                    if EMAIL_ENABLED:
                        _email_notify(
                            "DuckCoding 额度提醒",
                            f"CodeX 剩余额度：¥{remaining:.2f}，超过阈值 ¥{THRESHOLD_YEN:.2f}",
                            EMAIL_CFG,
                            dry_run=bool(EMAIL_DRY_RUN),
                        )
                except Exception:
                    pass
        except Exception as e:
            print("[DuckCoding] Error:", e)
        sys.exit(0)

    main()
