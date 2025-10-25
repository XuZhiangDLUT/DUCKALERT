# -*- coding: utf-8 -*-
"""
DuckCoding status watcher
- Polls https://status.duckcoding.com/status/duckcoding every N seconds (default 300s)
- Extracts all services' 24h availability via Playwright helper (Node)
- Prints current 24h availability per service
- For watched services (by name), fires non-blocking notifications on threshold crossings:
  * downward thresholds (default: 70, 60, 50, 30, 10)
  * upward thresholds (default: 80)

Notes:
- This script is independent from duckcoding_quota_watcher.py (no shared imports).
- Notifications prefer win10toast (non-blocking). Fallback: console + beep (non-blocking).

CLI examples:
  python duckcoding_status_watcher.py --once --watch "日本线路（CodeX）" --watch "日本线路（Claude Code）"
  python duckcoding_status_watcher.py --interval 300 --down 70 60 50 30 10 --up 80 --force-messagebox
"""
from __future__ import annotations
import json
import re
import time
import ctypes
from pathlib import Path
from typing import Dict, List, Tuple, Optional
import subprocess
import sys
import warnings

# Try to enforce UTF-8 console
try:
    sys.stdout.reconfigure(encoding='utf-8')  # type: ignore[attr-defined]
    sys.stderr.reconfigure(encoding='utf-8')  # type: ignore[attr-defined]
except Exception:
    pass

# Optional toast
_toaster = None
FORCE_MESSAGEBOX = False  # optional testing
USE_TOAST = False  # default off to avoid WNDPROC/WPARAM warnings on some systems
try:
    warnings.filterwarnings('ignore', message='pkg_resources is deprecated', category=UserWarning)
    from win10toast import ToastNotifier  # type: ignore
    _toaster = ToastNotifier()
except Exception:
    _toaster = None

# In-memory last-good cache to mask transient page/API noise
_LAST_GOOD_SERVICES: Dict[str, Tuple[float, float]] = {}
_LAST_GOOD_TTL_SEC: float = 10 * 60  # 10 minutes

# Config
STATUS_URL = "https://status.duckcoding.com/status/duckcoding"
POLL_INTERVAL_SEC = 300  # 5 minutes
DOWN_THRESHOLDS_DEFAULT = [70.0, 60.0, 50.0, 30.0, 10.0]
UP_THRESHOLDS_DEFAULT = [80.0]
WATCH_DEFAULT = ["日本线路（CodeX）", "日本线路（Claude Code）"]

# Node fetch retry/backoff
STATUS_NODE_TIMEOUT_SEC = 75  # single attempt timeout (was 60)
STATUS_FETCH_RETRIES = 2      # extra attempts after the first
STATUS_FETCH_RETRY_DELAY_SEC = 2  # base delay; backoff = delay * (attempt_index+1)

# Paths
ROOT = Path(__file__).parent
NODE_SCRIPT = ROOT / "scripts" / "fetch_status_services.js"
STATE_FILE = ROOT / "status_watcher_state.json"

_percent_re = re.compile(r"(\d+(?:\.\d+)?)")


def _is_plausible_percent(p: float) -> bool:
    try:
        p = float(p)
        return (0.0 <= p <= 100.0)
    except Exception:
        return False


def _remember_good_pct(name: str, p: float) -> None:
    try:
        if _is_plausible_percent(p):
            _LAST_GOOD_SERVICES[name] = (float(p), time.time())
    except Exception:
        pass


def _get_last_good_pct(name: str, max_age_sec: float | None = None) -> Optional[float]:
    try:
        if name not in _LAST_GOOD_SERVICES:
            return None
        val, ts = _LAST_GOOD_SERVICES.get(name, (None, 0.0))  # type: ignore
        ttl = _LAST_GOOD_TTL_SEC if max_age_sec is None else float(max_age_sec)
        if val is not None and (time.time() - float(ts)) <= ttl and _is_plausible_percent(val):
            return float(val)
        return None
    except Exception:
        return None


def _beep() -> None:
    try:
        ctypes.windll.kernel32.Beep(1200, 200)
    except Exception:
        pass


def _notify(title: str, msg: str) -> None:
    # Default: console + beep (non-blocking), to avoid win10toast WNDPROC/WPARAM warnings
    if not USE_TOAST or _toaster is None or FORCE_MESSAGEBOX:
        print(f"[StatusWatcher][NOTIFY] {title}: {msg}")
        _beep()
        return
    # Optional toast path (non-blocking). If errors emerge, silently fall back.
    try:
        _toaster.show_toast(title, msg, duration=5, threaded=True)
        _beep()
        # Brief wait so toast can schedule without blocking
        for _ in range(10):
            if not _toaster.notification_active():
                break
            time.sleep(0.05)
    except Exception:
        print(f"[StatusWatcher][NOTIFY] {title}: {msg}")
        _beep()


