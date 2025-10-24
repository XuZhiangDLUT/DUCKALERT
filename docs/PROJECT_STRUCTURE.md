# Project Structure

- README.md — How to install, run, and operate (phases A/B)
- duckcoding_quota_watcher.py — Main Python watcher (entry point)
- duckcoding_ack.txt — 0/1 interactive control file (created on demand)
- requirements.txt — Python dependencies
- .gitignore — Ignore Node/Python artifacts and local control files
- scripts/ — Playwright helpers (Node)
  - fetch_codex_token.js — Auto-reveal CodeX token
  - fetch_benefit_tokens.js — Reveal all benefit tokens and print JSON
  - query_details_from_site.js — Query full details via UI and print JSON
  - query_remaining_from_site.js — Quick remaining-only via UI
- package.json — Node setup + handy npm scripts
- docs/ — Extra docs (this file)
