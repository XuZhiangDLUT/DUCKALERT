# DuckCoding 监控脚本 使用说明

脚本通过自动打开 DuckCoding 查询页面提取三类福利令牌，并在每次轮询时打印完整额度信息；提醒策略分为阶段A/阶段B，支持用一个 0/1 文本文件与程序“实时交互”。

脚本文件：
- 额度监控：`duckcoding_quota_watcher.py`
- 状态监控：`duckcoding_status_watcher.py`

---

## 功能概览
- 自动抓取三枚福利令牌并查询：
  - `Claude Code 专用福利`、`CodeX 专用福利`、`Gemini CLI 专用福利`
  - 每次轮询固定打印三行（每枚令牌一行）：总额度、已使用（含百分比）、剩余额度
- 阶段A/阶段B 提醒策略（仅以 CodeX 剩余额度判定是否弹窗）：
  - 阶段A：只要 CodeX 剩余 > 阈值（默认 ¥3.00），每次轮询都弹窗
  - 阶段B：仅在 CodeX 剩余向下跨过 50 / 20 / 10 / 5 元时各提醒一次
  - 当 CodeX 剩余 < 阈值（¥3）时，自动回到阶段A，并重置状态
- 实时交互（非阻塞）：
  - 控制文件 `duckcoding_ack.txt`（内容为 `0` 或 `1`）
  - 你把 `0` 改成 `1`，程序在下一次轮询发现后立即进入阶段B（不再连续弹窗）
- 稳定性与性能：
  - 福利令牌在内存缓存 10 分钟（避免每轮都启动浏览器）
  - UI 读取失败时回退到 API 解析（部分福利令牌 API 可能 401，UI 为主）

---

## 环境与依赖
- 操作系统：Windows 10/11
- Python：3.8+（已在 3.11 上验证）
- 依赖：
  - 必需：`requests`
  - 可选（推荐）：`win10toast`（Windows 通知）
  - 自动抓取令牌：Node.js + Playwright（本仓库 `package.json` 已声明）

安装依赖：
```powershell
pip install requests win10toast
cd "d:\\User_Files\\Program Files\\DuckCodingAlert"
npm install
npx playwright install
```

运行前（可选）：启动 Playwright 进程
- 如需使用 MCP 管理的持久浏览器（建议先启动，保持命令窗口不关闭）：
```powershell
& "C:\Program Files\nodejs\npx.cmd" "@playwright/mcp@latest" --browser=chromium --headless --port 8931
```
- 说明：本项目脚本默认会自行启动/关闭浏览器；若你希望复用已启动的浏览器或在受限环境中更稳定地运行，可先启动上面的 Playwright 进程。

可用环境变量：`DUCKCODING_CHECK_URL`、`HTTP_PROXY`/`HTTPS_PROXY`。

---

## 运行方式
- 额度监控（前台运行，有日志）：
```powershell
cd "d:\\User_Files\\Program Files\\DuckCodingAlert"
python duckcoding_quota_watcher.py
```
- 额度监控（后台运行，无控制台）：
```powershell
pythonw "d:\\User_Files\\Program Files\\DuckCodingAlert\\duckcoding_quota_watcher.py"
```
- 额度监控：一次性跑一轮（打印三令牌，按当前阶段规则可能弹一次）：
```powershell
python duckcoding_quota_watcher.py --once
```
- 额度监控：默认系统通知 toast（非阻塞）+ 声音；toast 失败时自动回退为 Windows 消息框。
```powershell
python duckcoding_quota_watcher.py
```
- 如需强制使用消息框（阻塞式）：
```powershell
python duckcoding_quota_watcher.py --force-messagebox
```
- 如需显式启用 toast（默认已启用）：
```powershell
python duckcoding_quota_watcher.py --force-toast
```
- 额度监控：测试通知（默认显示 toast，不阻塞；如加 --force-messagebox 则为弹框）：
```powershell
python duckcoding_quota_watcher.py --test-notify
```

- 状态监控（前台运行，有日志）：
```powershell
# 不带 --watch：默认关注日本线路（CodeX）/日本线路（Claude Code），并打印全部服务
python duckcoding_status_watcher.py

# 带 --watch：关注指定服务，默认仍显示其他服务（关注优先）
python duckcoding_status_watcher.py --watch "日本线路（CodeX）" --watch "CodeX 号池"

# 仅显示关注服务（隐藏其他服务）
python duckcoding_status_watcher.py --only-watch --watch "日本线路（CodeX）" --watch "CodeX 号池"
```
- 状态监控：一次性跑一轮（便于验收）：
```powershell
python duckcoding_status_watcher.py --once --only-watch --watch "日本线路（CodeX）" --watch "CodeX 号池"
```
- 状态监控：自定义阈值与间隔（每5分钟默认）：
```powershell
python duckcoding_status_watcher.py --interval 300 --down 70 60 50 30 10 --up 80 
```

---

## 阶段A / 阶段B 与交互文件
- 交互文件：`duckcoding_ack.txt`（同目录）
  - `0`：阶段A（超过阈值就弹窗）
  - `1`：阶段B（按 50/20/10/5 向下里程碑提醒）
- 进入阶段B的两种方式：
  1) 你把 `duckcoding_ack.txt` 改成 `1`
  2) 阶段A连续弹窗达到上限（默认 5 次）后，会弹出一次阻塞式消息框，然后自动进入阶段B（不再退出）
- 回到阶段A：当 CodeX 剩余 < ¥3 后自动执行，且会重置状态并把 `duckcoding_ack.txt` 写回 `0`

