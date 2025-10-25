# 额度与状态监控脚本 使用说明

本仓库包含两个独立的 Windows 监控脚本：
- 额度监控：`duckcoding_quota_watcher.py`
- 状态监控：`duckcoding_status_watcher.py`

两者均以命令行方式运行，依赖 Node + Playwright 的辅助脚本（位于 `scripts/`）。额度脚本支持阶段化提醒（A/B），状态脚本支持下行/上行阈值提醒，并对抓取偶发失败做了“最后一次有效数据”回退与判定防抖，实际使用更加稳定。另新增：可持续生成 Plotly HTML 仪表盘，配合 VS Code Live Preview/Live Server 在标签页里自动刷新查看三条余额曲线。

---

## 功能概览（额度）
- 自动抓取三类“专用福利”令牌：
  - `Claude Code 专用福利`、`CodeX 专用福利`、`Gemini CLI 专用福利`
  - 每轮打印三行快照（总额度、已使用、百分比、剩余额度）
- 阶段化提醒（仅看 CodeX 剩余额度）：
  - 阶段A：只要剩余 > 阈值（默认 ¥3.00），每轮都提醒
  - 阶段B：仅在剩余向下跨越 50 / 20 / 10 / 5 元时各提醒一次
  - 当剩余 < 阈值时自动回到阶段A并重置
- 实时交互：
  - 通过同目录下 `duckcoding_ack.txt`（内容 `0`/`1`）控制阶段切换（详见下文）
- 稳定性增强：
  - 可信度校验：过滤“总/用/余 全 0.00”的无效结果
  - 最后一次有效数据回退（10 分钟）：抓取异常时沿用上次可信数据，快照行尾标注“缓存”
  - 决策防抖：若既无本轮可信数据、也无缓存，则打印“缺失”并跳过判定，不会误报
  - 分级降级：UI → API → 仅抓取“剩余”数字，尽力给出可用信息
  - 令牌抓取稳态：当三枚令牌不齐时会立即重试（2 次）并缩短缓存 TTL（60s），加速后续刷新；若仅 CodeX 缺失，会优先额外刷新 CodeX。

## 功能概览（状态）
- 每 N 秒（默认 300s）读取服务列表的 24h 可用率并打印快照
- 关注列表：可指定多个服务名称，仅它们参与提醒
- 阈值提醒（非阻塞）：
  - 下行阈值（默认 70/60/50/30/10）：上轮≥阈值、本轮跌破阈值时提醒一次
  - 上行阈值（默认 80）：服务曾跌破“最大下行阈值”后，首次升破 80% 时提醒一次
- 稳定性增强：
  - 最后一次有效数据回退（10 分钟）：关注服务缺失数据时，沿用缓存值打印并参与判定，行尾标注“缓存”
  - 目录保持稳定：非关注服务若本轮缺失，也会使用上一轮有效数据继续显示（标记“缓存”），避免“其他服务”清单突然消失。
  - 决策防抖：既无本轮数据、也无缓存 → 打印“缺失”，同时跳过判定，避免误报/恢复抖动

---

## 环境与依赖
- 操作系统：Windows 10/11
- Python：3.8+（建议 3.11）
- 依赖：
  - 必需：`requests`
  - 可选：`win10toast`（Windows 通知）
  - 辅助：Node.js + Playwright（已在 `package.json` 声明）

安装依赖示例：
```powershell
pip install requests win10toast
cd <项目根目录>
npm install
npx playwright install
```

如需持久浏览器或受限环境更稳，可先行启动 Playwright 后端（可选）：
```powershell
& "C:\\Program Files\\nodejs\\npx.cmd" "@playwright/mcp@latest" --browser=chromium --headless --port 8931
```
---

## 快速开始
- 额度监控（前台运行，有日志）：
```powershell
cd <项目根目录>
python duckcoding_quota_watcher.py
```
- 额度监控（后台运行，无控制台）：
```powershell
pythonw "<项目根目录>\\duckcoding_quota_watcher.py"
```
- 额度：一次性跑一轮（打印三令牌，可能弹一次）：
```powershell
python duckcoding_quota_watcher.py --once
```
- 额度：HTML 实时预览（Plotly）
  1) 在 VS Code 安装并启用 “Live Preview” 或 “Live Server” 插件（任一即可）
  2) 运行脚本并指定输出 HTML 路径（脚本每轮写入会触发自动刷新）：
