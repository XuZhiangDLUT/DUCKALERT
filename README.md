# DuckCoding Alert (Windows) — 额度与状态监控

本仓库提供两个独立的 Windows 脚本与一组 Node + Playwright 辅助脚本，用于：
- 额度监控：`duckcoding_quota_watcher.py`（抓取三类“专用福利”余额，分阶段提醒，支持 CSV 历史与 HTML 仪表盘、可选邮件通知）
- 状态监控：`duckcoding_status_watcher.py`（定时读取 24h 可用率，对关注服务做上下行阈值提醒，含“最后一次有效数据”回退与判定防抖）

日志前缀统一为 `[DuckCoding]` / `[StatusWatcher]`，输出尽量简洁。

---

## 常用命令（推荐）
- 先启动 Playwright MCP 浏览器后端（可选但推荐，在受限环境更稳）：
```powershell
& "C:\\Program Files\\nodejs\\npx.cmd" "@playwright/mcp@latest" --browser=chromium --headless --port 8931
```
- 额度监控（仪表盘 + 邮件通知，一条命令）：
```powershell
python duckcoding_quota_watcher.py --html duckcoding_dashboard.html --email
```
- 状态监控（仅显示并关注 CodeX / Claude Code）：
```powershell
python duckcoding_status_watcher.py --only-watch --watch "日本线路（CodeX）" --watch "日本线路（Claude Code）"
```

---

## 主要特性
- 三令牌额度：自动读取 `Claude Code / CodeX / Gemini CLI` 三类专用福利，打印“总/用/%/余”。
- 分阶段提醒（额度）：
  - 阶段A：`CodeX 剩余 > ¥3` 每轮提醒一次；阶段内邮件最多发送一封。
  - 阶段B：仅在余额向下跨越 50 / 20 / 10 / 5 元时提醒一次；跌破 ¥3 自动回到阶段A。
  - 通过 `duckcoding_ack.txt`（内容 `0/1`）进行手动切换与重置。
- 稳定性：UI→API→仅剩余 多级回退；“可信度校验 + 最后一次有效数据(10min) + 判定防抖”。
- 历史与可视化：每轮快照写入 `data/quota_history.csv`；可选 `--html` 生成 Plotly 仪表盘（配合 VS Code Live Preview/Live Server 自动刷新）。
- 状态提醒：可对多服务设置下行/上行阈值（默认 `70/60/50/30/10`、`80`），仅在跨阈值时提醒一次。
- Windows 友好：优先使用 toast（非阻塞），失败时回退为系统消息框；附提示音。

---

## 环境要求
- Windows 10/11
- Python 3.8+（建议 3.11）
- Node.js 18+（含 Playwright 浏览器）
- 网络环境如需代理：设置 `HTTP_PROXY`/`HTTPS_PROXY`

安装依赖示例：
```powershell
# Python 依赖
pip install -r requirements.txt

# Node 依赖 + Playwright 浏览器
npm install
npx playwright install
```

---

## 快速开始（额度监控）
- 前台常驻（每 60s 一轮）：
```powershell
python duckcoding_quota_watcher.py
```
- 一次性检查：
```powershell
python duckcoding_quota_watcher.py --once
```
- 后台（无控制台窗口）：
```powershell
pythonw "D:\\User_Files\\Program Files\\DuckCodingAlert\\duckcoding_quota_watcher.py"
```
- 生成 HTML 仪表盘并自动刷新查看（配合 VS Code Live Preview/Live Server 打开）：
```powershell
python duckcoding_quota_watcher.py --html duckcoding_dashboard.html
```
- 邮件通知（SMTP，阶段A内最多发一封）：
```powershell
# 干跑测试（不发送，只打印）
python duckcoding_quota_watcher.py --email --email-test --email-dry-run
# 正常启用（在超过阈值或阶段B首次跨里程碑时触发）
python duckcoding_quota_watcher.py --email
```
- 通知通道选择/自测：
```powershell
python duckcoding_quota_watcher.py --force-messagebox
python duckcoding_quota_watcher.py --force-toast
python duckcoding_quota_watcher.py --test-notify
```
---

