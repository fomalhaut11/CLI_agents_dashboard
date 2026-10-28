# -*- coding: utf-8 -*-
"""Stream Dashboard 配置加载器 (可移植版)。

设计: dashboard 本体零项目耦合。所有项目专属配置(streams / 白名单 / 命令 / 数据集 /
产出目录)外置到一个 JSON 文件, 本模块负责: 定位并加载它 -> ${VAR} 插值 -> 补通用默认
-> 暴露 C.XXX 常量给 app.py。app.py 不感知配置来源。

配置文件定位顺序:
  1. 环境变量 STREAM_DASHBOARD_CONFIG 指向的文件
  2. <REPO_ROOT>/stream-dashboard.config.json
  3. <本 dashboard 目录>/stream-dashboard.config.json
  4. 都没有 -> 内置最小默认(仅通用功能; 项目专属面板呈空态)

REPO_ROOT(要管理的目标仓库根) 定位顺序:
  1. 环境变量 STREAM_DASHBOARD_REPO
  2. 配置文件里的 "repoRoot" (相对路径按配置文件所在目录解析)
  3. 配置文件所在目录
  4. 当前工作目录

插值变量: ${REPO_ROOT} ${DATA_ROOT} ${WT_BASE} ${DASH_DIR}
"""
import json
import os
import platform
import re
import shutil

DASH_DIR = os.path.dirname(os.path.abspath(__file__))
_IS_WIN = platform.system() == "Windows"


# ---------------- 定位并加载配置文件 ----------------
def _find_config_path():
    env = os.environ.get("STREAM_DASHBOARD_CONFIG")
    if env and os.path.isfile(env):
        return os.path.abspath(env)
    env_repo = os.environ.get("STREAM_DASHBOARD_REPO")
    candidates = []
    if env_repo:
        candidates.append(os.path.join(env_repo, "stream-dashboard.config.json"))
    candidates += [
        os.path.join(os.getcwd(), "stream-dashboard.config.json"),
        os.path.join(DASH_DIR, "stream-dashboard.config.json"),
    ]
    for p in candidates:
        if os.path.isfile(p):
            return os.path.abspath(p)
    return None


CONFIG_PATH = _find_config_path()
_CFG = {}
if CONFIG_PATH:
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            _CFG = json.load(f)
    except Exception as e:  # noqa: BLE001
        raise SystemExit("[stream-dashboard] 配置文件解析失败 %s: %s" % (CONFIG_PATH, e))


# ---------------- REPO_ROOT / DATA_ROOT / 插值 ----------------
def _resolve_repo_root():
    env = os.environ.get("STREAM_DASHBOARD_REPO")
    if env:
        return os.path.abspath(env)
    cfg_dir = os.path.dirname(CONFIG_PATH) if CONFIG_PATH else os.getcwd()
    rr = _CFG.get("repoRoot")
    if rr:
        return os.path.abspath(rr if os.path.isabs(rr) else os.path.join(cfg_dir, rr))
    return cfg_dir if CONFIG_PATH else os.getcwd()


REPO_ROOT = _resolve_repo_root()
_data_root_raw = os.environ.get("DATA_ROOT") or _CFG.get("dataRoot") or ""
DATA_ROOT = os.path.normpath(_data_root_raw) if _data_root_raw else ""
WT_BASE = _CFG.get("worktreeBase") or os.path.dirname(REPO_ROOT)

_VARS = {"REPO_ROOT": REPO_ROOT, "DATA_ROOT": DATA_ROOT, "WT_BASE": WT_BASE, "DASH_DIR": DASH_DIR}


def _interp(v):
    if isinstance(v, str):
        return re.sub(r"\$\{(\w+)\}", lambda m: _VARS.get(m.group(1), m.group(0)), v)
    if isinstance(v, list):
        return [_interp(x) for x in v]
    if isinstance(v, dict):
        return {k: _interp(x) for k, x in v.items()}
    return v


def _get(key, default):
    """取配置项(带插值), 缺失用 default。"""
    return _interp(_CFG[key]) if key in _CFG else default


# ---------------- 通用配置(内置默认, 可被 JSON 覆盖) ----------------
HOST = _CFG.get("host", "127.0.0.1")
PORT = int(os.environ.get("STREAM_DASHBOARD_PORT", _CFG.get("port", 5111)))


def _default_shell():
    if _IS_WIN:
        return ["powershell.exe", "-NoLogo"]
    sh = os.environ.get("SHELL") or shutil.which("bash") or "/bin/sh"
    return [sh, "-i"]


SHELL = _get("shell", None) or _default_shell()

# 终端后端: node-pty(新版 ConPTY, 修好全屏 TUI scrollback) 为默认。STREAM_DASHBOARD_PTY=winpty 回滚。
USE_NODE_PTY = os.environ.get("STREAM_DASHBOARD_PTY", "node").lower() != "winpty"
NODE_EXE = os.environ.get("NODE_EXE") or shutil.which("node") or ("node.exe" if _IS_WIN else "node")
TERM_SIDECAR_DIR = os.path.join(DASH_DIR, "term-sidecar")
PTY_BRIDGE_JS = os.path.join(TERM_SIDECAR_DIR, "pty-bridge.js")