```powershell
python duckcoding_quota_watcher.py --html duckcoding_dashboard.html
```
  3) 在 VS Code 里打开该 HTML，使用 Live Preview/Live Server 打开为标签页。页面会随文件更新而刷新，显示三条曲线：
     - `Claude Code 专用福利`、`CodeX 专用福利`、`Gemini CLI 专用福利` 的剩余额度（¥）
  说明：HTML 内部引用 Plotly JS CDN（cdn.plot.ly），若有网络/代理限制，请配置系统/VS Code 代理。
- 强制使用消息框（阻塞式）：
```powershell
python duckcoding_quota_watcher.py --force-messagebox
```
- 强制使用 toast（非阻塞）：
```powershell
python duckcoding_quota_watcher.py --force-toast
```
- 测试通知：
```powershell
python duckcoding_quota_watcher.py --test-notify
```

- 状态监控（前台运行，有日志）：
```powershell
# 不带 --watch：打印全部服务
python duckcoding_status_watcher.py
# 带 --watch：仅这些服务参与提醒（打印仍含其他服务，或配合 --only-watch 隐藏）
python duckcoding_status_watcher.py --watch "日本线路（CodeX）" --watch "日本线路（Claude Code）"
# 仅显示关注服务
python duckcoding_status_watcher.py --only-watch --watch "日本线路（CodeX）" --watch "日本线路（Claude Code）"
```
- 状态：一次性快照 + 判定
```powershell
python duckcoding_status_watcher.py --once --only-watch --watch "日本线路（CodeX）" --watch "日本线路（Claude Code）"
```
- 状态：自定义阈值与间隔（默认每 5 分钟）
```powershell
python duckcoding_status_watcher.py --interval 300 --down 70 60 50 30 10 --up 80
```
---

## 抓取重试（状态脚本）
- Python 侧：Node 抓取失败会自动重试（默认 2 次，线性退避 2s、4s），单次抓取超时 75s。
- 结合“最后一次有效数据”回退与“判定防抖”，即便某一轮失败，也不会造成误报或大幅抖动。

## 阶段A / 阶段B 与交互文件（额度脚本）
- 交互文件：`duckcoding_ack.txt`（同目录，内容 `0` 或 `1`）
  - `0`：阶段A（超过阈值就弹窗）
  - `1`：阶段B（按 50/20/10/5 向下里程碑提醒）
- 进入阶段B：
  1) 手动把 `duckcoding_ack.txt` 写为 `1`
  2) 阶段A中连续弹窗达到上限（默认 5 次）后，会弹一次阻塞式消息框，然后自动进入阶段B
- 回到阶段A：当 CodeX 剩余 < ¥3 后自动执行，且重置状态并将 `duckcoding_ack.txt` 写回 `0`

常用操作（PowerShell）：
```powershell
# 我已看到提醒，进入阶段B：
Set-Content -Path duckcoding_ack.txt -Value 1 -NoNewline
# 手动回到阶段A：
Set-Content -Path duckcoding_ack.txt -Value 0 -NoNewline
```

---

## HTML 仪表盘说明（额度）
- 启用方式：`--html <输出路径>`（例如 `duckcoding_dashboard.html`）
- 数据来源：每轮抓取快照后，追加到内存历史并写入单一 HTML 文件
- 可视化：单图三线（Claude/Codex/Gemini 的“剩余额度 ¥”），带时间轴；页头显示“最后更新时间”
- 自动刷新：依赖 VS Code Live Preview/Live Server 对文件变更的自动重载；无需 meta refresh
- 历史长度：保留最近约 720 个点（默认 60s 间隔 ≈ 12 小时），可在源码 `_MAX_HISTORY_POINTS` 调整
- 依赖：不需要额外 Python 包；HTML 依赖 Plotly JS CDN（`https://cdn.plot.ly/plotly-2.29.1.min.js`）

---