## 快速开始（状态监控）
- 打印全部服务：
```powershell
python duckcoding_status_watcher.py
```
- 仅关注指定服务（参与提醒；打印仍可包含“其他服务”）：
```powershell
python duckcoding_status_watcher.py --watch "日本线路（CodeX）" --watch "日本线路（Claude Code）"
```
- 仅显示关注服务：
```powershell
python duckcoding_status_watcher.py --only-watch --watch "日本线路（CodeX）" --watch "日本线路（Claude Code）"
```
- 一次性快照与判定：
```powershell
python duckcoding_status_watcher.py --once --only-watch --watch "日本线路（CodeX）" --watch "日本线路（Claude Code）"
```
- 自定义阈值与间隔（默认 300s）：
```powershell
python duckcoding_status_watcher.py --interval 300 --down 70 60 50 30 10 --up 80
```
- 启用 Windows toast：
```powershell
python duckcoding_status_watcher.py --toast
```
---

## 环境变量（可选）
- 代理：`HTTP_PROXY` / `HTTPS_PROXY`（Node/Playwright 与 Python 请求都会遵循）
- Playwright：`PLAYWRIGHT_UA` 自定义 UA；`DUCKCODING_CHECK_URL` 覆盖额度查询页；`DC_STATUS_URL` 覆盖状态页
- 数据目录：`DUCKCODING_DATA_DIR`（覆盖 `data/`）
- JS 令牌输入：`DUCKCODING_TOKEN`（仅 Node 脚本 `query_*.js` 可用，Python 额度脚本会自动从福利页抓取）
- 邮件（任意其一生效；支持 `.env`/`.env.local` 自动加载）：
  - `SMTP_HOST` / `SMTP_PORT` / `SMTP_STARTTLS`(1|0) / `SMTP_SSL`(1|0)
  - `SMTP_USER` / `SMTP_PASS`(或 `SMTP_PASSWORD`) / `SMTP_FROM`
  - `ALERT_EMAIL_TO`（逗号分隔）/ `SMTP_TIMEOUT`

`.env.local` 示例（不入库）：
```ini
# Gmail: 需两步验证 + App Password
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_STARTTLS=1
SMTP_SSL=0
SMTP_USER=you@gmail.com
SMTP_PASS=your-app-password
ALERT_EMAIL_TO=to1@example.com,to2@example.com

# 或 QQ 邮箱（开启 POP3/SMTP 后的授权码）
# SMTP_HOST=smtp.qq.com
# SMTP_PORT=465
# SMTP_SSL=1
# SMTP_STARTTLS=0
# SMTP_USER=123456@qq.com
# SMTP_PASS=授权码
```
---

## 提醒策略与交互（额度）
- 基线阈值：`THRESHOLD_YEN = ¥3.0`（源码可改）
- 阶段A：`CodeX 剩余 > ¥3` → 每轮提醒；阶段内邮件最多一次。
- 阶段B：仅在首次向下跨越 `50/20/10/5` 时提醒一次（静默，不发邮件）。
- 自动回切：`剩余 < ¥3` → 回到阶段A，重置状态与邮件额度；`duckcoding_ack.txt` 写回 `0`。
- 手动切换：同目录 `duckcoding_ack.txt`（`0/1`）
```powershell
# 我已确认，切到阶段B
Set-Content -Path duckcoding_ack.txt -Value 1 -NoNewline
# 回到阶段A
Set-Content -Path duckcoding_ack.txt -Value 0 -NoNewline
```
- 音效：优先使用系统提示音；失败回退为 beep。
---

