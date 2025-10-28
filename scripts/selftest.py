# Lightweight smoke tests for quota/status watchers (offline, no network/Node).
# Validates helper logic, formatting, and stability gates without hitting websites.

import importlib.util
from pathlib import Path
import os

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

    # Persist snapshot to temp data dir
    tmp_dir = ROOT / 'data_test_tmp'
    try:
        path = qx._persist_snapshot_csv(tmp_dir, ['Claude Code 专用福利','CodeX 专用福利','Gemini CLI 专用福利'], details_map)
        assert path.exists(), 'history csv not created'
        content = path.read_text(encoding='utf-8')
        assert 'ts_iso,ts_epoch' in content.splitlines()[0]
        print('[SELFTEST] quota persistence ok')
    finally:
        # Cleanup
        try:
            for p in (tmp_dir.glob('*')):
                p.unlink()
            tmp_dir.rmdir()
        except Exception:
            pass


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


def test_email_config():
    # Validate email config resolution from environment vars + dry-run send (no network)
    qpath = ROOT / 'duckcoding_quota_watcher.py'
    qx2 = import_module(qpath, 'qx2')

    # Backup and set temporary envs
    prev = {k: os.environ.get(k) for k in ['SMTP_USER','SMTP_PASS','ALERT_EMAIL_TO']}
    os.environ['SMTP_USER'] = 'test.sender@gmail.com'
    os.environ['SMTP_PASS'] = 'app-pass-xxxx'
    os.environ['ALERT_EMAIL_TO'] = 'zhiangxu1093@gmail.com'
    try:
        class Args: pass
        args = Args()
        args.email_to = None
        args.smtp_user = None
        args.smtp_pass = None
        args.smtp_host = None
        args.smtp_port = None
        args.smtp_starttls = None
        args.smtp_from = None
        cfg = qx2._resolve_email_config(args)
        assert cfg is not None
        assert cfg.user == 'test.sender@gmail.com'
        assert 'zhiangxu1093@gmail.com' in cfg.to_addrs
        ok = qx2._send_email(cfg, '[SELFTEST] DuckCoding 邮件测试(干跑)', '这是一封干跑测试邮件，不会真正发送。', dry_run=True)
        assert ok
        print('[SELFTEST] email config ok')
    finally:
        # Restore env
        for k, v in prev.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


if __name__ == '__main__':
    test_quota()
    test_status()
    test_email_config()
    print('[SELFTEST] all passed')