常用操作（PowerShell）：
```powershell
# 我已看到提醒，进入阶段B：
Set-Content -Path duckcoding_ack.txt -Value 1 -NoNewline
# 手动回到阶段A（通常无需手动，程序满足条件会自动重置）：
Set-Content -Path duckcoding_ack.txt -Value 0 -NoNewline
```

---

## 配置与阈值
- 轮询间隔：`POLL_INTERVAL_SEC = 60`
- 余额阈值：`THRESHOLD_YEN = 3.0`（进入阶段A提醒的基准）
- 阶段B里程碑：`PHASE_B_THRESHOLDS = [50.0, 20.0, 10.0, 5.0]`
- 连续弹窗上限：`NOTIFY_LIMIT_BEFORE_BLOCK = 5`（阶段A中触顶后进入阶段B）
- 福利令牌缓存：`_BENEFIT_TOKEN_CACHE_TTL_SEC = 600`（10 分钟）

若需修改阈值与里程碑，直接改脚本顶部对应常量即可；后续我也可以为你加命令行参数。

---

## 令牌解析与查询
- 令牌优先级：
  1) 福利页自动抓取（优先取 `CodeX 专用福利`）
  2) 回退令牌（仅兜底）
- 三令牌查询：页面 UI 先行（与网页显示一致），失败时回退 API 解析
- 打印样例（每次轮询先打印时间分隔线，再固定三行）：
```
[DuckCoding] ----- 2025-01-01 12:34:56 -----
[DuckCoding] Claude Code 专用福利 | 总 ¥0.00 | 用 ¥0.00 (—) | 余 ¥0.00
[DuckCoding] CodeX 专用福利       | 总 ¥249.26 | 用 ¥153.54 (61.6%) | 余 ¥95.73
[DuckCoding] Gemini CLI 专用福利  | 总 ¥0.00 | 用 ¥0.00 (—) | 余 ¥0.00
```

---

## 状态监控（duckcoding_status_watcher.py）
- 作用：每 5 分钟（默认，可配）轮询状态页，动态读取全部服务的 24h 可用率并打印快照。
- 数据来源：先用状态页 API 拉取服务清单，再在页面中就近解析“24小时”百分比，适配服务名称的新增/删除。
- 关注列表：默认关注“日本线路（CodeX）”“日本线路（Claude Code）”；可通过 `--watch` 重复指定多个名称。
- 阈值提醒（非阻塞）：
  - 下行阈值（默认）：`--down 70 60 50 30 10`，当上一次≥阈值且本次跌破阈值时提醒一次。
  - 上行阈值（默认）：`--up 80`，“恢复”仅在服务曾跌破“最大下行阈值”（默认 70）之后，首次升破 80% 时提醒；避免 80% 附近抖动造成频繁提醒。
  - 默认使用“控制台+蜂鸣”非阻塞提醒，尽量避免 Win10 toast 在部分系统上的 WNDPROC/WPARAM 警告。
  - 若需开启 toast：加 `--toast`（仍为非阻塞）；若想禁用 toast（即便可用）：加 `--force-messagebox`（名称沿用旧习惯，此处不会弹 MessageBox）。
- 状态持久化：`status_watcher_state.json`（已加入 .gitignore），用于跨轮询比较阈值跨越。
- 代理与可选环境：`HTTP_PROXY`/`HTTPS_PROXY` 影响 Playwright；`DC_STATUS_URL` 可自定义状态页地址。
- Node 依赖：`scripts/fetch_status_services.js`（Playwright）。首次使用请执行 `npm install && npx playwright install`。

示例
```powershell
# 一次性快照 + 阈值判定
python duckcoding_status_watcher.py --once --watch "日本线路（CodeX）" --watch "日本线路（Claude Code）"

# 常驻轮询（每 5 分钟）
python duckcoding_status_watcher.py --interval 300 --watch "日本线路（CodeX）" --watch "日本线路（Claude Code）"

# 自定义阈值
python duckcoding_status_watcher.py --down 70 60 50 30 10 --up 80 --watch "日本线路（CodeX）"

# 仅抓取原始数据（Node 脚本）
npm run -s dc-status-fetch
```

---

## 常见问题与排查
- 首次使用 Playwright 报浏览器缺失：执行 `npx playwright install`
- 需要代理：设置 `HTTP_PROXY` / `HTTPS_PROXY`
- 编码乱码（如“CodeX 涓撶敤绂忓埄”）：脚本已强制控制台切换到 UTF-8 并统一 Python 输出编码；仍异常时请在 PowerShell 使用 `chcp 65001` 再运行。
- Toast 异常（WPARAM/WNDPROC 等警告）：额度脚本已改为在独立子进程内展示 toast（非阻塞），避免控制台噪声；若 toast 在你机器上无法显示，可加 `--force-messagebox` 切为消息框。状态脚本默认只用非阻塞 toast（无 MessageBox 回退），若 toast 不可用则退为控制台+蜂鸣（仍非阻塞）。

---

## 附：Node 脚本（可单独使用）
- `node scripts/fetch_benefit_tokens.js` → 输出三类福利令牌 JSON 映射
- `node scripts/query_details_from_site.js sk-XXXX` → 输出 { name, total_yen, used_yen, used_percent, remaining_yen }
- `node scripts/query_remaining_from_site.js sk-XXXX` → 仅输出剩余额度数字（旧接口，仍可用于快速探测）

---

## 目录与文件
- 脚本：`duckcoding_quota_watcher.py`
- 交互：`duckcoding_ack.txt`（0/1）
- Node：`scripts/`（Playwright 辅助脚本）
- 路径：`d:\\User_Files\\Program Files\\DuckCodingAlert`

如需 CI/测试或更多 CLI 选项（阈值/里程碑改为命令行参数、导出 CSV/Markdown 等），告诉我我可以补上。