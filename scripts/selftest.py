# Lightweight smoke tests for quota/status watchers (offline, no network/Node).
# Validates helper logic, formatting, and stability gates without hitting websites.

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def import_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)  # type: ignore
    assert spec and spec.loader
    import sys
    sys.modules[name] = mod  # ensure decorators like @dataclass can resolve module in sys.modules
    spec.loader.exec_module(mod)  # type: ignore
    return mod


def test_quota():
    p = ROOT / 'duckcoding_quota_watcher.py'
    qx = import_module(p, 'qx')
    print('[SELFTEST] quota import ok')

    assert qx._is_plausible_details(qx.QuotaDetails()) is False
    assert qx._is_plausible_details(qx.QuotaDetails(remaining_yen=0.48)) is True
    assert qx._is_plausible_details(qx.QuotaDetails(total_yen=100, used_yen=40, remaining_yen=60)) is True

    qx._remember_good('CodeX 专用福利', qx.QuotaDetails(total_yen=100, used_yen=40, remaining_yen=60))
    assert isinstance(qx._get_last_good_if_fresh('CodeX 专用福利'), qx.QuotaDetails)

    assert '[≤¥' in qx._quota_tag('CodeX 专用福利', qx.QuotaDetails(remaining_yen=1.0))
    assert '缓存' in qx._quota_tag('CodeX 专用福利', qx.QuotaDetails(remaining_yen=5.0), stale=True)
    assert '缺失' in qx._quota_tag('CodeX 专用福利', qx.QuotaDetails(remaining_yen=1.0), missing=True)

    details_map = {
        'Claude Code 专用福利': qx.QuotaDetails(total_yen=200, used_yen=150, remaining_yen=50),
        'CodeX 专用福利': qx.QuotaDetails(total_yen=300, used_yen=120, remaining_yen=180),
        'Gemini CLI 专用福利': qx.QuotaDetails(),
    }
    qx._print_quota_snapshot(details_map, ['Claude Code 专用福利','CodeX 专用福利','Gemini CLI 专用福利'], stale={'Gemini CLI 专用福利': True}, missing={'Gemini CLI 专用福利': True})
    print('[SELFTEST] quota snapshot ok')


def test_status():
    p = ROOT / 'duckcoding_status_watcher.py'
    sx = import_module(p, 'sx')
    print('[SELFTEST] status import ok')

    raw = [
        {'name': '日本线路（CodeX）', 'percent_24h': 72.3},
        {'name': '日本线路（Claude Code）', 'percent_24h': 91.1},
    ]
    services = sx._normalize_services(raw)
    assert '日本线路（CodeX）' in services and '日本线路（Claude Code）' in services

    sx._remember_good_pct('日本线路（CodeX）', services['日本线路（CodeX）'])
    assert sx._get_last_good_pct('日本线路（CodeX）') is not None

    sx._print_snapshot(services, ['日本线路（CodeX）','日本线路（Claude Code）'], sx.DOWN_THRESHOLDS_DEFAULT, sx.UP_THRESHOLDS_DEFAULT, only_watch=True, stale={}, missing={})
    print('[SELFTEST] status snapshot ok')


if __name__ == '__main__':
    test_quota()
    test_status()
    print('[SELFTEST] all passed')
