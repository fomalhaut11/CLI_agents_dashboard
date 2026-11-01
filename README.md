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
- **知识页签**(2026-10-26):台账的只读视图(数据层 `knowledge.py`, 配置 `knowledgeRepo` / `reportCatalog` / `testResultsDir` / `researchInbox`): 结论(现行 / 含已取代, 否决与成立同样标色, 取代链)、复现漏斗(研报库目录 -> 复现台账各状态, 可下钻)、因子知识(注册表 test_results + 复现评级 + 单因子测试归档文件与图)、教训(高危且无守护标红)、待处理(收件箱未入账提案 / 跨线请求 / provisional 结论 / 带重开条件的结论 / 无守护高危教训)与全局搜索; 页面不写台账, 只给命令。研究页签卡片另显示 "结论 N · 排队 M"。
- **研究页签**(2026-10-26):原 Streams 页签改名"基座开发"; 新"研究"页签读独立研究库登记册(`researchRegistry`), 每个研究库一张卡片: 工作区干净/脏 N(只算本库)、`base.pin` 与落后基座 main 的提交数(提示不是故障)、影响提示(pin 之后基座改动的 `.py` 与本库 import 的模块: ! 改到被 import 的模块 / ^ 只改了被 import 模块所在顶层包里别的 .py(可能间接用到, tests/ 不算) / o 都没有)、pin 检出联接与研究档位门 hook 是否就绪、数据(登记册 `lineage_nodes` 在血缘快照里的 strict 判定, "数据 a/n 新鲜", 2026-10-29); 终端/会话/改动复用基座开发页签的能力(cwd 在已登记研究库里的终端由后端按库内 `base.pin` 自动设 PYTHONPATH = 研究库根;<basePinsRoot>/<pin>, 标签前缀"研"), 详情页分子页签: 概览 / 升级 pin / 晋升与请求 / RESEARCH_SUMMARY; 登记册 base_pin 与库内 base.pin 不一致显示黄色"登记册待跟进"(提示)。研究库根下有库但登记册没有 -> 顶部提示。刻意没有"同步 main"; 升级 pin 在详情页"升级 pin"区块: 选基座提交 -> "对照"(受管进程, 旧 / 新 pin 下本库测试 + import 判定) -> 无新增失败才能"提交升级" -> 可"回退"; 三步都调 `researchPinCmd` 配置的基座 pin 工具, 缺省只读。
- **晋升队列**(2026-10-27):数据 = 基座跨线请求台账 `<repoRoot>/tools/harness/requests.py list --json`(60 秒缓存; 研究库在自己 main 上提交 `requests/<登记名>.jsonl`, kind `promote` 带成果类型 / 基座落点)。"基座开发"页签顶部一行计数, "查看"开模态: 全部未结请求, 晋升优先再按等待天数, 每条给基座侧命令, 页面不写台账。研究库卡片 "晋升 N", 详情 "晋升与请求" + "发起晋升"(在研究库 main 上 open --kind promote, 只提交本库请求文件)。基座侧台账要含研究库支持 (基座 H-106 之后)。
- **第 4 步**(2026-10-29, 设计稿 5.2-5.4): **数据**子页签 = 本库血缘节点的判定 / 判据 / 内容终点 + 上游(多是基座节点)与下游(谁在用, "@库名" = 别的研究库), 读数据页"点亮图"的血缘快照(`_lineage_snapshot.json`, 按 mtime 缓存; 快照多旧照实显示, 刷新在数据页), `/api/research/data?name=`。**归档**: 概览里"归档 / 取消归档"以受管进程跑 `research_pin.py archive <库> [--unarchive] --execute`(临时 worktree 改登记册 status 并提交, 基座检出 `--ff-only`, 验 main 后删临时 worktree; `POST /api/research/archive` 只校验并给命令); 已归档卡片变灰收进"已归档"折叠区, 详情不给升级 pin / 发起晋升, 后端 pin_trial / pin_upgrade / pin_revert / promote 对已归档拒(400), pin 工具 trial / upgrade 也拒; 基座总览里不标"过旧"。**一键处理**: 晋升队列每条"一键处理"(`POST /api/promotion/land`): 开(或复用)基座 worktree `<基座旁>/wt-<id>` + 分支 `land/<id>`(从 main), 把任务书写进该 worktree 的 git 目录 `LAND_BRIEF.md`(不在工作区, 不会误提交), 前端在那里开终端标签起 `claude "Read <任务书> ..."`; ack / 落地 / `--ff-only` / close 由会话按任务书做(ack 前先问人), 页面不写台账、不碰 main 检出; 原手工命令收在"手工命令"折叠里。
- **来历**(2026-10-29): 研究库详情"来历"子页签(首次打开才扫, 按存档标签 sha 缓存), 把 stream 时期串起来。**档案**: 登记册 `legacy_branch` -> 存档标签 `archive/stream/<分支名>`(及同前缀 WIP 标签)的提交、改过线目录的最近提交、存档里 `streams/<名字>/` + `tests/streams/<名字>/` 的文件树、相对分叉点在线目录外改过的文件(当时在分支上动过的共享代码), 文件名可点开看存档原文(`/api/research/origin_file`, 只许这些文件); `main_snapshot` 冻结快照路径与文件数。**依赖**: 本库 import 的基座模块, 现在的研究库 vs 存档那一版(迁库后新增 / 现在已不用)、pin 之后是否改过、main 上是否还在(找不到 = 改名或删除, 升级 pin 会断)、存档里谁 import。**资产**: 血缘节点(快照判定) + 本库产出根(文件数, "在产出页打开"直达) + 冻结快照里的 results / reports / outputs / state。**疑似产出路径**(未核实): 按行扫存档代码的写文件调用(to_csv / to_parquet / savefig / pickle.dump / open(..,"w") 等), 写调用里的变量按同文件赋值 / 参数默认值 / argparse 默认值追至多 3 层(往上看至多 3 行, 碰到另一个写调用即停), 归类为 数据根 / 基座 results (H-108 前) / 研究库 results / 线目录 / 脚本旁 / 临时目录 / 路径常量 / 追不到, 带文件名线索, 可过滤, 点行号看原文定位。接口 `/api/research/origin?name=`。
- **数据页 "机器" 切换**(2026-10-29): 数据页顶部"机器: 本机 / .22"。本机照旧现算; 选别的机器读拉回本机的数据包 `_remote_<key>.json`(不进 git)。"拉取"以受管进程跑 `remote_fetch.py --machine <key>`: scp dashboard 自己这份 `lineage_snapshot.py` / `data_layers.py` / `remote_snapshot.py` 到那台机器的临时目录 -> ssh 在那里的代码检出与数据根上用那台机器的解释器只读跑 `remote_snapshot.py`(血缘快照 strict judge + 数据集目录 mtime / 大小 / 判定 + 目录计数 + store_v2 版本 + 日更日志 daily_*.log 摘要: 各步 `WAVEk_RC` / `V3RA_GATE_RC`、有无 END、锁文件) -> scp 回本机(包坏了不覆盖旧包)。.22 上约 6-7 分钟(判定要读数据, SQL 族节点会查库水位)。远端视图只读: 不显示管线 / 复检按钮, 血缘图"重新生成快照"改为重拉数据包。机器清单 `REMOTE_DATA_MACHINES`: 本副本从部署脚本 `tools/deploy/sync_to_22.py` 的常量派生(ssh 别名 / 检出 / 数据根 / 临时目录; 解释器入口与日志目录 = 检出上一级的 `zxpy.cmd` / `pipeline_runs`, 同 `tools/deploy/daily_22.cmd`), 独立仓配置 `remoteDataMachines`。远端检出不是 git(sync_to_22 打包部署), 页面显示其血缘定义与执行器的 mtime 代替提交号。接口: `/api/data/machines`、`/api/data/remote/refresh`, 以及 `/api/data/overview|layer|lineage?machine=<key>`。
- **实盘跟踪页签**(2026-10-31): v3ra 实盘的只读视图。数据 = `live_snapshot.py` 在跑实盘的机器 (现为 .22) 上只读跑出的包: 模拟账户逐日 (`results/v3ra/paper_account/account.csv` + state.json)、最新持仓与成交 (positions / trades, 含成交状态计数)、近 15 个目标持仓日 (`results/v3ra/paper/<D>/` 的 summary.json 要点 + input_gate.json)、20:00 输入闸 (`pipeline_runs/gate_latest`)、各链路日志 (20:00 日更 / 07:30 清晨 / 09:25 竞价 / 09:28 开盘 30 分钟 / 22:00 分钟复刻: 各步 `*_RC`、有无 END、末 40 行; 锁文件; daily_skipped.log)、`DATA_ROOT/live` 最新快照文件、最近 12 个交易日各链路有没有产物。路径取那台机器 `config/production.yaml` 的 `holdings_dir` / `out_dir`; 不 import 基座代码, 几秒跑完。"拉取最新"以受管进程跑 `remote_fetch.py --machine <key> --live` (推脚本 -> 远端只读跑 -> 拉回 `_live_<key>.json`, 不进 git, 坏包不覆盖旧包); "本机"按本机检出现算。页签打开时远端机器没有数据包或数据包超过 10 分钟就自动拉一次, 停留期间每 10 分钟再拉 (浏览器标签页在后台 / 切到别的页签时不拉; 2026-11-01)。页面: 告警条 (最近交易日没有目标持仓 / 链路返回码非零 / 输入闸不过 / 锁文件; 账户开始前的缺口不算)、指标卡 (净值 / 累计超额 / 当日超额 / 持仓成交受阻 / 现金)、累计超额折线 (悬停看当日)、账户对比表 (2026-11-01: 生产账户 + `config/production.yaml` `tracking` 的多指数跟踪账户, 每行最新记账日 / 净值 / 累计超额 / 当日超额 / 持仓数 / 最新目标持仓日; 点一行, 指标卡 / 曲线 / 账户逐日 / 目标持仓 / 持仓与成交各块换成该账户; 跟踪账户最近交易日没出持仓或有账户失败进告警条; 链路多一条 08:10 `track_*.log`)、计划任务表 (那台机器 Windows 任务计划程序里的 zx_* / Codex_GS540* 任务: 状态 / 上次运行与结果 / 下次运行 / 错过次数; 链路对应任务不存在、已禁用、下次运行时间已过仍没触发会进告警条 -- 任务没触发就没有日志, 只看日志会显示成空格子)、链路日历 (点格子看日志末尾)、账户逐日表、目标持仓表、持仓与成交 (代码过滤)、链路日志。机器清单同数据页 (`REMOTE_DATA_MACHINES`)。接口: `/api/live?machine=<key>`、`/api/live/refresh`。
- **基座总览**(2026-10-27, 设计稿 5.5): 晋升队列那一行的"基座总览"按钮开模态。**pin 分布**: 各研究库钉的基座提交、pin 日期、落后 main 的提交数与天数, 过旧(落后 > 50 个提交或 > 30 天)高亮, 登记册与库内 base.pin 不一致标出, 并按 pin 分组。**影响预览**: 选 main 最近 30 个提交之一, 或某个基座 worktree 还没进 main 的改动(main...HEAD + 未提交; pin 检出不列), 列出 import 了被改模块的研究库 -- 合并前就知道会影响谁; 判据与研究卡片的影响提示同(! 被改的 .py 是研究库 import 的基座模块或其包下文件; ^ 只改了同一顶层包里别的 .py; 近似, 不看是不是接口)。接口: `/api/base_overview`、`/api/impact_preview?rev=<提交>|path=<worktree>`。
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

关键字段速览(完整见示例文件注释):`repoRoot` / `streamFolder`(分支→专属文件夹)/ `corePrefixes`(共享白名单)/ `frozenPrefixes`(只读冻结区)/ `detachedRoles`(游离工作树用途, 值为说明字符串或 `{"label": 卡片标题, "note": 说明}`; 卡片标题=label(缺省"固定版本"), 下行"锁定版本 <提交> · 与 main 同步/落后 main N"+说明, pin 检出不列)/ `quickCommands` / `jobCommands` / `artifacts`(产出根,支持 `worktrees` 自动生成)/ `keyDatasets`。

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