# 运行期状态
PROC_LOG_DIR = os.path.join(DASH_DIR, "_proc_logs")
STREAM_META_FILE = os.path.normpath(_get("streamMetaFile", os.path.join(DASH_DIR, "streams_meta.json")))
REGISTRY_FILE = os.path.normpath(_get("registryFile", os.path.join(REPO_ROOT, "stream-dashboard.registry.json")))

# 安全 git 操作白名单 (走 REST) — 通用
SAFE_GIT = _get("safeGit", {
    "fetch": ["fetch", "--all", "--prune"],
    "pull": ["pull", "--ff-only"],
    "status": ["status", "--porcelain=v1", "-b"],
    "diff": ["diff", "--stat"],
    "log": ["log", "--oneline", "-20"],
})

# 产出预览上限 — 通用
ARTIFACT_TEXT_MAX = int(_get("artifactTextMax", 1_000_000))
ARTIFACT_TABLE_ROWS = int(_get("artifactTableRows", 200))


# ---------------- 项目专属配置(缺省为空 -> 对应面板空态) ----------------
STREAM_FOLDER = _get("streamFolder", {})           # {branch: 专属文件夹}
FACTOR_STREAM = _get("factorStream", "")           # 因子型 stream 分支(例外规则), 无则空
CORE_PREFIXES = _get("corePrefixes", [])           # 共享核心白名单前缀
FROZEN_PREFIXES = _get("frozenPrefixes", [])       # 只读冻结区前缀: 任何非 main 分支改到 = 越界
REGISTRY_PATHS = _get("registryPaths", [])         # 共享单文件注册表(改动应走 main)

QUICK_COMMANDS = _get("quickCommands", [           # 通用 git 默认; 项目可覆盖
    {"label": "git status", "cmd": "git status -sb"},
    {"label": "git log(10)", "cmd": "git log --oneline -10"},
    {"label": "pull main", "cmd": "git merge --ff-only main"},
])
JOB_COMMANDS = _get("jobCommands", [])             # 作业型命令(受管进程), 无则空

# 研究页签 (2026-10-26): 独立研究库登记册 (YAML, repos: {名字: {path, status, base_pin, ...}}) +
# 研究库根 (插值 path 里的 ${research_root}) + pin 检出根; 缺省 None -> 研究页签空态
_rr = _get("researchRegistry", None)
RESEARCH_REGISTRY = os.path.normpath(_rr) if _rr else None
_rroot = _get("researchRoot", None)
RESEARCH_ROOT = os.path.normpath(_rroot) if _rroot else None
_pins = _get("basePinsRoot", None)
BASE_PINS_ROOT = os.path.normpath(_pins) if _pins else None
RESEARCH_NEW_CMD = _get("researchNewCmd", None)    # "+ 新建研究库" 命令 ({name} 占位), 无则不显示按钮

# 知识页签 (2026-10-26, knowledge.py): 台账所在仓库 (core/research/*.jsonl, core/factors/meta/factor_registry.json)
# + 研报库目录 catalog.jsonl + 单因子测试归档目录 + 结论收件箱目录; 缺省 None -> 知识页签空态 / 对应子视图为空
def _opt_path(key):
    v = _get(key, None)
    return os.path.normpath(v) if v else None


KNOWLEDGE_REPO = _opt_path("knowledgeRepo")
REPORT_CATALOG = _opt_path("reportCatalog")
TEST_RESULTS_DIR = _opt_path("testResultsDir")
RESEARCH_INBOX = _opt_path("researchInbox")

# 数据面板: (标签, 相对 DATA_ROOT 路径, 最大新鲜天数或 None); JSON 里是数组的数组
KEY_DATASETS = [tuple(x) for x in _get("keyDatasets", [])]
DIR_COUNTS = [tuple(x) for x in _get("dirCounts", [])]

# 产出查看器
_ART = _get("artifacts", {})
ARTIFACT_STREAMS = _ART.get("streams", [])
ARTIFACT_STREAM_CLASSES = _ART.get("streamClasses", [])
ARTIFACT_ROOTS = list(_ART.get("roots", []))
for _r in ARTIFACT_ROOTS:                      # 归一化路径分隔符(插值产生的混合斜杠)
    if _r.get("path"):
        _r["path"] = os.path.normpath(_r["path"])

# 可选: 各 stream 独立 worktree 的产出目录, 由 {streams:{key:wtDir}, subdirs:[[相对路径,标签,task]]} 生成
_WT = _ART.get("worktrees")
if _WT:
    for _st, _wt in (_WT.get("streams") or {}).items():
        for _sub, _lbl, _task in (_WT.get("subdirs") or []):
            ARTIFACT_ROOTS.append({
                "key": "wt_%s_%s" % (_st.replace("-", "_"), _sub.replace("/", "_")),
                "group": "worktree产出", "stream": _st, "task": _task,
                "label": "[worktree] %s (%s/%s)" % (_lbl, _wt, _sub),
                "path": os.path.join(WT_BASE, _wt, *_sub.split("/")),
            })