def _run_node_fetch() -> List[Dict[str, float]]:
    if not NODE_SCRIPT.exists():
        raise RuntimeError(f"Node script not found: {NODE_SCRIPT}")

    last_err = None
    for attempt in range(1 + int(STATUS_FETCH_RETRIES)):
        try:
            out = subprocess.check_output(
                ["node", str(NODE_SCRIPT)],
                text=True,
                encoding='utf-8',
                errors='ignore',
                stderr=subprocess.STDOUT,
                timeout=int(STATUS_NODE_TIMEOUT_SEC),
                cwd=str(ROOT),
            )
            try:
                data = json.loads(out)
                if isinstance(data, list):
                    return data  # [{name, percent_24h}]
                else:
                    raise RuntimeError("Node returned non-list JSON")
            except Exception as e:
                raise RuntimeError(f"Invalid JSON from Node: {e}\nRaw: {out[:200]}...")
        except Exception as e:
            last_err = e
            if attempt < int(STATUS_FETCH_RETRIES):
                # simple linear backoff
                try:
                    time.sleep(max(0.5, float(STATUS_FETCH_RETRY_DELAY_SEC) * (attempt + 1)))
                except Exception:
                    pass
                continue
            else:
                break
    raise RuntimeError(f"Node fetch failed after retries: {last_err}")


def _normalize_services(raw: List[Dict[str, float]]) -> Dict[str, float]:
    """Normalize noisy Node output into { service_name: percent_24h }.
    - Prefer names without '%' and without 'ago/now' artifacts
    - Prefer shorter clean names when duplicates exist
    """
    buckets: Dict[str, List[Tuple[str, float]]] = {}
    for item in raw:
        name = str(item.get("name", "")).strip()
        pct = float(item.get("percent_24h", 0.0))
        if not name:
            continue
        if pct < 0 or pct > 1000:
            continue
        # Skip very long garbage lines
        if len(name) > 160:
            continue
        # Build a simplified name variant for grouping
        key = name
        # Remove leading percent text like "98.21%..."
        key = re.sub(r"^\s*\d+(?:\.\d+)?%\s*", "", key)
        # Remove timing markers and tokens like '3h', '5m'
        key = key.replace("now", "").replace("ago", "")
        key = re.sub(r"\d+\s*[hm](?:\s*ago)?", "", key, flags=re.I)
        key = re.sub(r"\s+", " ", key).strip()
        # If name still contains '%', it's likely a noisy chunk
        if '%' in key:
            continue
        # Must look like a service name
        if not re.search(r"线路|号池|\bCLI\b|Claude|CodeX|Sonnet|Opus|CC\s*2api|（|）", key, flags=re.I):
            continue
        buckets.setdefault(key, []).append((name, pct))

    # Choose the shortest variant per bucket and take the max percent seen
    result: Dict[str, float] = {}
    for key, variants in buckets.items():
        if not variants:
            continue
        exacts = [(n, p) for (n, p) in variants if n.strip() == key]
        if exacts:
            # Prefer exact-name matches; conservative pick: min percent among exacts
            picked = min((p for _, p in exacts))
            result[key] = float(picked)
            continue
        # Otherwise pick the min percent among variants (avoid global/garbage 94.5% lines)
        picked = min(p for _, p in variants)
        result[key] = float(picked)
    return result


def _load_state_raw() -> dict:
    try:
        if STATE_FILE.exists():
            return json.loads(STATE_FILE.read_text(encoding='utf-8'))
    except Exception:
        pass
    return {}


def _save_state(state: dict) -> None:
    try:
        STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
    except Exception:
        pass


def _severity_tag(pct: float, down: Optional[List[float]] = None, up: Optional[List[float]] = None) -> str:
    try:
        if down:
            for t in sorted(set(float(x) for x in down)):
                if pct < t:
                    return f"↓<{int(t)}%"
        if up:
            hi = max(float(x) for x in up)
            if pct >= hi:
                return f"↑≥{int(hi)}%"
    except Exception:
        pass
    return ""