## 数据持久化与仪表盘
- 历史 CSV：每轮将三令牌快照写入 `data/quota_history.csv`（已忽略，不入库）。可用 `--data-dir` 或 `DUCKCODING_DATA_DIR` 更改目录。
- 序列 CSV（永久追加，不清空）：每轮为三条曲线各追加一行到 `data/benefit_series.csv`，字段：
  - `year,month,day,hour,minute,second,curve_id,value,is_cached,is_missing`
  - `curve_id`: 1=Claude Code 专用福利, 2=CodeX 专用福利, 3=Gemini CLI 专用福利
  - `value`: 对应福利“剩余额度(¥)”
  - `is_cached`: 1=使用了“最后一次有效数据”（缓存），0=实时抓取
  - `is_missing`: 1=本轮该福利无数据（既无实时也无缓存），0=有数据（实时或缓存）
- HTML 仪表盘：`--html <path>` 开启；单文件内置 Plotly 图表（从 CDN 加载），显示 3 条“剩余额度 (¥)”曲线与最后更新时间。
  - 建议使用 VS Code 的 Live Preview/Live Server 打开该 HTML，脚本写入时页面自动刷新。
  - 默认保留最近约 12 小时数据（源码 `HISTORY_WINDOW_SEC` / `_MAX_HISTORY_POINTS` 可调）。

- 文件对比（两种 CSV 用途）：
  - `data/quota_history.csv`：宽表，每轮一行，字段包含 `ts_iso, ts_epoch` 与三福利各自的 `total/used/used_percent/remaining`，适合做“完整快照/统计报表”。
  - `data/benefit_series.csv`：长表，每轮三行，字段 `year,month,day,hour,minute,second,curve_id,value,is_cached,is_missing`，适合做“余额曲线/时间序列分析”。

## 稳定性说明
- 额度抓取：优先 UI（Playwright）→ 回退 API → 最后尝试仅抓取“剩余”数字。
- 状态抓取：Node 脚本带 2 次重试（线性退避），并做噪声清洗与名称归一化。
- 决策防抖：无新数据且无缓存时，打印“缺失”并跳过判定，避免误报/抖动。
---

## Node 辅助脚本（可独立使用）
- 获取三类福利令牌映射（JSON）：
```powershell
npm run fetch:tokens
# 或：node scripts/fetch_benefit_tokens.js
```
- 查询单令牌的“剩余额度”（仅数字）：
```powershell
node scripts/query_remaining_from_site.js sk-XXXX
```
- 查询单令牌的完整明细（JSON）：
```powershell
npm run query:details -- sk-XXXX
# 或：node scripts/query_details_from_site.js sk-XXXX
```
- 获取状态页服务及其 24h 可用率（JSON 数组）：
```powershell
node scripts/fetch_status_services.js
```
提示：若 Playwright 报“未安装浏览器”，执行 `npx playwright install`。
---

## 常见问题（FAQ）
- Playwright 提示“未安装浏览器”：执行 `npx playwright install`
- 需要走代理：设置 `HTTP_PROXY` / `HTTPS_PROXY`（Node 与 Python 请求都会遵循）
- 控制台中文乱码：在 PowerShell 运行前执行 `chcp 65001`
- Windows toast 异常：可加 `--force-messagebox`；仍会有提示音
- Node/Playwright 未找到：确保 Node 已安装并在 PATH 中；或使用完整路径调用
- 网页结构变化导致抓取失败：优先 UI→API→仅剩余，多级回退可缓解；若持续失败请更新 `scripts/*.js`
- 离线轻量自测：
```powershell
python scripts/selftest.py
```
---

## 目录结构（关键文件）
- `duckcoding_quota_watcher.py` — 额度监控（支持阶段提醒/CSV/HTML/邮件）
- `duckcoding_status_watcher.py` — 状态监控（24h 可用率，阈值提醒）
- `scripts/` — Node + Playwright 辅助脚本（token 抓取、额度查询、状态抓取）
- `data/` — 本地历史数据（忽略，不入库）
- `requirements.txt` / `package.json` — 依赖清单

## 安全与配置
- 不要把令牌/密码写入仓库。优先使用环境变量或 `.env.local`（已在 `.gitignore` 中忽略）。
- 如在公司/学校网络，请按需设置 `HTTP_PROXY` / `HTTPS_PROXY`。

如需更多 CLI 选项或扩展（例如将阈值改为参数、导出 Markdown 等），欢迎提出需求。
