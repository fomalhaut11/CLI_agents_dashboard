# Stream Dashboard 终端显示重构 — 设计讨论存档

> 2026-06-22 讨论存档, 供外部 CLI 接续. 主题: dashboard 内嵌 CLI(claude/codex) 终端的显示架构,
> 从"刮 raw TUI 字节流" 转向 "渲染结构化 transcript".

---

## 0. 起因 (上一条 bug)

dashboard 在 Claude 对话界面显示又不正常. 排查确认: 之前给 **Codex 做的"输入期保护"改造, 没有应用到 Claude**, 且是有意为之、文档里写明的.

证据 (`tools/stream_dashboard/static/app.js`, 当前为未提交工作区改动):
- `app.js:43-45` `isCodexTerm(t)` 只认 codex (`t.agent==="codex"` 或 label 含 "codex").
- `app.js:111` 只有 codex 终端在用户敲键时才设 `userInputUntil = Date.now()+5000`.
- `app.js:79` 状态轮询里"输入时跳过刷新状态灯"的保护带 `isCodexTerm(t) &&` 前缀, Claude 永远进不去这个分支.

`README.md` 白纸黑字:
> Codex 标签另有输入期保护: 用户正在输入时, 短暂跳过该活动 Codex 终端的状态灯更新; **Claude 仍沿用原状态轮询路径。**

→ 通用层保护(标签不变不重建、cols/rows 不变不重发 PTY resize)Claude 有; 但**输入期那层 codex 专属保护 Claude 从来没有**.

最小修法 (若仍走 raw TUI 路线): 新增 `isAgentTerm(t) = isCodexTerm(t) || agent==="claude" || label含"claude"`, 把 79、111 两处 `isCodexTerm` 换成 `isAgentTerm`, 不动 `isCodexTerm` 本身(它在 transcript 类型判定处仍要 codex 语义). 但下面的重构会让这个 bug 直接不存在.

约束提醒: `tools/stream_dashboard/` 在共享白名单, 改动只走 `main`.

---

## 1. 核心结论: "对不同 CLI 适配" 不是没意义, 是适配在了错误的层

现在的痛点 (`userInputUntil`、状态灯轮询、resize 抖动、display:none 光标错位) 全源于一件事:
**试图忠实复刻一个会自我重绘的 TUI 字节流**. raw PTY 输出是给人眼+终端模拟器看的, 不是给程序消费的.
每个 CLI 的重绘节奏/状态启发式都得单独 hack, CLI 升级就可能崩 —— 这层适配是低价值打地鼠.

适配躲不掉, 但该搬到**结构化 transcript 层**, 而这层**已经写完了**:
- `_parse_claude_transcript` (app.py:699): 认 `type=user/assistant`.
- `_parse_codex_transcript` (app.py:737): 认 `response_item` / `event_msg` / `session_meta`.
- `api_session_transcript` (app.py:778) docstring: *"Render saved JSONL into stable chat messages, independent of TUI redraw."*

同样叫"适配 CLI": 刮 TUI(脆) vs 读 JSONL schema(稳). 结论: **砍掉 TUI 字节级适配, 保留 schema 级适配**.

---

## 2. "对话完成后显示对话内容" 可行, 而且能做得更好

`api_session_transcript` 就是要的东西, 现在是二等公民("查看"按钮). 提案本质 = **把它扶正成主界面, raw 终端降级**.

不用等"对话完成": JSONL 逐条 append (claude 每条 assistant/tool/user 落一行; codex 每个 response_item 落一行).
→ **tail 这个文件就能近实时增量渲染**, 比刮重绘 TUI 更顺且无抖动. "完成后才显示" 是保守版, 实际能边跑边出.

---

## 3. "代码编写不输出" 等输出优化 — 现在就能做, 且只有这条路能做

`_content_text(content, include_tools)` 已把 text / tool_use / tool_result 分开. 渲染结构而非字节后可:
- 默认折叠 tool 调用, 写文件只显示一行 `Edit feature_engine.py (+12/-3)`, 点开才看 diff.
- 隐藏 thinking、隐藏 tool_result 噪音.
- 给 codex 的 `phase` 字段做分组.

raw TUI 永远做不到这些 —— 它画啥你只能看到啥. 所以"输出可优化"是换主界面的**核心理由之一**, 不是顺带好处.