def _print_snapshot(services: Dict[str, float], watch: Optional[List[str]] = None, down: Optional[List[float]] = None, up: Optional[List[float]] = None, only_watch: bool = False, stale: Optional[Dict[str, bool]] = None, missing: Optional[Dict[str, bool]] = None) -> None:
    # Pretty header with current local time, to visually separate each poll
    ts = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())
    header = f"--- 时间 {ts} ――― DuckCoding 状态 ―――"
    print("\n" + header)

    stale = stale or {}
    missing = missing or {}

    # Order: watched (in user-specified order) first, then the rest by name
    watch = watch or []
    names_all = list(services.keys())
    seen = set()
    ordered: List[str] = []
    for w in watch:
        if w in services and w not in seen:
            ordered.append(w); seen.add(w)
    for n in sorted(names_all):
        if n not in seen:
            ordered.append(n)

    # Compute pretty width for alignment
    name_width = max((len(n) for n in ordered), default=0)
    name_width = max(18, min(name_width, 36))  # clamp width

    def _tag_str_for(name: str, pct: float) -> str:
        tag = _severity_tag(pct, down, up)
        extras: List[str] = []
        if stale.get(name):
            extras.append("缓存")
        elif missing.get(name):
            extras.append("缺失")
        parts = [t for t in [tag] if t]
        parts.extend(extras)
        return f"  [{','.join(parts)}]" if parts else ""

    # Sections
    if watch:
        print("[关注服务]")
        for n in ordered:
            if n not in watch:
                continue
            pct = services.get(n, 0.0)
            print(f"  • {n:<{name_width}} | 24h {pct:6.2f}%{_tag_str_for(n, pct)}")
        if not only_watch:
            print("[其他服务]")
            for n in ordered:
                if n in watch:
                    continue
                pct = services.get(n, 0.0)
                print(f"  • {n:<{name_width}} | 24h {pct:6.2f}%{_tag_str_for(n, pct)}")
    else:
        print("[全部服务]")
        for n in ordered:
            pct = services.get(n, 0.0)
            print(f"  • {n:<{name_width}} | 24h {pct:6.2f}%{_tag_str_for(n, pct)}")

    print("-" * max(40, len(header)))


def _build_state(prev_raw: dict, down: List[float]) -> dict:
    """Normalize previous state into {name: {pct: float, degraded: bool}} using only previous data.
    Legacy numeric format is supported; degraded defaults to (pct < max_down) if not provided.
    """
    max_down = max([float(x) for x in down]) if down else 100.0
    state: dict = {}
    for name, entry in (prev_raw or {}).items():
        if isinstance(entry, dict):
            p_prev = float(entry.get("pct", 0.0))
            degraded_prev = bool(entry.get("degraded", (p_prev < max_down)))
        else:
            p_prev = float(entry) if isinstance(entry, (int, float)) else 0.0
            degraded_prev = (p_prev < max_down)
        state[name] = {"pct": p_prev, "degraded": degraded_prev}
    return state


def _check_crossings_and_update(prev_raw: dict, cur: Dict[str, float], watch: List[str], down: List[float], up: List[float]) -> dict:
    # Normalize thresholds
    down_sorted = sorted(set(float(x) for x in down), reverse=True)
    up_sorted = sorted(set(float(x) for x in up))
    max_down = max(down_sorted) if down_sorted else 100.0
    max_up = max(up_sorted) if up_sorted else 100.0

    watch_set = set(watch)

    # Build prev structured state
    prev_state = _build_state(prev_raw, down)
    new_state = {}

    for name, p_cur in cur.items():
        p_cur = float(p_cur)
        prev_entry = prev_state.get(name, {})
        p_prev = float(prev_entry.get("pct", p_cur))
        was_degraded = bool(prev_entry.get("degraded", False))

        # Initialize next entry (carry previous degraded by default)
        new_entry = {"pct": p_cur, "degraded": was_degraded}

        if name in watch_set:
            # Down crossings: prev >= t and cur < t
            for t in down_sorted:
                if p_prev >= t and p_cur < t:
                    _notify("DuckCoding 状态异常", f"{name} 24h 可用率跌破 {t:.0f}% （当前 {p_cur:.2f}%）")
                    new_entry["degraded"] = True

            # Up recovery: only if previously degraded and first time cross above max_up (e.g., 80%)
            if was_degraded and (p_prev <= max_up) and (p_cur > max_up):
                _notify("DuckCoding 状态恢复", f"{name} 24h 可用率升破 {max_up:.0f}% （当前 {p_cur:.2f}%）")
                new_entry["degraded"] = False

        # If still degraded and current is below max_down, keep degraded; otherwise keep last decision
        if new_entry["degraded"] is not False and p_cur < max_down:
            new_entry["degraded"] = True

        new_state[name] = new_entry

    return new_state


