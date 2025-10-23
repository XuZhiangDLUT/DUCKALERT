# DuckCoding 额度监控脚本 使用说明

本脚本用于定时查询 DuckCoding 的额度（余额），当剩余额度大于设定阈值时，发送 Windows 通知（优先 toast，失败则回退为 MessageBox 弹窗）。

脚本文件：`duckcoding_quota_watcher.py`

---

## 环境与依赖
- 操作系统：Windows 10/11
- Python：3.8+（已在 3.11 上验证）
- 依赖：
  - 必需：`requests`
  - 可选（推荐）：`win10toast`（用于 Windows 通知 toast）
  - 自动提取令牌（可选）：Node.js + Playwright（已在本仓库 `package.json` 中声明）

安装依赖（命令行）：
```bash
pip install requests win10toast
```

若需使用“自动提取令牌”，请确保安装 Node 及 Playwright 浏览器内核：
```powershell
cd "d:\\User_Files\\Program Files\\DuckCodingAlert"
npm install
npx playwright install
```

---

## 运行方式
- 命令行前台运行（有控制台输出，便于观察日志）：
  ```bash
  cd "d:\\User_Files\\Program Files\\DuckCodingAlert"
  python duckcoding_quota_watcher.py
  ```
- 静默后台运行（无控制台窗口）：
  ```bash
  pythonw "d:\\User_Files\\Program Files\\DuckCodingAlert\\duckcoding_quota_watcher.py"
  ```
- Windows 计划任务（建议用于长期自动运行）：
  1. 打开“任务计划程序” → “创建任务”。
  2. “常规”中勾选“使用最高权限运行”（可选）。
  3. “触发器” → “新建”，设置为“按计划”或“按登录时/启动时”，间隔 1 分钟或合适频率。
  4. “操作” → “新建”：
     - 程序或脚本：`pythonw.exe`
     - 添加参数：`"d:\\User_Files\\Program Files\\DuckCodingAlert\\duckcoding_quota_watcher.py"`
     - 起始于：`d:\\User_Files\\Program Files\\DuckCodingAlert`
  5. 根据需要设置“条件”和“设置”，保存即可。

---

## 参数预设与说明（脚本内顶部）
- `API_URL`：DuckCoding 额度查询接口地址（通常无需修改）。
- `POLL_INTERVAL_SEC`：轮询间隔（秒），默认 `60`。根据需要调整频率。
- `THRESHOLD_YEN`：额度阈值（日元），默认 `5.0`。只要剩余额度高于该阈值，每次轮询都会触发通知（带提示音）。
- `NOTIFY_LIMIT_BEFORE_BLOCK`：累计通知次数上限（默认 `5`）。达到次数后会弹出阻塞式消息框，并自动退出程序。

### 令牌获取策略（已自动化）
脚本启动时按以下优先级解析令牌：
- 环境变量：读取 `DUCKCODING_TOKEN`（若以 `sk-` 开头则直接使用）。
- 自动提取：使用 Node + Playwright 打开 `https://check.duckcoding.com/`，点击“CodeX 专用福利”中的“显示令牌”，自动抓取当前可见的令牌。
- 回退：若以上两者均不可用，则使用脚本内置的回退令牌（仅为兜底，不保证长期有效）。

如希望固定使用你自己的令牌，推荐通过环境变量覆盖：
```powershell
$env:DUCKCODING_TOKEN = "sk-xxxxxxxx"
python duckcoding_quota_watcher.py
```

若要彻底关闭自动提取功能，仅设置环境变量 `DUCKCODING_TOKEN` 即可（脚本会优先使用环境变量，不会再访问网页）。

---

## 通知机制
- 优先使用 `win10toast` 显示 Windows 通知（非阻塞，右下角弹出），并伴随系统提示音。
- 若 `win10toast` 不可用或失败，自动回退为 Win32 `MessageBox` 弹窗（阻塞，需要点击“确定/OK”），同样会播放提示音。
- 只要余额高于阈值（`THRESHOLD_YEN`），每次轮询都会提醒一次；累计达到 `NOTIFY_LIMIT_BEFORE_BLOCK` 次后，会弹出阻塞式消息框提示并自动退出。
- 若未见到 toast：
  - 检查 Windows 通知是否开启、专注助手是否关闭。
  - 某些环境策略可能屏蔽通知，可使用回退 MessageBox 以确保可见。

---

## 日志输出（前台运行时可见）
- 启动：`[DuckCoding] quota watcher started. Checking every <N> seconds`
- 每次查询：`[DuckCoding] Remaining: ¥<金额>`
- 异常：`[DuckCoding] Error: <异常信息>`

退出方式：在命令行按 `Ctrl + C` 终止。

---

## 快速验收与调试建议
- 验证通知是否工作：将 `THRESHOLD_YEN` 临时改为一个很小的值（如 `0.01`），运行脚本，若额度高于阈值应立刻弹出通知。
- 常见问题：
  - 缺少依赖：执行 `pip install requests win10toast`。
  - 网络受限：若需代理，配置系统代理或设置 `HTTP_PROXY/HTTPS_PROXY` 环境变量。
  - Toast 不显示：检查通知权限或使用 MessageBox 回退（保持 `win10toast` 未安装也会触发回退）。

---

## 目录与文件
- 脚本：`duckcoding_quota_watcher.py`
- 位置：`d:\\User_Files\\Program Files\\DuckCodingAlert`

如需增加命令行参数（例如一键触发一次通知的 `--test-notify`），可告知我，我可以为脚本加入简单的 CLI 支持以便验收与运维。
 
### 已提供的 CLI 参数
- `--test-notify`：立即弹出一次测试通知（带提示音）后退出。
- `--once`：仅查询一次额度；若高于阈值则通知一次后退出。
- `--force-messagebox`：强制使用阻塞式 MessageBox（忽略 toast），便于看弹窗与听提示音。