---

## 4. 命门: turn 结束后"等待用户输入的选项"

不是脚注, 是决定方案成立与否的中心约束. 拆成两种:

1. **停在提示符等下一句话** — trivial, 输入框送 `prompt + \r` 进 PTY 即可.
2. **弹出带选项的菜单** — 权限确认(`1.Yes 2.Yes,don't ask 3.No`)、plan 审批、AskUserQuestion 多选、模型/slash 选择器. **这才是问题.**

第二类的分水岭 —— 哪些进了 transcript:
- **进 JSONL (作为 tool_use)**: AskUserQuestion、ExitPlanMode 等. 问题文本+选项都在 tool_use 的 input 里, 用户回答作为 tool_result 回写. → **可在网页端用结构化数据重建原生按钮**, 点击把答案送回输入通道.
- **没进 JSONL (harness/TUI-only)**: 工具权限门(最高频那个三选一)、模型选择器、slash 菜单. 它们是 harness 层 UI, 不写对话日志. → 纯 tail transcript 看不到也答不了, 这是真实盲区.

### 干净解法: 让 CLI 跑在"结构化 IO 模式", 选项不再是 TUI 产物

- **Claude**: headless / stream-json (`claude -p --input-format stream-json --output-format stream-json`, 或 Agent SDK). 此模式下:
  - assistant 文本/tool_use 以 JSON 事件流出 (主显示).
  - **权限请求变成结构化 `can_use_tool` / control_request 事件** —— 本来 TUI-only 的三选一变成可接收事件, 渲染成网页按钮, 决定以 JSON 送回.
  - 无 TUI → 无字节刮、无 `userInputUntil`、无状态灯启发式. 第二类盲区里"没进 transcript 的权限门"被补回.
- **Codex**: 显示侧 tail rollout 没问题; rollout 目前只见 `function_call`/`function_call_output`, **没见审批事件**. 审批能否结构化是唯一未知数, 定不了就给 codex 留瘦 PTY 专门应答弹窗.

### 白捡的好处

"有 tool_use 挂着、还没有对应 tool_result" == **turn 正卡在等你**.
这个判据直接从 transcript 结构读出, 可替掉 `status()` (app.py:1216-1239) 那套"输出静默+CPU低=大概等你"的脆弱启发式, 也不再需要 `userInputUntil` 猜用户在不在打字. "等你回话"状态灯第一次能做准.

---

## 5. 目标架构 (双层)

- **主显示层** = tail JSONL 的结构化聊天视图 (带折叠/过滤), 覆盖 95% "看它在干嘛". 后端加 tail/poll 增量接口.
- **输入/逃生层** = 瘦 PTY 通道, 只管送 keystroke 和应答 raw 弹窗. 默认收起, 需要交互时露出. 不再忠实渲染 TUI.
- 选项处理: "等下一句"→输入框; "等选项"→结构化事件重建按钮; "实在重建不了"→落 raw PTY.
- 落地后 `isCodexTerm`/`userInputUntil`/状态灯轮询那一坨 TUI 适配 hack 大部分可删, 第 0 节的 bug 直接不存在.

---

## 6. 待实测确认 (定型前的两个未知数)

1. Claude `--output-format stream-json` 下, **权限请求以什么事件结构出现、能否程序化应答** (control_request / can_use_tool 的实际 schema).
2. Codex 非交互/`exec` 模式有没有**等价的审批事件** —— 决定 codex 能否全脱 TUI, 还是必须留瘦 PTY 兜底.

这两个结果直接决定: Claude 能否彻底退场 TUI; Codex 要不要保留 PTY.

---

## 关键文件索引

- `tools/stream_dashboard/static/app.js` — 前端终端/标签/会话恢复逻辑 (isCodexTerm:43, refreshTermStatus:71, makeTerm:104).
- `tools/stream_dashboard/app.py` — 后端 PTY、transcript 解析、状态检测.
  - `_parse_claude_transcript`:699 / `_parse_codex_transcript`:737 / `api_session_transcript`:778
  - `_content_text(..., include_tools)` — text/tool 分离
  - `Terminal.status()`:1216 / `_scan_tree()`:1174 — 现有忙/等启发式
- `tools/stream_dashboard/README.md` — 现状说明 (含 "Claude 仍沿用原状态轮询路径" 那句).