## 配置与阈值
- 额度轮询间隔：`POLL_INTERVAL_SEC = 60`
- 额度阈值：`THRESHOLD_YEN = 3.0`
- 阶段B里程碑：`PHASE_B_THRESHOLDS = [50.0, 20.0, 10.0, 5.0]`
- 连续弹窗上限：`NOTIFY_LIMIT_BEFORE_BLOCK = 5`
- 令牌缓存：`_BENEFIT_TOKEN_CACHE_TTL_SEC = 600`（完整时 10 分钟）
- 令牌缓存（不完整时短 TTL）：`_BENEFIT_TOKEN_CACHE_TTL_SEC_INCOMPLETE = 60`
- 令牌抓取重试：`_BENEFIT_REFRESH_MAX_TRIES = 2`
- 额度快照容错缓存：`_LAST_GOOD_TTL_SEC = 600`
- 状态快照容错缓存：`_LAST_GOOD_TTL_SEC = 600`（状态脚本内部同名变量）

如需将 TTL/阈值改为命令行参数，可在后续优化中添加（欢迎提出需求）。
---

## 数据来源与降级策略
- 额度脚本：
  1) 页面 UI（Playwright）为主，解析“总/用/余/百分比”
  2) 回退 API 解析（若适用）
  3) 最后尝试仅抓取“剩余额度”数字（UI→API）
  4) 可信度校验失败 → 使用 10 分钟内“最后一次有效数据”（标注“缓存”）
  5) 若既无可信数据也无缓存 → 标注“缺失”，并跳过本轮判定
- 状态脚本：
  - 使用 Node 脚本从网页读取服务及对应的 24h 可用率
  - 正常化去噪（过滤带 %/ago 等噪声文本，聚合名称变体）
  - 对关注服务同样做“缓存/缺失”与判定防抖

---

## 快照与状态标签说明
- 额度（仅 CodeX 行显示阈值标签）：
  - `[>¥3]` / `[≤¥3]`
  - 当使用缓存或数据缺失时会追加：`[... ,缓存]` / `[... ,缺失]`
- 状态：
  - 严重度标签：`↓<70%`、`↓<60%`、… 或 `↑≥80%`
  - 数据来源标签：`缓存` / `缺失`

示例：
```
  • CodeX 专用福利       | 总 ¥  249.28 | 用 ¥  248.80 (99.8%) | 余 ¥  200.00  [>¥3]
  • 日本线路（CodeX）    | 24h  72.31%  [↓<80%,缓存]
  • 日本线路（Claude）   | 24h  91.05%  [↑≥80%]
```
---

## 常见问题与排查
- 首次使用 Playwright 提示浏览器缺失：执行 `npx playwright install`
- 需要代理：设置 `HTTP_PROXY` / `HTTPS_PROXY`
- 控制台中文乱码：在 PowerShell 运行前执行 `chcp 65001`，脚本已尽量强制 UTF-8 输出
- Windows toast 提示异常：额度脚本默认在独立子进程内展示 toast（非阻塞），失败时会回退为消息框；也可加 `--force-messagebox`
- 判定抖动：通常是网页偶发超时/噪声导致，脚本已加入“可信度校验 + 最后一次有效数据 + 判定防抖”，可通过快照行尾标签确认数据来源

---

## 离线自测（无需网络）
- 运行轻量自测脚本，验证关键辅助逻辑与打印格式（不访问网页、不调用 Node）：
```powershell
python scripts/selftest.py
```
- 输出包含：
  - quota/status 模块导入 OK
  - 打印示例快照（含标签）
  - All passed

---

## 附：Node 脚本（可单独使用）
- `node scripts/fetch_benefit_tokens.js` → 输出三类“专用福利”令牌 JSON 映射
- `node scripts/query_details_from_site.js sk-XXXX` → 输出 `{ name, total_yen, used_yen, used_percent, remaining_yen }`
- `node scripts/query_remaining_from_site.js sk-XXXX` → 仅输出剩余额度数字

---

## 目录与文件
- 额度脚本：`duckcoding_quota_watcher.py`
- 状态脚本：`duckcoding_status_watcher.py`
- 交互文件：`duckcoding_ack.txt`（0/1）
- Node 助手脚本：`scripts/`
- 根路径：`<项目根目录>`

如需更多 CLI 选项（例如将 TTL/阈值做成命令行参数、导出 CSV/Markdown、简易测试），可以提出需求再补充。