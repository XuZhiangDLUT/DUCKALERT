# Repository Guidelines

## Project Structure & Module Organization
- `duckcoding_quota_watcher.py` — Windows notifier that checks DuckCoding remaining ¥ via API/website.
- `scripts/` — Node + Playwright helpers:
  - `fetch_codex_token.js` (auto-reveals CodeX token)
  - `query_remaining_from_site.js` (reads remaining from UI)
- `package.json` — minimal Node setup; `node_modules/` is ignored by Git.
- `README.md` — user-facing usage notes; `temp.md` — local instructions (ignored).

## Build, Test, and Development Commands
- Install Node deps: `npm install`
- Run token fetcher: `node scripts/fetch_codex_token.js`
- Query remaining: `node scripts/query_remaining_from_site.js sk-XXXX`
- Python deps: `pip install requests win10toast`
- One-off check: `python duckcoding_quota_watcher.py --once`
- Background (no console): `pythonw "D:\\User_Files\\Program Files\\DuckCodingAlert\\duckcoding_quota_watcher.py"`
- Useful env: `DUCKCODING_TOKEN`, `DUCKCODING_CHECK_URL`, `HTTP_PROXY`/`HTTPS_PROXY`.

## Coding Style & Naming Conventions
- Python: PEP 8, 4-space indent, type hints, snake_case for functions/vars; keep modules single-purpose.
- JS (CommonJS): kebab-case filenames in `scripts/`, prefer `const`, async/await, no global state.
- Logging: concise, prefixed with `[DuckCoding]`.

## Testing Guidelines
- No formal suite yet. Add smoke tests before refactors:
  - Python: stub network, test `_parse_money`, `fetch_remaining_yen_best()` with `--once`.
  - JS: if adding tests, place in `tests/` as `*.spec.(js|ts)` and use `@playwright/test`.
- Aim for basic behavior coverage; CI optional.

## Commit & Pull Request Guidelines
- Conventional Commits: `<type>(<scope>): <desc>`; types: `feat|fix|refactor|docs|style|test|config|optimization|visualization`.
- Scopes to prefer: `config|scripts|utils|docs|main`.
- Keep messages imperative and specific. Example:
  - `config(config): add .gitignore for Node/Python`
- PRs: clear description, steps to verify, logs/screenshots when UI scraping changes, link related issues.
- Policy: no auto-commit; only commit when explicitly requested.

## Security & Configuration Tips
- Never commit tokens; use env vars. `.env*` files are ignored; keep `DUCKCODING_TOKEN` local.
- When behind a proxy, set `HTTP_PROXY/HTTPS_PROXY` for Playwright and Python requests.