def run_once(watch: List[str], down: List[float], up: List[float], only_watch: bool = False) -> None:
    raw = _run_node_fetch()
    services = _normalize_services(raw)

    # Remember good values from this round
    for n, p in services.items():
        _remember_good_pct(n, p)

    # Build fallback view for printing and for decisions
    stale_map: Dict[str, bool] = {}
    missing_map: Dict[str, bool] = {}
    services_view: Dict[str, float] = dict(services)

    # Ensure watched services always appear
    for n in (watch or []):
        if n not in services_view:
            last = _get_last_good_pct(n)
            if last is not None:
                services_view[n] = float(last)
                stale_map[n] = True
            else:
                services_view[n] = 0.0
                missing_map[n] = True

    # Keep previously seen (non-watched) services visible using last-good cache to avoid empty lists
    try:
        for n in list((_LAST_GOOD_SERVICES or {}).keys()):  # type: ignore[name-defined]
            if n not in services_view:
                last = _get_last_good_pct(n)
                if last is not None:
                    services_view[n] = float(last)
                    stale_map[n] = True
    except Exception:
        pass

    _print_snapshot(services_view, watch, down, up, only_watch=only_watch, stale=stale_map, missing=missing_map)

    # Decision gating: only use current or stale-fallback; skip missing (no data at all)
    cur_for_decision: Dict[str, float] = {}
    for n, p in services.items():
        cur_for_decision[n] = p
    for n in (watch or []):
        if n not in cur_for_decision:
            last = _get_last_good_pct(n)
            if last is not None:
                cur_for_decision[n] = float(last)

    prev_raw = _load_state_raw()
    new_state = _check_crossings_and_update(prev_raw, cur_for_decision, watch, down, up)
    _save_state(new_state)


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="DuckCoding Status watcher")
    parser.add_argument('--interval', type=int, default=POLL_INTERVAL_SEC, help='Polling interval seconds (default 300)')
    parser.add_argument('--watch', action='append', default=None, help='Service name to watch; repeat to add multiple. If omitted, use built-in defaults.')
    parser.add_argument('--down', nargs='+', type=float, default=DOWN_THRESHOLDS_DEFAULT, help='Downward thresholds (percent)')
    parser.add_argument('--up', nargs='+', type=float, default=UP_THRESHOLDS_DEFAULT, help='Upward thresholds (percent)')
    parser.add_argument('--once', action='store_true', help='Run a single poll and exit')
    parser.add_argument('--force-messagebox', action='store_true', help='Debug: disable toast and print to console + beep (non-blocking)')
    parser.add_argument('--toast', action='store_true', help='Enable Windows toast (non-blocking). Default off to avoid WNDPROC warnings')
    parser.add_argument('--only-watch', action='store_true', help='Only print watched services (hide others)')
    args = parser.parse_args()

    global FORCE_MESSAGEBOX, USE_TOAST
    FORCE_MESSAGEBOX = bool(args.force_messagebox)
    USE_TOAST = bool(args.toast)

    # Resolve watch list: if user provided any --watch, they override defaults; else use WATCH_DEFAULT
    watch_list = list(dict.fromkeys(args.watch)) if args.watch else list(WATCH_DEFAULT)

    if args.once:
        run_once(watch_list, args.down, args.up, only_watch=bool(args.only_watch))
        return

    print(f"[StatusWatcher] started. Interval={args.interval}s, watch={watch_list}, down={args.down}, up={args.up}, only_watch={bool(args.only_watch)}")
    prev_raw = _load_state_raw()
    while True:
        try:
            raw = _run_node_fetch()
            services = _normalize_services(raw)

            # Remember good values seen this round
            for n, p in services.items():
                _remember_good_pct(n, p)

            # Build view for printing and for decision
            stale_map: Dict[str, bool] = {}
            missing_map: Dict[str, bool] = {}
            services_view: Dict[str, float] = dict(services)

            # Ensure watched services always appear
            for n in watch_list:
                if n not in services_view:
                    last = _get_last_good_pct(n)
                    if last is not None:
                        services_view[n] = float(last)
                        stale_map[n] = True
                    else:
                        services_view[n] = 0.0
                        missing_map[n] = True

            # Keep previously seen non-watched services visible using last-good cache
            try:
                for n in list((_LAST_GOOD_SERVICES or {}).keys()):  # type: ignore[name-defined]
                    if n not in services_view:
                        last = _get_last_good_pct(n)
                        if last is not None:
                            services_view[n] = float(last)
                            stale_map[n] = True
            except Exception:
                pass

            _print_snapshot(services_view, watch_list, args.down, args.up, only_watch=bool(args.only_watch), stale=stale_map, missing=missing_map)

            # Only decide with current+stale (skip truly missing)
            cur_for_decision: Dict[str, float] = {}
            for n, p in services.items():
                cur_for_decision[n] = p
            for n in watch_list:
                if n not in cur_for_decision:
                    last = _get_last_good_pct(n)
                    if last is not None:
                        cur_for_decision[n] = float(last)

            new_state = _check_crossings_and_update(prev_raw, cur_for_decision, watch_list, args.down, args.up)
            _save_state(new_state)
            prev_raw = new_state
        except subprocess.CalledProcessError as e:
            print("[StatusWatcher] Node error:", getattr(e, 'output', str(e)))
        except Exception as e:
            print("[StatusWatcher] Error:", e)
        finally:
            time.sleep(max(5, int(args.interval)))


if __name__ == '__main__':
    main()
