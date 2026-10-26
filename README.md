# Stream Dashboard

一个**本地 Web 控制台**,用于管理**多条 git worktree 并行开发流(stream)**:把 git 动态、约定守卫、diff/提交、多终端(持久 PTY)、**Claude / Codex 会话恢复与 transcript 查看**、进程、产出浏览整合到一个浏览器页面。

> 亮点:**Windows 下能正确渲染 Claude Code / Codex 等全屏 TUI 的 Web 终端**——用 node-pty 捆绑的新版 ConPTY 修复了旧 winpty / 系统 ConPTY 在全屏 TUI 下 scrollback 滚不到历史的问题。

仅监听 `127.0.0.1`。终端和进程都是**真实 shell**,请勿绑定 `0.0.0.0` 对外暴露。

## 功能

- **多 stream 状态板**:每条 stream 一个 git worktree,汇总 git 状态 / 约定守卫(越界改动告警)/ diff / 受控提交。
  卡片徽章: `clean`/`脏 N` = 工作区未提交文件数; `领先 N` = 本分支有 N 个提交未进 main; `落后 N` = main 有 N 个提交本分支未合入 (worktree 是完整检出, 共享核心是分支快照, 不是实时引用 main)。落后时卡片出现 **同步 main** 按钮 (`POST /api/sync_main` = `git merge main`): 工作区脏则拒绝, 冲突则自动 `merge --abort` 并列出冲突文件, 不会留下半合并状态; main 卡片自身仍用 pull (ff-only 对 origin/main)。
- **持久多终端**:每个终端一个真实 shell,浏览器刷新/断线不杀会话(进程登记在服务端)。
- **Claude / Codex 会话恢复 + transcript 查看**:读取结构化会话记录,稳定回看历史,不依赖 TUI 重绘。
- **产出查看器**:白名单根目录下浏览报告/图表/表格(png/pdf/docx/csv/parquet…),带路径穿越防护。
- **研究页签**(2026-10-26):原 Streams 页签改名"基座开发"; 新"研究"页签读独立研究库登记册(`researchRegistry`), 每个研究库一张卡片: 工作区干净/脏 N(只算本库)、`base.pin` 与落后基座 main 的提交数(提示不是故障)、影响提示(pin 之后基座改动的 `.py` 是否碰到本库 import 的模块: o 无 / ! 有)、pin 检出联接与研究档位门 hook 是否就绪、血缘节点数; 终端/会话/改动复用基座开发页签的能力, 详情页给最近提交、pin 之后的基座提交、受影响文件与 RESEARCH_SUMMARY。研究库根下有库但登记册没有 -> 顶部提示。刻意没有"同步 main"(研究库升级 pin 走基座侧工具)。
- **进程 / 作业面板**:注册表落盘(dashboard 重启后自动接管仍在跑的进程,按 pid+create_time 认领防 PID 复用),psutil 整树停止(shell 壳下的真实工作进程一并杀干净),失败红色高亮 + 耗时/结束时间,整树 CPU/内存占用,重跑按钮,同命令防重跑闸门(可确认强制),日志尾部增量读(GB 级日志不卡)+ utf-8/GBK 自动解码 + 下载。

## 依赖

- **Python 3.8+**:`pip install -r requirements.txt`(flask / flask-sock / psutil)
- **Node.js 16+**(终端后端 node-pty):`cd term-sidecar && npm install`
- xterm.js 走 CDN,无需前端构建。

> **平台**:Windows-first(ConPTY 修复针对 Windows)。类 Unix 下 node-pty 走原生 pty,终端同样可用;`shell` 默认取 `$SHELL`/bash。

## 快速开始

```bash
# 1. 装依赖
pip install -r requirements.txt
cd term-sidecar && npm install && cd ..

# 2. 写配置(复制示例, 按你的项目改)
cp stream-dashboard.config.example.json stream-dashboard.config.json
#   至少改 repoRoot 指向你要管理的仓库; streams / 白名单 / 产出目录按需配

# 3. 启动
#   Windows:  start.bat        类 Unix:  ./start.sh        通用:  python app.py
```

浏览器打开 http://127.0.0.1:5111。

## 配置

所有项目专属配置都在 **`stream-dashboard.config.json`**(不进仓;模板见 `stream-dashboard.config.example.json`)。字段全部可选,缺省则对应面板呈空态。

配置文件定位顺序:`STREAM_DASHBOARD_CONFIG` 环境变量 → `<repoRoot>/stream-dashboard.config.json` → dashboard 目录。
路径支持 `${REPO_ROOT}` `${DATA_ROOT}` `${WT_BASE}` 插值。

常用环境变量:

| 变量 | 作用 |
|---|---|
| `STREAM_DASHBOARD_CONFIG` | 指定配置文件路径 |
| `STREAM_DASHBOARD_REPO` | 覆盖目标仓库根(REPO_ROOT) |
| `STREAM_DASHBOARD_PORT` | 端口(默认 5111) |
| `STREAM_DASHBOARD_PTY` | `winpty` 回滚到旧终端后端(需 `pip install pywinpty`) |
| `NODE_EXE` / `PYTHON` | 指定 node / python 解释器 |

关键字段速览(完整见示例文件注释):`repoRoot` / `streamFolder`(分支→专属文件夹)/ `corePrefixes`(共享白名单)/ `quickCommands` / `jobCommands` / `artifacts`(产出根,支持 `worktrees` 自动生成)/ `keyDatasets`。

## 终端后端(ConPTY 修复)

终端 PTY 用 **node-pty 捆绑的新版 ConPTY**(`useConptyDll`),而非 Win10 系统旧 ConPTY / winpty——旧后端在 Claude/Codex 等全屏 TUI 下 scrollback 缺陷导致**滚不到历史**。实现:Python `_Term` 为每个终端起一个 `term-sidecar/pty-bridge.js` 子进程(stdin 帧=输入/resize,stdout=PTY 原始字节),前端 xterm 传 `windowsPty:{backend:"conpty"}`。

回滚:`STREAM_DASHBOARD_PTY=winpty`(仅 Windows,需 pywinpty)。

## 项目布局

```
app.py                          后端 (Flask + flask-sock + node-pty 桥接)
config.py                       配置加载器 (读 JSON + ${VAR} 插值 + 默认值)
stream-dashboard.config.example.json   配置模板
static/  templates/             前端 (xterm.js via CDN)
term-sidecar/                   node-pty 终端后端
  pty-bridge.js                 每终端一个桥接子进程 (新版 ConPTY)
  package.json                  node 依赖 (npm install)
start.bat / start.sh            启动脚本
```

## License

MIT — 见 [LICENSE](LICENSE)。终端基于 [xterm.js](https://github.com/xtermjs/xterm.js) 与 [node-pty](https://github.com/microsoft/node-pty)(均 MIT)。
