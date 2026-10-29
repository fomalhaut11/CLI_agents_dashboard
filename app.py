# -*- coding: utf-8 -*-
"""
Stream Dashboard v1.4 - 多 stream worktree 一体化管理控制台

标签页: Streams(git状态+约定守卫+diff+多终端[持久PTY]+会话恢复[claude/codex]) / 实验注册表 / 进程 / 数据 / 产出(实验输出查看) / 后台Agent
仅监听 127.0.0.1. 终端/进程是真实 shell, 切勿绑定 0.0.0.0.
启动: python tools/stream_dashboard/app.py  ->  http://127.0.0.1:5111
"""
import codecs
import json
import fnmatch
import os
import re
import subprocess
import sys
import threading
import time

from flask import Flask, jsonify, render_template, request, send_file
from flask_sock import Sock

try:
    import winpty  # 仅 USE_NODE_PTY=False 回滚时需要
except Exception:  # noqa: BLE001
    winpty = None

try:
    import psutil
except Exception:  # noqa: BLE001
    psutil = None

import config as C
import data_layers as DL
import knowledge as KN

app = Flask(__name__)
sock = Sock(app)

os.makedirs(C.PROC_LOG_DIR, exist_ok=True)

# ---------------- git 辅助 ----------------

def run_git(cwd, args, timeout=60):
    try:
        p = subprocess.run(["git"] + args, cwd=cwd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, "[timeout]"
    except Exception as e:  # noqa: BLE001
        return 1, "[error] %s" % e


def list_worktrees():
    rc, out = run_git(C.REPO_ROOT, ["worktree", "list", "--porcelain"])
    if rc != 0:
        return []
    trees, cur = [], {}
    for line in out.splitlines():
        if line.startswith("worktree "):
            if cur:
                trees.append(cur)
            cur = {"path": line[9:].strip()}
        elif line.startswith("branch "):
            cur["branch"] = line[7:].strip().replace("refs/heads/", "")
        elif line.startswith("detached"):
            cur["branch"] = "(detached)"
    if cur:
        trees.append(cur)
    return trees


# ---------------- 约定守卫 ----------------

def classify_path(branch, f):
    """返回 (level, kind, msg). level: ok|warn|violation."""
    f = f.replace("\\", "/")
    if branch == "main" or branch == "(detached)":
        return ("ok", "main", "")
    for rp in C.REGISTRY_PATHS:
        if f == rp:
            return ("warn", "registry", "注册表改动应走 main")
    # main 上的冻结快照区 (H-097): 研究线迁出时的快照, 只读; 研究线在研究库里改
    for pre in getattr(C, "FROZEN_PREFIXES", ()):
        if f.startswith(pre):
            return ("violation", "frozen", "冻结快照 (H-097) 只读, 研究线在研究库里改")
    allowed = C.STREAM_FOLDER.get(branch)
    if allowed and f.startswith(allowed + "/"):
        return ("ok", "own", "")
    # 本 stream 的测试。CLAUDE.md 无条件约束 #6 要求测试一律放 tests/ 下, 与
    # "新代码只许待在 streams/<line>/" 表面冲突; 约定的实际落法是
    # tests/streams/<line>/ ---- 守卫此前不认这条, 把合规测试全报成"未归类"。
    if allowed and f.startswith("tests/" + allowed + "/"):
        return ("ok", "own_test", "")
    # 别的 stream 文件夹 (含其测试目录)
    for b, folder in C.STREAM_FOLDER.items():
        if b == branch:
            continue
        if f.startswith(folder + "/") or f.startswith("tests/" + folder + "/"):
            return ("violation", "wrong_stream", "属于 %s" % b)
    # 因子型 stream: 因子文件 OK
    if branch == C.FACTOR_STREAM:
        if f.startswith("factors/repository/") or f.startswith("tests/"):
            return ("ok", "factor", "")
    if not C.STREAM_FOLDER:
        # 没有 stream 分支了 (2026-10-28 全部退役): 本仓分支都是基座开发线, 改共享核心是本职,
        # 落地走 worktree + --ff-only. 共享前缀只标注不告警 (提交时 harness A0 闸会给 warn).
        for pre in C.CORE_PREFIXES:
            if f.startswith(pre):
                return ("ok", "core", "共享核心: 落地走 worktree + --ff-only 进 main")
        return ("ok", "base", "")
    # 共享核心
    for pre in C.CORE_PREFIXES:
        if f.startswith(pre):
            return ("violation", "core", "共享核心, 应走 main")
    return ("warn", "unclassified", "未归类, 建议放 stream 文件夹")


# git 默认对非 ASCII 路径输出 C-quoted 形式: 整个路径被双引号包住, 每个中文字节
# 写成一个反斜杠八进制转义。一旦带上这层包装, 下面所有 startswith 前缀判定全部失配
# ---- 于是**凡文件名含中文的都被报成"未归类", 不管放得多正确** (2026-08-31 实测
# etf-timing 99 条告警里 34 条是这么来的)。core.quotepath=false 让 git 直接吐 UTF-8
# 原文; strip('"') 留作仍会触发引用的边角情形 (路径含空格或引号)。
GIT_NOQUOTE = ["-c", "core.quotepath=false"]


def guard_report(path, branch):
    """该 worktree 相对 main 的改动 (已提交+工作区) 的约定检查."""
    files = set()
    rc, out = run_git(path, GIT_NOQUOTE + ["diff", "--name-only", "main...HEAD"])
    if rc == 0:
        files.update(l.strip().strip('"') for l in out.splitlines() if l.strip())
    rc, out = run_git(path, GIT_NOQUOTE + ["status", "--porcelain=v1"])
    if rc == 0:
        for line in out.splitlines():
            if len(line) > 3:
                files.add(line[3:].strip().strip('"'))
    items = []
    for f in sorted(files):
        level, kind, msg = classify_path(branch, f)
        if level != "ok":
            items.append({"file": f, "level": level, "kind": kind, "msg": msg})
    return items


# ---------------- stream meta (状态板) ----------------

def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


# ---------------- worktree 状态采集 ----------------

def worktree_info(wt):
    path, branch = wt["path"], wt.get("branch", "?")
    info = {"path": path, "name": os.path.basename(path.rstrip("\\/")), "branch": branch,
            "staged": 0, "unstaged": 0, "untracked": 0, "ahead": 0, "behind": 0,
            "commits": [], "guard": [], "ok": os.path.isdir(path)}
    if not info["ok"]:
        return info
    rc, out = run_git(path, ["status", "--porcelain=v1"])
    if rc == 0:
        for line in out.splitlines():
            if not line:
                continue
            if line.startswith("??"):
                info["untracked"] += 1
            else:
                if line[0] != " ":
                    info["staged"] += 1
                if line[1] != " ":
                    info["unstaged"] += 1
    base = "origin/main" if branch == "main" else "main"
    rc, out = run_git(path, ["rev-list", "--left-right", "--count", "%s...HEAD" % base])
    if rc == 0 and out.strip():
        try:
            behind, ahead = out.split()
            info["behind"], info["ahead"] = int(behind), int(ahead)
        except ValueError:
            pass
    rc, out = run_git(path, ["log", "--oneline", "-5"])
    if rc == 0:
        info["commits"] = [l for l in out.splitlines() if l.strip()]
    info["guard"] = guard_report(path, branch)
    return info


# ---------------- 路由: 页面 ----------------

@app.route("/")
def index():
    return render_template("index.html", quick=C.QUICK_COMMANDS, jobs=C.JOB_COMMANDS)


@app.route("/api/streams")
def api_streams():
    meta = load_json(C.STREAM_META_FILE, {})
    pins_root = getattr(C, "BASE_PINS_ROOT", None)
    pins_pre = _norm(pins_root) + os.sep if pins_root else None
    roles = getattr(C, "DETACHED_ROLES", None) or {}
    out, pins_hidden = [], 0
    for wt in list_worktrees():
        if pins_pre and _norm(wt.get("path")).startswith(pins_pre):
            pins_hidden += 1            # 研究库的 pin 检出: 研究页签 / 基座总览管, 不当开发线列卡片
            continue
        info = worktree_info(wt)
        info["meta"] = meta.get(info["branch"], {"status": "ACTIVE", "note": ""})
        if info["branch"] == "(detached)":   # 游离: 停在某提交上, 不在任何分支上 -> 标出停在哪 + 用途
            rc, head = run_git(info["path"], ["rev-parse", "--short=8", "HEAD"]) if info["ok"] else (1, "")
            info["head"] = head.strip() if rc == 0 else ""
            role = roles.get(info["name"], "")   # 说明字符串, 或 {"label": 卡片标题, "note": 说明}
            if isinstance(role, dict):
                info["role_label"], info["role"] = role.get("label", ""), role.get("note", "")
            else:
                info["role_label"], info["role"] = "", role
        out.append(info)
    active = sum(1 for x in out if x["branch"] not in ("main", "(detached)") and x["meta"]["status"] == "ACTIVE")
    return jsonify({"streams": out, "wip_active": active, "pins_hidden": pins_hidden})


# ---------------- 路由: git 安全操作 + diff + 分文件提交 ----------------

@app.route("/api/git", methods=["POST"])
def api_git():
    b = request.get_json(force=True)
    path, action = b.get("path"), b.get("action")
    if action not in C.SAFE_GIT:
        return jsonify({"rc": 1, "out": "不允许: %s" % action}), 400
    if not path or not os.path.isdir(path):
        return jsonify({"rc": 1, "out": "无效路径"}), 400
    rc, out = run_git(path, C.SAFE_GIT[action])
    return jsonify({"rc": rc, "out": out})


@app.route("/api/sync_main", methods=["POST"])
def api_sync_main():
    """一键让 stream 分支跟上 main: git merge main (非 ff).

    保护:
      - main 自身不允许 (main 该用 pull)
      - 工作区有未提交改动直接拒绝 (merge 会把脏文件卷进来或中途失败)
      - 合并冲突时自动 --abort, 把冲突文件列表返回, 让用户去终端处理;
        worktree 永远不会停留在半合并状态
    """
    b = request.get_json(force=True)
    path = b.get("path")
    if not path or not os.path.isdir(path):
        return jsonify({"rc": 1, "out": "无效路径"}), 400
    rc, branch = run_git(path, ["rev-parse", "--abbrev-ref", "HEAD"])
    branch = branch.strip()
    if rc != 0:
        return jsonify({"rc": rc, "out": branch})
    if branch == "main":
        return jsonify({"rc": 1, "out": "main 自身请用 pull (与 origin/main 同步), 不做 merge"})
    rc, st = run_git(path, ["status", "--porcelain=v1"])
    dirty = [l for l in st.splitlines() if l.strip()]
    if dirty:
        return jsonify({"rc": 1, "out": "工作区有 %d 个未提交改动, 先 commit 或 stash 再同步:\n%s"
                        % (len(dirty), "\n".join(dirty[:30]))})
    rc, cnt = run_git(path, ["rev-list", "--left-right", "--count", "main...HEAD"])
    behind = cnt.split()[0] if rc == 0 and cnt.strip() else "?"
    if behind == "0":
        return jsonify({"rc": 0, "out": "已与 main 同步, 无需合并"})
    rc, out = run_git(path, ["merge", "--no-edit", "main"], timeout=300)
    if rc == 0:
        return jsonify({"rc": 0, "out": "已合入 main 的 %s 个提交\n\n%s" % (behind, out)})
    _, conflicts = run_git(path, ["diff", "--name-only", "--diff-filter=U"])
    conflicts = [l for l in conflicts.splitlines() if l.strip()]
    run_git(path, ["merge", "--abort"])
    msg = ("合并有冲突, 已自动回退 (git merge --abort), worktree 保持合并前状态.\n"
           "冲突文件 %d 个:\n%s\n\n"
           "请在终端手工合并: git merge main ; 共享白名单文件取 main 版本 "
           "(git checkout main -- <path>), 本线文件夹内保留自己的.\n\n--- git 输出 ---\n%s"
           % (len(conflicts), "\n".join(conflicts[:50]), out))
    return jsonify({"rc": rc, "out": msg})


@app.route("/api/changed")
def api_changed():
    path = request.args.get("path")
    if not path or not os.path.isdir(path):
        return jsonify([])
    rc, out = run_git(path, ["status", "--porcelain=v1"])
    files = []
    if rc == 0:
        for line in out.splitlines():
            if len(line) < 3:
                continue
            x, y = line[0], line[1]
            f = line[3:].strip().strip('"')
            files.append({"file": f, "x": x, "y": y,
                          "staged": x not in (" ", "?"), "untracked": line.startswith("??")})
    return jsonify(files)


@app.route("/api/filediff")
def api_filediff():
    path, f = request.args.get("path"), request.args.get("file")
    staged = request.args.get("staged") == "1"
    if not path or not f:
        return jsonify({"diff": ""})
    args = ["diff"] + (["--cached"] if staged else []) + ["--", f]
    rc, out = run_git(path, args)
    if not out.strip() and not staged:
        # 未跟踪文件: 用 no-index 对比 /dev/null
        rc, out = run_git(path, ["diff", "--no-index", "--", os.devnull, f])
    return jsonify({"diff": out})


@app.route("/api/stage", methods=["POST"])
def api_stage():
    b = request.get_json(force=True)
    path, files, unstage = b.get("path"), b.get("files", []), b.get("unstage", False)
    if not path or not files:
        return jsonify({"rc": 1, "out": "缺参数"}), 400
    args = (["restore", "--staged"] if unstage else ["add", "--"]) + files
    rc, out = run_git(path, args)
    return jsonify({"rc": rc, "out": out})


@app.route("/api/commit", methods=["POST"])
def api_commit():
    b = request.get_json(force=True)
    path, files, msg = b.get("path"), b.get("files", []), (b.get("message") or "").strip()
    if not path or not msg:
        return jsonify({"rc": 1, "out": "缺路径或提交信息"}), 400
    if files:
        rc, out = run_git(path, ["add", "--"] + files)
        if rc != 0:
            return jsonify({"rc": rc, "out": out})
    rc, out = run_git(path, ["commit", "-m", msg])
    return jsonify({"rc": rc, "out": out})


@app.route("/api/worktree", methods=["POST"])
def api_worktree_add():
    b = request.get_json(force=True)
    name, branch = (b.get("name") or "").strip(), (b.get("branch") or "").strip()
    if not name or not branch:
        return jsonify({"rc": 1, "out": "name/branch 必填"}), 400
    target = os.path.abspath(os.path.join(C.REPO_ROOT, "..", name))
    rc, out = run_git(C.REPO_ROOT, ["worktree", "add", target, "-b", branch])
    return jsonify({"rc": rc, "out": out})


# ---------------- 路由: stream 状态板 ----------------

@app.route("/api/stream_meta", methods=["POST"])
def api_stream_meta():
    b = request.get_json(force=True)
    branch = b.get("branch")
    if not branch:
        return jsonify({"rc": 1}), 400
    meta = load_json(C.STREAM_META_FILE, {})
    meta[branch] = {"status": b.get("status", "ACTIVE"), "note": b.get("note", "")}
    save_json(C.STREAM_META_FILE, meta)
    return jsonify({"rc": 0})


# ---------------- 路由: 研究库 (研究页签, 2026-10-26) ----------------
# 读登记册 (C.RESEARCH_REGISTRY, YAML: repos: {名字: {path, status, base_pin, lineage_nodes, data_namespace, ...}}).
# 研究库 = 独立 git 库, 钉基座某提交 (库根 base.pin); 卡片只算本库的脏 / 提交, pin 落后基座 main 多少是提示不是故障.
# 影响提示 (设计稿 5.2): pin..main 改动的基座 .py 与本库 import 的基座模块求交集 -- ! 改到被 import 的模块 (需升级并
# 复核结果); ^ 没改到被 import 的模块, 但改了同一顶层包里别的 .py (可能经包内 import 间接用到, 建议升级); o 都没有.
# 不提供 "同步 main" (研究库升级 pin 走基座的 tools/research_pin.py upgrade).

_RESEARCH_IMPORTS = {}   # (库路径, HEAD sha) -> 本库 import 的基座模块 (缓存)
_IMPORT_RE = re.compile(r"^\s*(?:from\s+([A-Za-z_][\w.]*)\s+import\b|import\s+([A-Za-z_][\w.]*))", re.M)
_RESEARCH_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{1,40}$")


def _research_registry():
    path = getattr(C, "RESEARCH_REGISTRY", None)
    if not path or not os.path.isfile(path):
        return {}
    try:
        import yaml
        with open(path, encoding="utf-8") as f:
            return (yaml.safe_load(f) or {}).get("repos") or {}
    except Exception:  # noqa: BLE001 -- 没装 pyyaml / 文件坏: 页签空态, 不拖垮面板
        return {}


def _research_path(entry, name):
    raw = str((entry or {}).get("path") or "")
    root = getattr(C, "RESEARCH_ROOT", None) or ""
    if "${research_root}" in raw:
        if not root:
            return ""
        raw = raw.replace("${research_root}", root)
    return os.path.normpath(raw) if raw else (os.path.join(root, name) if root else "")


def _norm(p):
    p = str(p or "")
    if p.startswith("\\\\?\\"):
        p = p[4:]
    return os.path.normcase(os.path.normpath(p))


def _read_pin(repo):
    try:
        with open(os.path.join(repo, "base.pin"), encoding="utf-8") as f:
            return (f.read().split() or [""])[0].strip()
    except OSError:
        return ""


def research_term_env(cwd):
    """终端 cwd 落在某个已登记研究库里 -> (库名, PYTHONPATH, 说明); 不在任何研究库里 -> (None, None, "").
    PYTHONPATH = 研究库根;<pin 检出根>/<base.pin> (与 research_pin.py 写进 env.ps1 / .claude/settings.json 的同式,
    但直接按库内 base.pin 现算, 不信可能过期的副本). pin 检出不存在 -> 不设, 说明里给 prepare 命令."""
    c = _norm(cwd)
    for name, entry in _research_registry().items():
        repo = _research_path(entry, name)
        if not repo:
            continue
        r = _norm(repo)
        if c != r and not c.startswith(r + os.sep):
            continue
        pin = _read_pin(repo)
        pins_root = getattr(C, "BASE_PINS_ROOT", None)
        if not pin or not pins_root:
            return name, None, "没有 base.pin 或未配置 pin 检出根, PYTHONPATH 未设"
        pd = os.path.join(pins_root, pin)
        if not os.path.isdir(pd):
            return name, None, "pin 检出 %s 不存在, PYTHONPATH 未设 (python tools/research_pin.py prepare %s --execute)" % (pd, pin[:8])
        return name, os.pathsep.join([repo.replace("\\", "/"), pd.replace("\\", "/")]), ""
    return None, None, ""


def _base_imports(repo, head):
    """本库 .py 里 import 的基座模块 (顶层名在基座仓库里存在、且不是 streams 的)."""
    key = (repo, head)
    if key in _RESEARCH_IMPORTS:
        return _RESEARCH_IMPORTS[key]
    mods = set()
    for dp, dn, fn in os.walk(repo):
        dn[:] = [d for d in dn if d not in (".git", "__pycache__", "node_modules")]
        for name in fn:
            if not name.endswith(".py"):
                continue
            try:
                with open(os.path.join(dp, name), encoding="utf-8", errors="replace") as f:
                    text = f.read()
            except OSError:
                continue
            for m in _IMPORT_RE.finditer(text):
                mod = m.group(1) or m.group(2)
                top = mod.split(".")[0]
                if top == "streams":
                    continue
                if os.path.isdir(os.path.join(C.REPO_ROOT, top)) or os.path.isfile(os.path.join(C.REPO_ROOT, top + ".py")):
                    mods.add(mod)
    _RESEARCH_IMPORTS[key] = sorted(mods)
    return _RESEARCH_IMPORTS[key]


def _impact(repo, head, pin):
    """(pin 落后 main 的提交数, ! 档 [(模块, 文件)], ^ 档 [文件]) -- pin..main 改动的基座文件 vs 本库 import."""
    if not pin:
        return None, [], []
    rc, out = run_git(C.REPO_ROOT, ["rev-list", "--count", "%s..main" % pin])
    behind = int(out.strip()) if rc == 0 and out.strip().isdigit() else None
    if not behind:
        return behind, [], []
    rc, out = run_git(C.REPO_ROOT, GIT_NOQUOTE + ["diff", "--name-only", pin, "main"])
    changed = [l.strip().strip('"') for l in out.splitlines() if l.strip()] if rc == 0 else []
    hits = _import_hits(repo, head, changed)
    return behind, hits, _import_soft(repo, head, changed, hits)


def _import_hits(repo, head, changed):
    """改动的基座文件里被本库 import 碰到的 [(模块, 文件)] (研究卡片影响提示与基座总览影响预览共用).
    只算 .py: "接口有变" 先用 "被 import 的模块文件有改动" 近似 (设计稿 5.2); config/*.yaml 等数据文件不算."""
    hits = []
    for mod in _base_imports(repo, head):
        pre = mod.replace(".", "/")
        for f in changed:
            if f.endswith(".py") and (f == pre + ".py" or f.startswith(pre + "/")):
                hits.append((mod, f))
    return hits


def _import_soft(repo, head, changed, hits):
    """^ 档: 没被本库直接 import、但与被 import 的模块同属一个顶层包的改动 .py (如 import core.paths 时 core/x.py 改了).
    tests/ 不算 (不进运行路径). 只是 "可能间接用到" 的提示, 不判定接口."""
    hard = {f for _, f in hits}
    tops = {m.split(".")[0] for m in _base_imports(repo, head)} - {"tests"}
    return sorted(f for f in changed
                  if f.endswith(".py") and f not in hard and f.split("/")[0] in tops)


def _impact_level(hits, soft):
    return "!" if hits else ("^" if soft else "o")


# ---------------- 研究库的外部数据节点 (设计稿 5.2 / 5.3 第 5 项, 2026-10-29) ----------------
# 登记册 lineage_nodes = 产者在本研究库的血缘节点. 新鲜度不在 web 线程里现判, 读数据页 "点亮图" 的血缘快照
# (lineage_snapshot.py, strict judge, 与 manage.py data scan 同口径); 快照多旧页面照实显示, 刷新走数据页的按钮.
_FRESH = ("OK", "FROZEN", "STATIC")
_SNAP = {"mtime": None, "v": None}


def _lineage_snap():
    """{"generated_at", "nodes": {名字: 节点}, "down": {名字: [下游]}, "age_s"}; 没有快照 -> None. 按文件 mtime 缓存."""
    try:
        mt = os.path.getmtime(LINEAGE_SNAPSHOT)
    except OSError:
        return None
    if _SNAP["mtime"] != mt:
        try:
            with open(LINEAGE_SNAPSHOT, encoding="utf-8") as f:
                snap = json.load(f)
        except (OSError, ValueError):
            return None
        nodes = {n["name"]: n for n in snap.get("nodes") or [] if n.get("name")}
        down = {}
        for n in nodes.values():
            for u in n.get("upstream") or []:
                down.setdefault(u, []).append(n["name"])
        _SNAP.update(mtime=mt, v={"generated_at": snap.get("generated_at"), "nodes": nodes, "down": down})
    return dict(_SNAP["v"], age_s=int(time.time() - mt))


def _data_summary(names):
    """卡片: 本库血缘节点里 新鲜 (OK / FROZEN / STATIC) 的个数, 不新鲜的名字, 快照里没有的名字."""
    if not names:
        return None
    snap = _lineage_snap()
    if snap is None:
        return {"n": len(names), "fresh": 0, "stale": [], "unknown": list(names), "snapshot": None, "age_s": None}
    fresh, stale, unknown = 0, [], []
    for n in names:
        node = snap["nodes"].get(n)
        if node is None:
            unknown.append(n)
        elif node.get("status") in _FRESH:
            fresh += 1
        else:
            stale.append(n)
    return {"n": len(names), "fresh": fresh, "stale": stale, "unknown": unknown,
            "snapshot": snap["generated_at"], "age_s": snap["age_s"]}


def _owner_map():
    """血缘节点 -> 产者研究库名 (登记册 lineage_nodes); 不在里面的是基座节点."""
    return {n: name for name, e in _research_registry().items() for n in (e or {}).get("lineage_nodes") or []}


def _node_row(snap, n, owners):
    node = snap["nodes"].get(n) if snap else None
    row = {"name": n, "owner": owners.get(n) or "基座", "in_snapshot": node is not None, "fresh": False}
    if node:
        row.update({k: node.get(k) for k in ("status", "why", "endpoint", "frequency", "wave", "frozen")})
        row["fresh"] = node.get("status") in _FRESH
    return row


@app.route("/api/research/data")
def api_research_data():
    """研究库详情 "数据": 本库节点 (状态 / 判据 / 终点) + 各自的上游 (多来自基座) 与下游 (谁在用)."""
    name = request.args.get("name") or ""
    reg = _research_registry()
    if name not in reg:
        return jsonify({"error": "登记册里没有 %s" % name}), 404
    snap = _lineage_snap()
    owners = _owner_map()
    rows = []
    for n in (reg[name] or {}).get("lineage_nodes") or []:
        row = _node_row(snap, n, owners)
        node = snap["nodes"].get(n) if snap else None
        row["upstream"] = [_node_row(snap, u, owners) for u in (node or {}).get("upstream") or []]
        row["external"] = list((node or {}).get("external") or [])
        row["downstream"] = [_node_row(snap, d, owners) for d in sorted((snap or {}).get("down", {}).get(n) or [])]
        rows.append(row)
    return jsonify({"nodes": rows, "snapshot": (snap or {}).get("generated_at"), "age_s": (snap or {}).get("age_s"),
                    "missing_snapshot": snap is None})


def research_info(name, entry):
    repo = _research_path(entry, name)
    info = {"name": name, "path": repo, "status": (entry or {}).get("status") or "active",
            "exists": bool(repo) and os.path.isdir(os.path.join(repo, ".git")),
            "lineage_nodes": list((entry or {}).get("lineage_nodes") or []),
            "data_namespace": (entry or {}).get("data_namespace"),
            "registered_pin": str((entry or {}).get("base_pin") or ""),
            "staged": 0, "unstaged": 0, "untracked": 0, "branch": "", "commits": [],
            "pin": "", "pin_behind": None, "impact": [], "impact_soft": 0, "impact_level": "",
            "link_ok": False, "hook_ok": False}
    info["archived"] = info["status"] == "archived"
    info["data"] = _data_summary(info["lineage_nodes"])
    if not info["exists"]:
        return info
    rc, out = run_git(repo, ["status", "--porcelain=v1", "-b"])
    if rc == 0:
        for line in out.splitlines():
            if line.startswith("##"):
                info["branch"] = line[3:].split("...")[0].strip()
            elif line.startswith("??"):
                info["untracked"] += 1
            elif line:
                info["staged"] += line[0] != " "
                info["unstaged"] += line[1] != " "
    rc, out = run_git(repo, ["log", "--format=%h %ad %s", "--date=short", "-5"])
    info["commits"] = [l for l in out.splitlines() if l.strip()] if rc == 0 else []
    rc, head = run_git(repo, ["rev-parse", "HEAD"])
    head = head.strip() if rc == 0 else ""
    pin = _read_pin(repo)
    info["pin"] = pin
    info["pin_behind"], hits, soft = _impact(repo, head, pin)
    info["impact"] = sorted({m for m, _ in hits})
    info["impact_soft"] = len(soft)
    info["impact_level"] = _impact_level(hits, soft) if info["pin_behind"] else ""
    pins_root = getattr(C, "BASE_PINS_ROOT", None)
    if pins_root and pin:
        try:
            tgt = os.readlink(os.path.join(pins_root, pin, "streams", name))
            info["link_ok"] = _norm(tgt) == _norm(os.path.join(repo, "streams", name))
        except (OSError, ValueError):
            info["link_ok"] = False
    rc, hooks = run_git(repo, ["rev-parse", "--git-path", "hooks"])
    hp = hooks.strip()
    hp = hp if os.path.isabs(hp) else os.path.join(repo, hp)
    try:
        with open(os.path.join(hp, "pre-commit"), encoding="utf-8", errors="replace") as f:
            info["hook_ok"] = "# research-gate v1" in f.read()
    except OSError:
        info["hook_ok"] = False
    return info


@app.route("/api/research")
def api_research():
    reg = _research_registry()
    repos = [research_info(n, e) for n, e in reg.items()]
    known = {_norm(r["path"]) for r in repos if r["path"]}
    root = getattr(C, "RESEARCH_ROOT", None)
    unregistered = []
    if root and os.path.isdir(root):
        unregistered = sorted(d for d in os.listdir(root)
                              if os.path.isdir(os.path.join(root, d, ".git")) and _norm(os.path.join(root, d)) not in known)
    rc, main_sha = run_git(C.REPO_ROOT, ["rev-parse", "--short=8", "main"])
    counts = KN.research_counts(_kn_repo(), getattr(C, "RESEARCH_INBOX", None)) if _kn_repo() else {}
    reqs, _ = _all_requests()
    for r in repos:     # 已晋升到基座结论台账的现行结论数 / 收件箱排队数 (知识库设计 8 节)
        r["knowledge"] = counts.get(r["name"]) or {"findings": 0, "queued": 0}
        # 本库提给基座、仍未结的请求 (晋升 / 其他), 晋升队列 (H-106)
        mine = [q for q in reqs if q.get("from") == r["name"] and q.get("status") in ("open", "acked")]
        r["requests_open"] = {"promote": sum(q.get("kind") == "promote" for q in mine),
                              "other": sum(q.get("kind") != "promote" for q in mine)}
    return jsonify({"repos": repos, "unregistered": unregistered,
                    "configured": bool(getattr(C, "RESEARCH_REGISTRY", None)),
                    "main": main_sha.strip() if rc == 0 else "",
                    "can_create": bool(getattr(C, "RESEARCH_NEW_CMD", None))})


@app.route("/api/research/detail")
def api_research_detail():
    name = request.args.get("name") or ""
    reg = _research_registry()
    if name not in reg:
        return jsonify({"error": "登记册里没有 %s" % name}), 404
    info = research_info(name, reg[name])
    if not info["exists"]:
        return jsonify(info)
    rc, out = run_git(info["path"], ["log", "--format=%h %ad %s", "--date=short", "-20"])
    info["commits"] = [l for l in out.splitlines() if l.strip()] if rc == 0 else []
    summary = os.path.join(info["path"], "streams", name, "RESEARCH_SUMMARY.md")
    try:
        with open(summary, encoding="utf-8", errors="replace") as f:
            info["summary"] = f.read(200_000)
    except OSError:
        info["summary"] = ""
    _, info["term_pythonpath"], info["term_note"] = research_term_env(info["path"])
    rc, head = run_git(info["path"], ["rev-parse", "HEAD"])
    _, hits, soft = _impact(info["path"], head.strip(), info["pin"])
    info["impact_files"] = sorted({"%s  <- %s" % (f, m) for m, f in hits})
    info["impact_soft_files"] = soft
    info["pin_log"] = []
    if info["pin"]:
        rc, out = run_git(C.REPO_ROOT, ["log", "--format=%h %ad %s", "--date=short", "-30", "%s..main" % info["pin"]])
        info["pin_log"] = [l for l in out.splitlines() if l.strip()] if rc == 0 else []
    return jsonify(info)


# ---------------- 研究库 "来历" (stream 时期, 2026-10-29) ----------------
# 研究线迁库前是基座仓库里的 stream 分支: 原分支已退役, 提交在存档标签 archive/stream/<分支名> (登记册 legacy_branch),
# main 上 streams/<名字>/ 是迁出时的冻结快照 (登记册 main_snapshot, H-097). 这里把三样串起来:
#   档案   存档标签 (及同前缀的 WIP 标签) 的提交 / 线目录文件树 / 线目录外改过的文件, 文件原文可点开
#   依赖   本库 import 的基座模块: 现在的研究库 vs 存档标签那一版, 标 新增 / 消失 / pin 之后改过 / main 上已不存在
#   资产   血缘节点 + 研究库产出目录 + 冻结快照里的 results / reports + 疑似产出路径 (存档代码里写文件的调用点,
#          按行粗扫, 未核实: 写调用里的变量按同文件里的赋值追至多 3 层, 按追出来的表达式归类; 函数参数 / 跨文件
#          传进来的路径追不到, 归 "看上下文")
_ORIGIN_CACHE = {}       # (库名, 存档标签 sha) -> 扫描结果 (档案内容不变, 按 sha 缓存)
_WRITE_RE = re.compile(r"\.(to_parquet|to_pickle|to_csv|to_excel|to_json|to_feather|to_hdf|savefig|write_text|"
                       r"write_bytes|to_html)\s*\(|\b(pickle\.dump|json\.dump|joblib\.dump|np\.save[z]?|"
                       r"numpy\.save|shutil\.copy\w*|torch\.save)\s*\(|\bopen\s*\([^)]*['\"](?:w|wb|a|ab|w\+)['\"]")
_PATH_CONST_RE = re.compile(r"\b(DATA_ROOT|RESULTS_DIR|LOGS_DIR|PROJECT_ROOT|AUXILIARY_DIR|RAW_FACTORS_DIR|"
                            r"BARRA_DIR|FACTOR_STORE_V2_DIR|[A-Z][A-Z0-9_]*_(?:DIR|ROOT|PATH))\b")
_STR_RE = re.compile(r"""['"]([^'"\n]{2,120})['"]""")
_ASSIGN_RE = re.compile(r"^\s*([A-Za-z_]\w*)\s*(?::[^=]+)?=(?!=)\s*(.+)$")
_IDENT_RE = re.compile(r"\b([A-Za-z_]\w*)\b")
_DEFAULT_RE = re.compile(r"""(?:[(,]\s*|--)([A-Za-z_][\w-]*)['"]?\s*(?::[^=,()]+)?=\s*([^,()]+(?:\([^()]*\))?)""")


def _legacy_tags(entry):
    """(主存档标签, [同前缀的其他存档标签]); legacy_branch 形如 stream/<x> -> archive/stream/<x>."""
    br = str((entry or {}).get("legacy_branch") or "").strip()
    if not br.startswith("stream/"):
        return "", []
    main_tag = "archive/" + br
    rc, out = run_git(C.REPO_ROOT, ["tag", "-l", main_tag + "*"])
    tags = [t.strip() for t in out.splitlines() if t.strip()] if rc == 0 else []
    return (main_tag if main_tag in tags else ""), [t for t in tags if t != main_tag]


def _git_blobs(ref, paths):
    """{路径: 文本} -- 一次 cat-file --batch 读完 (档案几百个文件, 不逐个起 git)."""
    if not paths:
        return {}
    spec = "".join("%s:%s\n" % (ref, x) for x in paths).encode("utf-8")
    try:
        p = subprocess.run(["git", "cat-file", "--batch"], cwd=C.REPO_ROOT, input=spec, capture_output=True,
                           timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        return {}
    out, data, i = {}, p.stdout, 0
    for path in paths:
        nl = data.find(b"\n", i)
        if nl < 0:
            break
        head = data[i:nl].split()
        i = nl + 1
        if len(head) < 3 or head[1] != b"blob":
            continue
        n = int(head[2])
        out[path] = data[i:i + n].decode("utf-8", "replace")
        i += n + 1
    return out


def _mods_in(texts):
    """{基座模块: [出现的文件]} -- 规则同 _base_imports (顶层名在基座仓库里存在、且不是 streams)."""
    mods = {}
    for path, text in texts.items():
        for m in _IMPORT_RE.finditer(text):
            mod = m.group(1) or m.group(2)
            top = mod.split(".")[0]
            if top == "streams":
                continue
            if os.path.isdir(os.path.join(C.REPO_ROOT, top)) or os.path.isfile(os.path.join(C.REPO_ROOT, top + ".py")):
                mods.setdefault(mod, []).append(path)
    return mods


def _resolve(expr, assigns, depth=3, seen=None):
    """expr 里出现的标识符若在同文件有赋值, 把赋值表达式拼进来 (至多 depth 层). 取最近一次以前的赋值太贵, 取最后一次."""
    seen = set() if seen is None else seen
    out = [expr]
    if depth <= 0:
        return expr
    for ident in _IDENT_RE.findall(expr):
        if ident in seen or ident not in assigns:
            continue
        seen.add(ident)
        out.append(_resolve(assigns[ident], assigns, depth - 1, seen))
    return " ".join(out)


def _write_kind(line, name=""):
    consts = sorted(set(_PATH_CONST_RE.findall(line)))
    low = line.replace("\\", "/").lower()
    if "DATA_ROOT" in consts or "stockdata" in low or any(c in consts for c in ("AUXILIARY_DIR", "RAW_FACTORS_DIR",
                                                                                   "BARRA_DIR", "FACTOR_STORE_V2_DIR")):
        kind = "数据根"
    elif "PROJECT_ROOT" in consts and "results" in low:
        kind = "基座 results (H-108 前写进基座检出)"
    elif "RESULTS_DIR" in consts or "LOGS_DIR" in consts:
        kind = "研究库 results / logs"
    elif ("streams/" + name) in low or ("streams', '" + name) in low or ('streams", "' + name) in low:
        kind = "线目录 streams/%s (H-108 前在基座检出里)" % name if "PROJECT_ROOT" in consts else "线目录 streams/%s" % name
    elif "__file__" in line:
        kind = "脚本旁 (相对 __file__)"
    elif "PROJECT_ROOT" in consts:
        kind = "基座检出下 (PROJECT_ROOT)"
    elif "tempfile" in low or "tmp_path" in low or "mkdtemp" in low:
        kind = "临时目录"
    elif consts:
        kind = "路径常量 " + ", ".join(consts)
    else:
        kind = "追不到 (调用方传入 / 运行时拼出)"
    return kind, consts


def _suspect_writes(texts, name="", limit=2000):
    rows = []
    for path in sorted(texts):
        if not path.endswith(".py") or path.startswith("tests/"):
            continue
        lines = texts[path].splitlines()
        assigns = {}
        for line in lines:
            if line.lstrip().startswith("#"):
                continue
            m = _ASSIGN_RE.match(line)
            if m and not line.lstrip().startswith(("def ", "async def ")):
                assigns[m.group(1)] = m.group(2)
            for a, d in _DEFAULT_RE.findall(line):     # def f(out_dir=STATE_DIR) / add_argument("--out-dir", default=...)
                assigns.setdefault(a.replace("-", "_"), d)
        for i, line in enumerate(lines):
            if line.lstrip().startswith("#") or not _WRITE_RE.search(line):
                continue
            up = []                                              # 路径常与写调用分行拼: 带上至多 3 行上文,
            for j in range(i - 1, max(-1, i - 4), -1):           # 碰到另一个写调用就停 (不借别人的路径)
                if _WRITE_RE.search(lines[j]):
                    break
                if not lines[j].lstrip().startswith("#"):
                    up.insert(0, lines[j])
            ctx = " ".join(up + [line])
            resolved = _resolve(ctx, assigns)
            kind, consts = _write_kind(resolved, name)
            lits = [x for x in _STR_RE.findall(resolved) if ("/" in x or "." in x) and " " not in x
                    and not x.startswith(".")][:4]
            rows.append({"file": path, "line": i + 1, "code": line.strip()[:200], "kind": kind, "hints": lits})
            if len(rows) >= limit:
                return rows, True
    return rows, False


def _origin_scan(name, tag, tag_sha):
    key = (name, tag_sha)
    if key in _ORIGIN_CACHE:
        return _ORIGIN_CACHE[key]
    roots = ["streams/%s" % name, "tests/streams/%s" % name]
    rc, out = run_git(C.REPO_ROOT, GIT_NOQUOTE + ["ls-tree", "-r", "--name-only", tag, "--"] + roots)
    files = [l.strip() for l in out.splitlines() if l.strip()] if rc == 0 else []
    texts = _git_blobs(tag, [f for f in files if f.endswith(".py")])
    mods = _mods_in(texts)
    writes, truncated = _suspect_writes(texts, name)
    rc, mb = run_git(C.REPO_ROOT, ["merge-base", "main", tag])
    outside = []
    if rc == 0 and mb.strip():
        rc, out = run_git(C.REPO_ROOT, GIT_NOQUOTE + ["diff", "--name-only", mb.strip(), tag])
        outside = [l.strip() for l in out.splitlines()
                   if l.strip() and not any(l.strip().startswith(r + "/") for r in roots)] if rc == 0 else []
    res = {"files": files, "mods": mods, "writes": writes, "writes_truncated": truncated,
           "outside": outside, "merge_base": mb.strip()[:8] if rc == 0 else ""}
    _ORIGIN_CACHE[key] = res
    return res


def _mod_exists_on_main(mod):
    base = mod.replace(".", "/")
    for cand in (base + ".py", base + "/__init__.py", base):
        rc, _ = run_git(C.REPO_ROOT, ["cat-file", "-e", "main:" + cand])
        if rc == 0:
            return True
    parent = base.rsplit("/", 1)[0] if "/" in base else ""     # from pkg.mod import Name: Name 可能是属性
    return bool(parent) and run_git(C.REPO_ROOT, ["cat-file", "-e", "main:" + parent + ".py"])[0] == 0


def _dir_count(path):
    if not path or not os.path.isdir(path):
        return None
    n = 0
    for _dp, dn, fn in os.walk(path):
        dn[:] = [d for d in dn if d not in (".git", "__pycache__")]
        n += len(fn)
        if n > 100000:
            break
    return n


@app.route("/api/research/origin")
def api_research_origin():
    """研究库详情 "来历": stream 时期的档案 / 依赖 / 资产 (见本节注释)."""
    name = request.args.get("name") or ""
    reg = _research_registry()
    if name not in reg:
        return jsonify({"error": "登记册里没有 %s" % name}), 404
    entry = reg[name] or {}
    repo = _research_path(entry, name)
    exists = bool(repo) and os.path.isdir(os.path.join(repo, ".git"))
    tag, other_tags = _legacy_tags(entry)
    snap_rel = str(entry.get("main_snapshot") or "")
    res = {"name": name, "legacy_branch": entry.get("legacy_branch") or "", "tag": tag, "other_tags": [],
           "main_snapshot": snap_rel, "snapshot_files": None, "tag_info": None, "commits": [], "files": [],
           "outside": [], "deps": [], "writes": [], "writes_truncated": False, "assets": []}
    for t in other_tags:
        rc, line = run_git(C.REPO_ROOT, ["log", "-1", "--format=%h %cs %s", t])
        res["other_tags"].append({"tag": t, "line": line.strip() if rc == 0 else ""})
    if snap_rel:
        rc, out = run_git(C.REPO_ROOT, GIT_NOQUOTE + ["ls-tree", "-r", "--name-only", "main", "--", snap_rel])
        res["snapshot_files"] = len([l for l in out.splitlines() if l.strip()]) if rc == 0 else None

    scan = None
    if tag:
        rc, sha = run_git(C.REPO_ROOT, ["rev-list", "-n", "1", tag])
        sha = sha.strip()
        rc, line = run_git(C.REPO_ROOT, ["log", "-1", "--format=%h%x09%cs%x09%s", tag])
        parts = line.strip().split("\t", 2) if rc == 0 else []
        res["tag_info"] = {"sha": sha, "short": parts[0] if parts else "", "date": parts[1] if len(parts) > 1 else "",
                           "subject": parts[2] if len(parts) > 2 else ""}
        rc, out = run_git(C.REPO_ROOT, ["log", "-20", "--format=%h %cs %s", tag, "--",
                                        "streams/%s" % name, "tests/streams/%s" % name])
        res["commits"] = [l for l in out.splitlines() if l.strip()] if rc == 0 else []
        scan = _origin_scan(name, tag, sha)
        res["files"] = scan["files"]
        res["outside"] = scan["outside"][:300]
        res["outside_total"] = len(scan["outside"])
        res["merge_base"] = scan["merge_base"]
        res["writes"], res["writes_truncated"] = scan["writes"], scan["writes_truncated"]

    # 依赖: 现在 vs 存档; pin 之后是否改过 (同影响提示判据); main 上是否还在
    now, pin_files = {}, set()
    if exists:
        rc, head = run_git(repo, ["rev-parse", "HEAD"])
        now = {m: True for m in _base_imports(repo, head.strip() if rc == 0 else "")}
        pin = _read_pin(repo)
        if pin:
            _behind, hits, _soft = _impact(repo, head.strip(), pin)
            pin_files = {m for m, _f in hits}
    then = (scan or {}).get("mods") or {}
    for mod in sorted(set(now) | set(then)):
        res["deps"].append({"module": mod, "now": mod in now, "then": mod in then,
                            "then_files": sorted(set(then.get(mod) or []))[:5], "changed_since_pin": mod in pin_files,
                            "on_main": _mod_exists_on_main(mod)})

    # 资产: 血缘节点 / 研究库产出目录 / 冻结快照里的结果
    snap = _lineage_snap()
    owners = _owner_map()
    for n in entry.get("lineage_nodes") or []:
        row = _node_row(snap, n, owners)
        res["assets"].append({"kind": "血缘节点", "label": n, "status": row.get("status") or "快照里没有",
                              "fresh": row["fresh"], "detail": row.get("endpoint") or ""})
    for r in getattr(C, "ARTIFACT_ROOTS", []):
        if r.get("stream") == name and not r.get("virtual"):
            res["assets"].append({"kind": "研究库产出", "label": r.get("label"), "path": r.get("path"),
                                  "art_key": r["key"], "files": _dir_count(r.get("path"))})
    for sub in ("results", "reports", "outputs", "state"):
        rel = "%s/%s" % (snap_rel, sub) if snap_rel else ""
        if rel and os.path.isdir(os.path.join(C.REPO_ROOT, *rel.split("/"))):
            res["assets"].append({"kind": "冻结快照", "label": rel, "path": os.path.join(C.REPO_ROOT, *rel.split("/")),
                                  "files": _dir_count(os.path.join(C.REPO_ROOT, *rel.split("/")))})
    return jsonify(res)


_ORIGIN_PATH_RE = re.compile(r"^(?:tests/)?streams/[a-z0-9_]+/[^\0]*$")


@app.route("/api/research/origin_file")
def api_research_origin_file():
    """存档标签里某个文件的原文 (只许本库的 streams/<名字>/ 与 tests/streams/<名字>/ 下, 或线目录外改过的文件)."""
    name, path = request.args.get("name") or "", request.args.get("path") or ""
    reg = _research_registry()
    if name not in reg:
        return jsonify({"error": "登记册里没有 %s" % name}), 404
    tag, _ = _legacy_tags(reg[name])
    if not tag:
        return jsonify({"error": "%s 没有存档标签" % name}), 404
    if ".." in path.split("/") or path.startswith("/") or ":" in path:
        return jsonify({"error": "路径不合规"}), 400
    rc, sha = run_git(C.REPO_ROOT, ["rev-list", "-n", "1", tag])
    scan = _origin_scan(name, tag, sha.strip())
    if path not in scan["files"] and path not in scan["outside"]:
        return jsonify({"error": "不是本库存档里的文件: %s" % path}), 400
    text = _git_blobs(tag, [path]).get(path)
    if text is None:
        return jsonify({"error": "存档里读不到 %s (可能已删除)" % path}), 404
    return jsonify({"tag": tag, "path": path, "text": text[:400000], "truncated": len(text) > 400000})


@app.route("/api/research/new", methods=["POST"])
def api_research_new():
    """新建研究库: 校验名字后作为受管进程跑 C.RESEARCH_NEW_CMD (cwd = REPO_ROOT), 日志在进程页."""
    cmd_tpl = getattr(C, "RESEARCH_NEW_CMD", None)
    name = ((request.get_json(silent=True) or {}).get("name") or "").strip()
    if not cmd_tpl:
        return jsonify({"rc": 1, "out": "本面板未配置新建研究库命令"}), 400
    if not _RESEARCH_NAME_RE.match(name):
        return jsonify({"rc": 1, "out": "名字须是小写字母开头的小写字母/数字/下划线 (2-41 位)"}), 400
    return jsonify({"rc": 0, "cmd": cmd_tpl.format(name=name), "path": C.REPO_ROOT})


# ---------------- 路由: 升级 pin 页面化 (2026-10-29) ----------------
# 三步都调基座 pin 工具 (C.RESEARCH_PIN_CMD, 如 "python tools/research_pin.py"; cwd = REPO_ROOT), 页面不自己改研究库:
#   对照 trial <库> <sha> --execute       受管进程 (跑旧 / 新 pin 下本库测试 + import 判定), 报告落 <pin 检出根>/_trials/<库>/
#   提交 upgrade <库> <sha> --commit --execute   要求同一 HEAD 上 verdict same 的对照报告, 在研究库提交 (带 Base-Pin-* 尾注)
#   回退 revert <库> --execute            git revert 最近那次带尾注的升级提交 (仍是当前 pin 时)
# 登记册 base_pin 是基座 main 上的共享文件, 页面不改 (提示去 main 改).
_TRAILER_RE = re.compile(r"^Base-Pin-(Old|New): ([0-9a-f]{40})$", re.M)


def _research_repo_or_error(name):
    reg = _research_registry()
    if name not in reg:
        return None, "登记册里没有 %s" % name
    repo = _research_path(reg[name], name)
    if not repo or not os.path.isdir(os.path.join(repo, ".git")):
        return None, "本机没有研究库 %s" % name
    return repo, ""


def _trial_brief(t, pin, head):
    tests, imps = t.get("tests") or {}, t.get("imports") or {}

    def side(d, key):
        return {"ran": bool(d.get("ran")), "n": len(d.get(key) or []), "summary": d.get("summary") or d.get("note") or ""}

    return {"old": t.get("old", ""), "new": t.get("new", ""), "label": t.get("new_label", ""),
            "verdict": t.get("verdict", ""), "started": t.get("started", ""), "seconds": t.get("seconds"),
            "repo_head": t.get("repo_head", ""), "repo_dirty": bool(t.get("repo_dirty")),
            "tests": {"old": side(tests.get("old") or {}, "bad"), "new": side(tests.get("new") or {}, "bad"),
                      "only_new": tests.get("only_new") or [], "only_old": tests.get("only_old") or []},
            "imports": {"old": side(imps.get("old") or {}, "hits"), "new": side(imps.get("new") or {}, "hits"),
                        "only_new": imps.get("only_new") or [], "only_old": imps.get("only_old") or []},
            "usable": (t.get("verdict") == "same" and t.get("old") == pin and t.get("repo_head") == head
                       and not t.get("repo_dirty"))}


@app.route("/api/research/pin_state")
def api_research_pin_state():
    """升级 pin 区块: 可选的基座提交 (pin..main) + 本库的对照报告 (新到旧) + 最近一次可回退的升级提交."""
    name = request.args.get("name") or ""
    repo, err = _research_repo_or_error(name)
    if err:
        return jsonify({"error": err}), 404
    pin = _read_pin(repo)
    rc, head = run_git(repo, ["rev-parse", "HEAD"])
    head = head.strip() if rc == 0 else ""
    rc, st = run_git(repo, ["status", "--porcelain"])
    commits = []
    if pin:
        rc, out = run_git(C.REPO_ROOT, ["log", "--format=%H\t%cs\t%s", "-40", "%s..main" % pin])
        for line in (out.splitlines() if rc == 0 else []):
            parts = line.split("\t", 2)
            if len(parts) == 3:
                commits.append({"sha": parts[0], "date": parts[1], "subject": parts[2]})
    trials = []
    pins_root = getattr(C, "BASE_PINS_ROOT", None)
    tdir = os.path.join(pins_root, "_trials", name) if pins_root else ""
    if tdir and os.path.isdir(tdir):
        files = sorted((os.path.join(tdir, f) for f in os.listdir(tdir) if f.endswith(".json")),
                       key=os.path.getmtime, reverse=True)[:8]
        for f in files:
            try:
                with open(f, encoding="utf-8") as fh:
                    trials.append(_trial_brief(json.load(fh), pin, head))
            except (OSError, ValueError):
                continue
    last = None
    rc, out = run_git(repo, ["log", "-1", "--format=%H%n%cs %s%n%B", "--grep", "^Base-Pin-New: "])
    if rc == 0 and out.strip():
        lines = out.splitlines()
        tr = dict(_TRAILER_RE.findall("\n".join(lines[2:])))
        if "Old" in tr and "New" in tr:
            last = {"commit": lines[0].strip(), "line": lines[1].strip() if len(lines) > 1 else "",
                    "old": tr["Old"], "new": tr["New"], "revertable": tr["New"] == pin}
    return jsonify({"name": name, "pin": pin, "head": head, "dirty": bool(st.strip()), "commits": commits,
                    "trials": trials, "last_upgrade": last, "enabled": bool(getattr(C, "RESEARCH_PIN_CMD", None))})


def _pin_cmd(*args):
    return " ".join([C.RESEARCH_PIN_CMD] + list(args))


def _pin_args_or_error(b, need_rev=True):
    name, rev = (b.get("name") or "").strip(), (b.get("rev") or "").strip()
    if not getattr(C, "RESEARCH_PIN_CMD", None):
        return name, rev, "本面板未配置 pin 工具命令"
    _repo, err = _research_repo_or_error(name)
    if err:
        return name, rev, err
    if _is_archived(name):
        return name, rev, "%s 已归档, 不升级 pin (要恢复先在概览页 \"取消归档\")" % name
    if need_rev and not _SHA_ARG_RE.match(rev):
        return name, rev, "rev 须是提交 sha"
    return name, rev, ""


def _is_archived(name):
    return ((_research_registry().get(name) or {}).get("status") or "") == "archived"


@app.route("/api/research/archive", methods=["POST"])
def api_research_archive():
    """归档 / 取消归档 (设计稿 5.4): 登记册 status 是基座 main 上的共享文件, 由 pin 工具 archive 子命令在临时
    worktree 里改并 --ff-only 进 main (会过基座的提交门, 可能要几十秒) -- 校验后返回命令, 前端以受管进程启动."""
    b = request.get_json(silent=True) or {}
    name, unarchive = (b.get("name") or "").strip(), bool(b.get("unarchive"))
    if not getattr(C, "RESEARCH_PIN_CMD", None):
        return jsonify({"rc": 1, "out": "本面板未配置 pin 工具命令"}), 400
    if name not in _research_registry():
        return jsonify({"rc": 1, "out": "登记册里没有 %s" % name}), 400
    if _is_archived(name) != unarchive:
        return jsonify({"rc": 1, "out": "%s 已经%s" % (name, "是 active" if unarchive else "归档")}), 400
    args = ["archive", name] + (["--unarchive"] if unarchive else []) + ["--execute"]
    return jsonify({"rc": 0, "cmd": _pin_cmd(*args), "path": C.REPO_ROOT})


@app.route("/api/research/pin_trial", methods=["POST"])
def api_research_pin_trial():
    """对照: 校验后返回命令, 前端以受管进程启动 (同 "+ 新建研究库"); 跑完前端再读 pin_state."""
    name, rev, err = _pin_args_or_error(request.get_json(silent=True) or {})
    if err:
        return jsonify({"rc": 1, "out": err}), 400
    return jsonify({"rc": 0, "cmd": _pin_cmd("trial", name, rev, "--execute"), "path": C.REPO_ROOT})


def _run_pin_tool(*args):
    cmd, env = _child_cmd_env(_pin_cmd(*args))
    try:
        p = subprocess.run(cmd, cwd=C.REPO_ROOT, shell=True, capture_output=True, env=env, timeout=600)
    except (OSError, subprocess.TimeoutExpired) as e:
        return jsonify({"rc": 1, "out": "跑不了 pin 工具: %s" % e})
    return jsonify({"rc": p.returncode, "out": (p.stdout + p.stderr).decode("utf-8", "replace").strip()})


@app.route("/api/research/pin_upgrade", methods=["POST"])
def api_research_pin_upgrade():
    """提交升级: pin 工具自己查对照报告 (同一 HEAD、verdict same), 不满足就拒绝; 页面不给 --force."""
    name, rev, err = _pin_args_or_error(request.get_json(silent=True) or {})
    if err:
        return jsonify({"rc": 1, "out": err}), 400
    return _run_pin_tool("upgrade", name, rev, "--commit", "--execute")


@app.route("/api/research/pin_revert", methods=["POST"])
def api_research_pin_revert():
    name, _rev, err = _pin_args_or_error(request.get_json(silent=True) or {}, need_rev=False)
    if err:
        return jsonify({"rc": 1, "out": err}), 400
    return _run_pin_tool("revert", name, "--execute")


# ---------------- 路由: 基座总览 (2026-10-27; 设计稿 research_repos_design 5.5) ----------------
# pin 分布: 各研究库钉在哪个基座提交, 落后 main 多少, 过旧高亮 (落后 > PIN_STALE_COMMITS 个提交或 > PIN_STALE_DAYS 天).
# 影响预览: 选 main 上某次提交, 或某个基座 worktree 还没进 main 的改动 (main...HEAD + 未提交), 列出 import 了被改
# 模块的研究库 -- 基座开发者合并前就知道会影响谁. 判据同研究卡片的影响提示 (_import_hits, 只算 .py).

PIN_STALE_COMMITS = 50
PIN_STALE_DAYS = 30
_SHA_ARG_RE = re.compile(r"^[0-9a-fA-F]{7,40}$")


def _research_repos_here():
    """[(名字, 路径, HEAD)] -- 登记册里本机有的研究库."""
    out = []
    for name, entry in _research_registry().items():
        repo = _research_path(entry, name)
        if repo and os.path.isdir(os.path.join(repo, ".git")):
            rc, head = run_git(repo, ["rev-parse", "HEAD"])
            out.append((name, repo, head.strip() if rc == 0 else ""))
    return out


@app.route("/api/base_overview")
def api_base_overview():
    rc, main_line = run_git(C.REPO_ROOT, ["log", "-1", "--format=%h %cs %s", "main"])
    rc2, st = run_git(C.REPO_ROOT, ["status", "--porcelain=v1"])
    dirty = [l for l in st.splitlines() if l.strip()] if rc2 == 0 else []
    pins = []
    for name, entry in _research_registry().items():
        repo = _research_path(entry, name)
        exists = bool(repo) and os.path.isdir(os.path.join(repo, ".git"))
        pin = _read_pin(repo) if exists else ""
        row = {"name": name, "exists": exists, "pin": pin, "registered_pin": str((entry or {}).get("base_pin") or ""),
               "status": (entry or {}).get("status") or "active", "pin_date": "", "behind": None, "age_days": None,
               "stale": False}
        if pin:
            r1, d = run_git(C.REPO_ROOT, ["show", "-s", "--format=%cs", pin])
            row["pin_date"] = d.strip() if r1 == 0 else ""
            r2, n = run_git(C.REPO_ROOT, ["rev-list", "--count", "%s..main" % pin])
            row["behind"] = int(n.strip()) if r2 == 0 and n.strip().isdigit() else None
            try:
                row["age_days"] = (time.time() - time.mktime(time.strptime(row["pin_date"], "%Y-%m-%d"))) // 86400
            except ValueError:
                pass
            row["stale"] = row["status"] != "archived" and (
                (row["behind"] or 0) > PIN_STALE_COMMITS or (row["age_days"] or 0) > PIN_STALE_DAYS)
        pins.append(row)
    groups = {}
    for p in pins:
        if p["pin"]:
            groups.setdefault(p["pin"], []).append(p["name"])
    rc3, log = run_git(C.REPO_ROOT, ["log", "--format=%H %cs %s", "-30", "main"])
    commits = [{"sha": l[:40], "date": l[41:51], "subject": l[52:]} for l in log.splitlines() if len(l) > 52] \
        if rc3 == 0 else []
    pins_root = _norm(getattr(C, "BASE_PINS_ROOT", None) or "\0")     # pin 检出也是基座 worktree, 但不是开发线
    wts = [{"path": t.get("path"), "branch": t.get("branch") or "(detached)"} for t in list_worktrees()
           if _norm(t.get("path")) != _norm(C.REPO_ROOT) and not _norm(t.get("path")).startswith(pins_root)]
    return jsonify({"main": main_line.strip() if rc == 0 else "", "main_dirty": len(dirty),
                    "pins": pins, "pin_groups": [{"pin": k, "repos": v} for k, v in groups.items()],
                    "stale_commits": PIN_STALE_COMMITS, "stale_days": PIN_STALE_DAYS,
                    "commits": commits, "worktrees": wts})


def _changed_files(rev=None, path=None):
    """(改动的基座文件, 说明). rev: main 上某次提交 (rev^..rev); path: 某个基座 worktree 未进 main 的改动."""
    if rev:
        if not _SHA_ARG_RE.match(rev):
            return None, "rev 须是提交号"
        rc, out = run_git(C.REPO_ROOT, GIT_NOQUOTE + ["show", "--name-only", "--format=", rev])
        if rc != 0:
            return None, "找不到提交 %s" % rev
        return [l.strip() for l in out.splitlines() if l.strip()], "提交 %s" % rev[:8]
    if path:
        known = {_norm(t.get("path")): t for t in list_worktrees()}
        if _norm(path) not in known:
            return None, "不是基座的 worktree: %s" % path
        files = set()
        rc, out = run_git(path, GIT_NOQUOTE + ["diff", "--name-only", "main...HEAD"])
        files.update(l.strip() for l in out.splitlines() if l.strip() and rc == 0)
        rc, out = run_git(path, GIT_NOQUOTE + ["status", "--porcelain=v1"])
        for l in out.splitlines() if rc == 0 else []:
            if len(l) > 3:
                files.add(l[3:].split(" -> ")[-1].strip().strip('"'))
        return sorted(files), "worktree %s 未进 main 的改动 (提交 + 未提交)" % path
    return None, "给 rev 或 path"


@app.route("/api/impact_preview")
def api_impact_preview():
    changed, label = _changed_files(request.args.get("rev") or None, request.args.get("path") or None)
    if changed is None:
        return jsonify({"error": label}), 400
    py = [f for f in changed if f.endswith(".py")]
    repos = []
    for name, repo, head in _research_repos_here():
        hits = _import_hits(repo, head, py)
        soft = _import_soft(repo, head, py, hits)
        repos.append({"name": name, "hits": sorted({"%s  <- %s" % (f, m) for m, f in hits}), "soft": soft,
                      "level": _impact_level(hits, soft)})
    repos.sort(key=lambda r: ("!^o".index(r["level"]), r["name"]))
    return jsonify({"label": label, "changed": changed, "n_py": len(py), "repos": repos,
                    "affected": [r["name"] for r in repos if r["hits"]],
                    "maybe": [r["name"] for r in repos if r["level"] == "^"]})


# ---------------- 路由: 晋升队列 (2026-10-27, H-106; 设计稿 research_repos_design 4.4 / 5.4 / 5.5) ----------------
# 数据 = 基座跨线请求台账 (tools/harness/requests.py list --json): 研究库在自己 main 上提交 requests/<名>.jsonl,
# kind=promote 是成果晋升. 队列在 "基座开发" 页签 (晋升优先, 再按等待天数); 研究库详情列本库的请求并可 "发起晋升"
# (只写并提交本库的请求文件, 不改基座). 处理 (ack / close / decline) 与落地仍在基座 worktree 里做, 页面只给命令.

_ALLREQ = {"t": 0.0, "v": None}
_PROMOTE_ARTIFACTS = ("factor", "dataset", "tool", "finding", "signal", "other")


def _requests_tool():
    return os.path.join(C.REPO_ROOT, "tools", "harness", "requests.py")


def _run_requests(args, cwd, timeout=60):
    env = dict(os.environ, PYTHONIOENCODING="utf-8")     # 管道输出默认按 GBK 编, 不设中文会乱
    return subprocess.run([sys.executable, _requests_tool()] + list(args), cwd=cwd, capture_output=True,
                          timeout=timeout, env=env)


def _all_requests(force=False):
    """(全部请求, 错误); 要扫 git refs 与各研究库, 60 秒内复用."""
    if force or _ALLREQ["v"] is None or time.time() - _ALLREQ["t"] > 60:
        try:
            p = _run_requests(["list", "--json"], C.REPO_ROOT)
            if p.returncode != 0:
                raise ValueError((p.stderr or p.stdout).decode("utf-8", "replace").strip()[-300:])
            rows = json.loads(p.stdout.decode("utf-8-sig", "replace") or "null")
            _ALLREQ["v"] = (rows if isinstance(rows, list) else [], "")
        except (OSError, subprocess.TimeoutExpired, ValueError) as e:
            _ALLREQ["v"] = ([], "读不了跨线请求台账: %s" % e)
        _ALLREQ["t"] = time.time()
    return _ALLREQ["v"]


def _land_paths(rid):
    """(worktree 路径, 分支名) -- worktree 开在基座检出旁边, 与现有 wt-* 同级."""
    wt = os.path.join(os.path.dirname(os.path.abspath(C.REPO_ROOT)), "wt-" + rid.replace("_", "-")).replace("\\", "/")
    return wt, "land/" + rid.replace("_", "-")


def _queue_item(r):
    """队列条目 + 基座侧落地命令."""
    rid = r.get("id") or ""
    wt, _branch = _land_paths(rid)
    r = dict(r)
    r["land_wt"] = wt if os.path.isdir(wt) else ""
    r["land"] = [
        "python tools/harness/requests.py ack %s --note \"...\"" % rid,
        "git worktree add --detach %s main   # 在 worktree 里按契约落地, 测试 + 门全过后 git merge --ff-only 进 main" % wt,
        "python tools/harness/requests.py close %s --commit <落地提交>   # 或 decline %s --reason \"...\"" % (rid, rid),
    ]
    return r


@app.route("/api/promotion")
def api_promotion():
    reqs, err = _all_requests(force=request.args.get("refresh") == "1")
    research = set(_research_registry())
    open_ = [r for r in reqs if r.get("status") in ("open", "acked")]
    open_.sort(key=lambda r: (r.get("kind") != "promote", -(r.get("age_days") or 0), r.get("id") or ""))
    return jsonify({"error": err, "queue": [_queue_item(r) for r in open_],
                    "n_promote": sum(r.get("kind") == "promote" for r in open_),
                    "n_research": sum(r.get("from") in research for r in open_),
                    "n_overdue": sum(bool(r.get("overdue")) for r in open_)})


_RID_RE = re.compile(r"^[a-z][a-z0-9_]{0,40}-\d{1,6}$")


def _land_brief(r, wt, branch):
    """落地会话的任务书 (写进该 worktree 的 git 目录, 不在工作区里, 不会被误提交)."""
    rid = r.get("id")
    main = C.REPO_ROOT.replace("\\", "/")
    fields = ["id: %s" % rid, "from: %s" % r.get("from"), "kind: %s" % r.get("kind"),
              "title: %s" % r.get("title"), "evidence: %s" % (r.get("evidence_ref") or "")]
    if r.get("kind") == "promote":
        fields += ["artifact: %s" % r.get("artifact"), "target (基座落点): %s" % r.get("target")]
    if r.get("paths"):
        fields.append("paths: %s" % ", ".join(r.get("paths")))
    return "\n".join([
        "# 落地跨线请求 %s" % rid, "",
        "dashboard 晋升队列 \"一键处理\" 生成 (%s). 本会话在基座 worktree `%s` (分支 `%s`, 从 main 开出)."
        % (time.strftime("%Y-%m-%d %H:%M"), wt, branch),
        "", "## 请求", ""] + ["- " + f for f in fields] + [
        "",
        "## 步骤 (设计稿 docs/reports/research_repos_design_20261016.md 4.4: 晋升由基座侧在 worktree 落地后 --ff-only 进 main)",
        "",
        "1. 先读请求的依据 (研究库在 `%s/<库>/`, 只读, 不要改研究库) 与相关基座文档 (CLAUDE.md 文档优先;"
        " 因子 / Barra 先读 docs/skills/dev/factor_dev.md), 判断能不能接。不能接 -> 跳到第 5 步 decline。"
        % (getattr(C, "RESEARCH_ROOT", "") or "").replace("\\", "/"),
        "2. 接: 在基座检出 `%s` (main 分支, 台账写者只能是 main) 跑"
        " `python tools/harness/requests.py ack %s --note \"...\"`, 只提交 requests/main.jsonl"
        " (`git commit -m ... -- requests/main.jsonl`, 别带上 main 检出里别的改动)。" % (main, rid),
        "3. 在本 worktree 按契约落地: 代码复用 factors.generators / core.paths, 注册 (factor_registry.json /"
        " derived_data.yaml / AVAILABLE_FACTORS 按需), 测试放 tests/, 前视审计, 文档同步 (docs/maintenance/change_policy.md)。"
        " 研究库代码不拷贝, 按基座规范重写或移植。",
        "4. 门全过后提交, 在基座检出 `git merge --ff-only %s`, 先验 `git rev-parse main` 等于本分支 HEAD 再继续"
        " (main 常被别的会话推进: ff 失败就在本分支 rebase main 后重来)。" % branch,
        "5. 在基座检出 `python tools/harness/requests.py close %s --commit <落地提交>`"
        " (或 `decline %s --reason \"...\"`), 同样只提交 requests/main.jsonl。" % (rid, rid),
        "6. 收尾: `git worktree remove %s` 与 `git branch -d %s`; 报告写 Docs updated: yes/no。" % (wt, branch),
        "",
    ])


@app.route("/api/promotion/land", methods=["POST"])
def api_promotion_land():
    """一键处理 (设计稿 5.4): 给某条未结请求开基座 worktree (分支 land/<id>, 从 main) + 写任务书, 返回在那里起
    Claude 会话的命令; 前端开终端标签跑它. ack / 落地 / ff / close 由会话按任务书做, 页面不写台账、不碰 main 检出.
    worktree 已存在 (之前点过) -> 不重开, 只刷新任务书."""
    rid = ((request.get_json(silent=True) or {}).get("id") or "").strip()
    if not _RID_RE.match(rid):
        return jsonify({"rc": 1, "out": "id 不合规"}), 400
    reqs, err = _all_requests(force=True)
    r = next((q for q in reqs if q.get("id") == rid), None)
    if r is None:
        return jsonify({"rc": 1, "out": err or "台账里没有 %s" % rid}), 400
    if r.get("status") not in ("open", "acked"):
        return jsonify({"rc": 1, "out": "%s 已结 (%s)" % (rid, r.get("status"))}), 400
    wt, branch = _land_paths(rid)
    created = False
    if not os.path.isdir(wt):
        rc, out = run_git(C.REPO_ROOT, ["worktree", "add", "-b", branch, wt, "main"])
        if rc != 0:
            return jsonify({"rc": rc, "out": "开 worktree 失败: " + out}), 500
        created = True
    rc, gd = run_git(wt, ["rev-parse", "--absolute-git-dir"])
    if rc != 0:
        return jsonify({"rc": 1, "out": "%s 不是 git worktree: %s" % (wt, gd)}), 500
    brief = os.path.join(gd.strip(), "LAND_BRIEF.md")
    with open(brief, "w", encoding="utf-8") as f:
        f.write(_land_brief(r, wt, branch))
    b = brief.replace("\\", "/")
    cmd = 'claude "Read %s and carry out that request landing step by step; confirm with me before step 2 (ack)."' % b
    return jsonify({"rc": 0, "path": wt, "branch": branch, "brief": b, "created": created, "cmd": cmd})


@app.route("/api/research/requests")
def api_research_requests():
    name = request.args.get("name") or ""
    reqs, err = _all_requests(force=request.args.get("refresh") == "1")
    mine = [r for r in reqs if r.get("from") == name]
    mine.sort(key=lambda r: (r.get("status") not in ("open", "acked"), -(r.get("age_days") or 0)))
    return jsonify({"error": err, "requests": mine})


@app.route("/api/research/promote", methods=["POST"])
def api_research_promote():
    """发起晋升: 在研究库 main 上 requests.py open --kind promote, 再只提交 requests/<名>.jsonl (过本库研究档位门).
    研究库工作区别的改动不碰 (git commit -- <该文件>)."""
    b = request.get_json(silent=True) or {}
    name = (b.get("name") or "").strip()
    reg = _research_registry()
    if name not in reg:
        return jsonify({"rc": 1, "out": "登记册里没有 %s" % name}), 400
    repo = _research_path(reg[name], name)
    if not repo or not os.path.isdir(os.path.join(repo, ".git")):
        return jsonify({"rc": 1, "out": "本机没有研究库 %s" % name}), 400
    if _is_archived(name):
        return jsonify({"rc": 1, "out": "%s 已归档, 不再发起晋升" % name}), 400
    artifact = (b.get("artifact") or "").strip()
    if artifact not in _PROMOTE_ARTIFACTS:
        return jsonify({"rc": 1, "out": "成果类型须是 %s" % "/".join(_PROMOTE_ARTIFACTS)}), 400
    args = ["open", "--kind", "promote", "--artifact", artifact,
            "--target", (b.get("target") or "").strip(), "--title", (b.get("title") or "").strip(),
            "--evidence", (b.get("evidence") or "").strip()]
    for p in b.get("paths") or []:
        if str(p).strip():
            args += ["--path", str(p).strip()]
    try:
        p = _run_requests(args, repo)
    except (OSError, subprocess.TimeoutExpired) as e:
        return jsonify({"rc": 1, "out": "跑不了 requests.py: %s" % e}), 500
    out = (p.stdout + p.stderr).decode("utf-8", "replace").strip()
    if p.returncode != 0:
        return jsonify({"rc": p.returncode, "out": out})
    m = re.search(r"已写 (\S+) open", out)
    rid = m.group(1) if m else "?"
    rel = "requests/%s.jsonl" % name
    rc1, o1 = run_git(repo, ["add", "--", rel])
    rc2, o2 = run_git(repo, ["commit", "-m", "requests: open %s (promote %s)" % (rid, artifact), "--", rel])
    _ALLREQ["v"] = None
    if rc1 != 0 or rc2 != 0:
        return jsonify({"rc": 1, "out": out + "\n\n已写但提交失败 (研究库里手工提交 %s):\n%s%s" % (rel, o1, o2)})
    return jsonify({"rc": 0, "id": rid, "out": out + "\n" + o2})


# ---------------- 路由: 知识 (知识页签, 2026-10-26, 知识库改造第 6 步; 数据层 knowledge.py) ----------------
# 只读: 结论 / 复现漏斗 / 因子知识 / 教训 / 待处理 / 全局搜索. 写入 (accept / set-state) 仍走基座工具, 页面只给命令.

_REQ_CACHE = {"t": 0.0, "v": None}


def _kn_repo():
    return getattr(C, "KNOWLEDGE_REPO", None)


def _kn_requests(repo):
    """跨线请求要扫 git refs, 60 秒内复用."""
    if _REQ_CACHE["v"] is None or time.time() - _REQ_CACHE["t"] > 60:
        _REQ_CACHE["v"] = KN.open_requests(repo)
        _REQ_CACHE["t"] = time.time()
    return _REQ_CACHE["v"]


@app.route("/api/knowledge/<view>")
def api_knowledge(view):
    repo = _kn_repo()
    if not repo:
        return jsonify({"configured": False})
    catalog = getattr(C, "REPORT_CATALOG", None)
    if view == "conclusions":
        return jsonify(KN.conclusions(repo, include_history=request.args.get("history") == "1"))
    if view == "conclusion":
        d = KN.conclusion_detail(repo, request.args.get("id") or "")
        return (jsonify(d), 200) if d else (jsonify({"error": "没有这条结论"}), 404)
    if view == "funnel":
        return jsonify(KN.funnel(repo, catalog))
    if view == "factors":
        return jsonify(KN.factors(repo))
    if view == "factor_archive":
        return jsonify({"files": KN.factor_archive(getattr(C, "TEST_RESULTS_DIR", None), request.args.get("name"))})
    if view == "lessons":
        return jsonify(KN.lessons(repo))
    if view == "lesson":
        d = KN.lesson_detail(repo, request.args.get("id") or "")
        return (jsonify(d), 200) if d else (jsonify({"error": "没有这条教训"}), 404)
    if view == "pending":
        return jsonify(KN.pending(repo, getattr(C, "RESEARCH_INBOX", None), requests_fn=_kn_requests))
    if view == "search":
        return jsonify(KN.search(repo, catalog, request.args.get("q") or ""))
    return jsonify({"error": "未知视图 %s" % view}), 404


@app.route("/api/knowledge/archive_file")
def api_knowledge_archive_file():
    """单因子测试归档里的文件 (图 / 报告); 只认归档目录内的相对路径."""
    full = KN.archive_file(getattr(C, "TEST_RESULTS_DIR", None), request.args.get("rel") or "")
    if not full:
        return jsonify({"error": "不在归档里"}), 404
    return send_file(full)


# ---------------- 路由: 实验注册表 ----------------

@app.route("/api/registry", methods=["GET"])
def api_registry_get():
    return jsonify(load_json(C.REGISTRY_FILE, []))


@app.route("/api/registry", methods=["POST"])
def api_registry_post():
    b = request.get_json(force=True)
    op = b.get("op", "add")
    data = load_json(C.REGISTRY_FILE, [])
    if op == "add":
        e = b.get("entry", {})
        nid = max([x.get("id", 0) for x in data], default=0) + 1
        e["id"] = nid
        data.append(e)
    elif op == "update":
        eid, fields = b.get("id"), b.get("fields", {})
        for x in data:
            if x.get("id") == eid:
                x.update(fields)
    elif op == "delete":
        data = [x for x in data if x.get("id") != b.get("id")]
    save_json(C.REGISTRY_FILE, data)
    return jsonify({"rc": 0, "count": len(data)})


# ---------------- 路由: 进程管理 ----------------
# 注册表落盘 (_proc_logs/procs.json): dashboard 重启后按 pid+create_time 认领仍在跑的进程,
# 序号续接不覆盖旧日志. 停止走 psutil 整树 (shell=True 的壳下真实工作进程一并处理).

PROCS = {}          # id -> {label,cmd,path,started_ts,ended_ts,rc,log,os_pid,create_time,
                    #        proc(Popen|None), logf, ps(psutil.Process|None), adopted, _pscache}
PROC_SEQ = {"n": 0}
PROC_LOCK = threading.Lock()
PROC_REG = os.path.join(C.PROC_LOG_DIR, "procs.json")
_PROC_PERSIST = ("label", "cmd", "path", "started_ts", "ended_ts", "rc",
                 "log", "os_pid", "create_time")


def _proc_save():
    rows = []
    for pid in sorted(PROCS):
        row = {k: PROCS[pid].get(k) for k in _PROC_PERSIST}
        row["id"] = pid
        rows.append(row)
    tmp = PROC_REG + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False, indent=1)
        os.replace(tmp, PROC_REG)
    except OSError:
        pass


def _ps_attach(os_pid, create_time):
    """按 pid 认领进程; create_time 不吻合视为 PID 已复用, 拒绝认领."""
    if psutil is None or not os_pid:
        return None
    try:
        ps = psutil.Process(os_pid)
        if create_time and abs(ps.create_time() - create_time) > 2.0:
            return None
        return ps
    except Exception:  # noqa: BLE001
        return None


def _proc_load():
    try:
        with open(PROC_REG, encoding="utf-8") as f:
            rows = json.load(f)
    except Exception:  # noqa: BLE001
        return
    for row in rows:
        pid = row.pop("id", None)
        if pid is None:
            continue
        p = {k: row.get(k) for k in _PROC_PERSIST}
        p.update({"proc": None, "logf": None, "ps": None, "adopted": True})
        if p["rc"] is None and p["ended_ts"] is None:            # 存盘时还在跑
            p["ps"] = _ps_attach(p["os_pid"], p["create_time"])
            if p["ps"] is None:                                  # 停机期间已结束, rc 无从得知
                p["ended_ts"] = -1
        PROCS[pid] = p
        PROC_SEQ["n"] = max(PROC_SEQ["n"], pid)


def _proc_poll(p):
    """刷新单条状态, 返回 'running'/'exited'/'lost'. 状态跃迁时记录结束时间并关日志句柄."""
    if p.get("ended_ts") == -1:
        return "lost"                                            # 停机期间结束, 结束时间/rc 未知
    if p.get("rc") is not None or p.get("ended_ts"):
        return "exited"
    if p.get("proc") is not None:
        rc = p["proc"].poll()
        if rc is None:
            return "running"
        p["rc"], p["ended_ts"] = rc, time.time()
    elif p.get("ps") is not None:
        try:
            alive = p["ps"].is_running()
        except Exception:  # noqa: BLE001
            alive = False
        if alive:
            return "running"
        p["ended_ts"] = time.time()                              # 认领的进程拿不到 rc
    else:
        return "lost"
    try:
        if p.get("logf"):
            p["logf"].close()
            p["logf"] = None
    except Exception:  # noqa: BLE001
        pass
    _proc_save()
    return "exited"


def _proc_tree(p):
    """当前进程树 (psutil.Process 列表). 按 pid 缓存对象, cpu_percent 才有上次采样基线."""
    root = p.get("ps")
    if root is None and p.get("proc") is not None and psutil is not None:
        try:
            root = p["ps"] = psutil.Process(p["proc"].pid)
        except Exception:  # noqa: BLE001
            return []
    if root is None:
        return []
    try:
        kids = root.children(recursive=True)
    except Exception:  # noqa: BLE001
        kids = []
    cache = p.setdefault("_pscache", {})
    tree, seen = [], set()
    for x in [root] + kids:
        seen.add(x.pid)
        tree.append(cache.setdefault(x.pid, x))
    for stale in [k for k in cache if k not in seen]:
        del cache[stale]
    return tree


def _proc_usage(p):
    """整树 CPU% / 内存MB. 首轮 cpu_percent 无基线返回 0, 下一轮 (2s 后) 起有意义."""
    cpu, mem = 0.0, 0
    for x in _proc_tree(p):
        try:
            cpu += x.cpu_percent(None)
            mem += x.memory_info().rss
        except Exception:  # noqa: BLE001
            pass
    return round(cpu, 1), round(mem / 1048576)


def _proc_kill_tree(p):
    """整树 terminate -> wait 5s -> kill 幸存者. 无 psutil 时退化为只杀壳."""
    victims = _proc_tree(p)
    if not victims:
        if p.get("proc") is not None and p["proc"].poll() is None:
            p["proc"].terminate()
        return
    for x in victims:
        try:
            x.terminate()
        except Exception:  # noqa: BLE001
            pass
    try:
        _, alive = psutil.wait_procs(victims, timeout=5)
        for x in alive:
            try:
                x.kill()
            except Exception:  # noqa: BLE001
                pass
    except Exception:  # noqa: BLE001
        pass


def _child_cmd_env(cmd):
    """受管进程的解释器与 PATH (2026-09-17). dashboard 由 start.bat 双击启动时 PATH 里没有 python (系统/用户
    PATH 无 python, 只有 conda 激活注入), 子进程 'python ...' 会报 "不是内部或外部命令"; lineage_pipeline 自己再
    spawn 的 'python -m ...' 同样死. 处理: 命令首词是裸 python 时换成本进程解释器, 并把解释器目录与 Scripts 前置到 PATH."""
    exe = sys.executable
    parts = cmd.strip().split(None, 1)
    if parts and parts[0].lower() in ("python", "python.exe"):
        cmd = '"%s"' % exe + (" " + parts[1] if len(parts) > 1 else "")
    env = dict(os.environ)
    pdir = os.path.dirname(exe)
    env["PATH"] = os.pathsep.join([pdir, os.path.join(pdir, "Scripts"), env.get("PATH", "")])
    return cmd, env


def _proc_start(cmd, path, label, force=False):
    if not force:
        dup = [pid for pid, p in sorted(PROCS.items())
               if p.get("cmd") == cmd and p.get("path") == path and _proc_poll(p) == "running"]
        if dup:
            return None, dup[-1]
    with PROC_LOCK:
        PROC_SEQ["n"] += 1
        pid = PROC_SEQ["n"]
    logpath = os.path.join(C.PROC_LOG_DIR, "proc_%d.log" % pid)
    logf = open(logpath, "wb")           # 二进制落盘: 子进程编码原样保留, 读端再猜
    flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)   # 不随 dashboard Ctrl+C 陪葬
    cmd_exec, env = _child_cmd_env(cmd)
    proc = subprocess.Popen(cmd_exec, cwd=path, shell=True, stdout=logf,
                            stderr=subprocess.STDOUT, creationflags=flags, env=env)
    p = {"label": label, "cmd": cmd, "path": path, "started_ts": time.time(),
         "ended_ts": None, "rc": None, "log": logpath, "os_pid": proc.pid,
         "create_time": None, "proc": proc, "logf": logf, "ps": None, "adopted": False}
    if psutil is not None:
        try:
            p["ps"] = psutil.Process(proc.pid)
            p["create_time"] = p["ps"].create_time()
        except Exception:  # noqa: BLE001
            pass
    PROCS[pid] = p
    _proc_save()
    return pid, None


@app.route("/api/proc/start", methods=["POST"])
def api_proc_start():
    b = request.get_json(force=True)
    path, cmd, label = b.get("path") or C.REPO_ROOT, b.get("cmd"), b.get("label") or b.get("cmd")
    if not cmd:
        return jsonify({"rc": 1, "out": "缺命令"}), 400
    pid, dup = _proc_start(cmd, path, label, force=bool(b.get("force")))
    if pid is None:
        return jsonify({"rc": 1, "dup": dup,
                        "out": "同命令已在 #%d 运行, 需 force 强制再跑" % dup}), 409
    return jsonify({"rc": 0, "id": pid})


def _fmt_ts(ts):
    return time.strftime("%m-%d %H:%M:%S", time.localtime(ts)) if ts and ts > 0 else ""


def _fmt_dur(sec):
    sec = int(sec)
    if sec < 60:
        return "%ds" % sec
    if sec < 3600:
        return "%dm%02ds" % (sec // 60, sec % 60)
    return "%dh%02dm" % (sec // 3600, sec % 3600 // 60)


@app.route("/api/proc/list")
def api_proc_list():
    out = []
    for pid, p in sorted(PROCS.items()):
        st = _proc_poll(p)
        row = {"id": pid, "label": p["label"], "cmd": p["cmd"], "path": p["path"],
               "status": st, "rc": p.get("rc"), "adopted": bool(p.get("adopted")),
               "started": _fmt_ts(p.get("started_ts")), "ended": _fmt_ts(p.get("ended_ts")),
               "dur": "", "cpu": None, "mem_mb": None}
        if st == "running":
            if p.get("started_ts"):
                row["dur"] = _fmt_dur(time.time() - p["started_ts"])
            if psutil is not None:
                row["cpu"], row["mem_mb"] = _proc_usage(p)
        elif st == "exited" and p.get("started_ts") and (p.get("ended_ts") or 0) > 0:
            row["dur"] = _fmt_dur(p["ended_ts"] - p["started_ts"])
        out.append(row)
    return jsonify(out)


@app.route("/api/proc/log")
def api_proc_log():
    pid = int(request.args.get("id", 0))
    p = PROCS.get(pid)
    if not p:
        return jsonify({"text": "(无此进程)"})
    if request.args.get("dl"):
        try:
            return send_file(p["log"], as_attachment=True,
                             download_name=os.path.basename(p["log"]))
        except Exception as e:  # noqa: BLE001
            return jsonify({"text": "(日志下载失败: %s)" % e}), 404
    tail = 20000
    try:
        size = os.path.getsize(p["log"])
        with open(p["log"], "rb") as f:
            if size > tail:
                f.seek(size - tail)
                data = f.read()
                cut = data.find(b"\n")                           # 掐掉可能截断的首行
                data = data[cut + 1:] if cut >= 0 else data
            else:
                data = f.read()
    except OSError:
        data = b""
    text = None
    for enc in ("utf-8", "gbk"):                                 # 子进程常见两种编码
        try:
            text = data.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        text = data.decode("utf-8", errors="replace")
    return jsonify({"text": text, "truncated": size > tail if data else False})


@app.route("/api/proc/stop", methods=["POST"])
def api_proc_stop():
    pid = int(request.get_json(force=True).get("id", 0))
    p = PROCS.get(pid)
    if p and _proc_poll(p) == "running":
        _proc_kill_tree(p)
    return jsonify({"rc": 0})


@app.route("/api/proc/clear", methods=["POST"])
def api_proc_clear():
    b = request.get_json(force=True) or {}
    for pid in [k for k, v in PROCS.items() if _proc_poll(v) != "running"]:
        p = PROCS.pop(pid)
        try:
            if p.get("logf"):
                p["logf"].close()
        except Exception:  # noqa: BLE001
            pass
        if b.get("del_logs"):
            try:
                os.remove(p["log"])
            except OSError:
                pass
    _proc_save()
    return jsonify({"rc": 0})


_proc_load()


# ---------------- 路由: 数据管理可视化 ----------------

# ---------------- 数据页 "机器" 切换 (2026-10-29): 别的机器 (如 .22) 的数据情况, 只读 ----------------
# 远端数据包由 remote_fetch.py (受管进程) 在那台机器上跑 remote_snapshot.py 后拉回 _remote_<key>.json; 下面几个数据页
# 接口带 ?machine=<key> 时读这个包, 不带 (或 local) 时照旧现算本机. 远端不给任何写操作 (不跑管线 / 复检).
_REMOTE_CACHE = {}       # key -> (mtime, bundle)


def _machines():
    return {m["key"]: m for m in getattr(C, "REMOTE_DATA_MACHINES", None) or []}


def _remote_bundle_path(key):
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "_remote_%s.json" % key)


def _remote_bundle(key):
    """(包, 错误). 按文件 mtime 缓存."""
    if key not in _machines():
        return None, "未配置机器 %s" % key
    p = _remote_bundle_path(key)
    try:
        mt = os.path.getmtime(p)
    except OSError:
        return None, ""
    hit = _REMOTE_CACHE.get(key)
    if not hit or hit[0] != mt:
        try:
            with open(p, encoding="utf-8") as f:
                hit = (mt, json.load(f))
        except (OSError, ValueError) as e:
            return None, "数据包读不了: %s" % e
        _REMOTE_CACHE[key] = hit
    b = dict(hit[1])
    b["fetched_age_s"] = int(time.time() - mt)
    return b, ""


def _machine_arg():
    m = (request.args.get("machine") or "").strip()
    return "" if m in ("", "local") else m


@app.route("/api/data/machines")
def api_data_machines():
    out = [{"key": "local", "label": "本机", "data_root": C.DATA_ROOT}]
    for k, m in _machines().items():
        b, err = _remote_bundle(k)
        meta = (b or {}).get("meta") or {}
        out.append({"key": k, "label": m["label"], "data_root": m["data_root"], "ssh": m["ssh"],
                    "generated_at": meta.get("generated_at"), "fetched_age_s": (b or {}).get("fetched_age_s"),
                    "error": err})
    return jsonify({"machines": out})


@app.route("/api/data/remote/refresh", methods=["POST"])
def api_data_remote_refresh():
    """拉远端数据包: 受管进程跑 remote_fetch.py (推脚本 -> 远端只读跑 -> 拉回), 日志在进程页."""
    key = ((request.get_json(silent=True) or {}).get("machine") or "").strip()
    if key not in _machines():
        return jsonify({"rc": 1, "out": "未配置机器 %s" % key}), 400
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "remote_fetch.py").replace("\\", "/")
    pid, dup = _proc_start('python "%s" --machine %s' % (script, key), C.REPO_ROOT,
                           "数据包 @ " + _machines()[key]["label"])
    if pid is None:
        return jsonify({"rc": 1, "dup": dup, "out": "已在 #%d 拉取中" % dup}), 409
    return jsonify({"rc": 0, "id": pid})


def _remote_or_error(key):
    b, err = _remote_bundle(key)
    if b is None:
        return None, (jsonify({"missing": True, "machine": key, "error": err}), 200)
    return b, None


@app.route("/api/data/overview")
def api_data_overview():
    """页面骨架: 三组概览卡 + 目录计数 + store_v2 版本. 明细走 /api/data/layer。"""
    mk = _machine_arg()
    if mk:
        b, resp = _remote_or_error(mk)
        if resp:
            return resp
        meta = b.get("meta") or {}
        return jsonify({"machine": mk, "data_root": meta.get("data_root"), "counts": b.get("counts") or [],
                        "store_v2_version": b.get("store_v2_version") or "", "groups": b.get("groups") or [],
                        "lineage": meta.get("lineage_available", True), "content_scanned_at": "",
                        "meta": meta, "runs": b.get("runs") or {}, "fetched_age_s": b.get("fetched_age_s")})
    root = C.DATA_ROOT
    counts = []
    for label, rel, suf in C.DIR_COUNTS:
        d = os.path.join(root, rel.replace("/", os.sep))
        n = 0
        if os.path.isdir(d):
            n = sum(1 for x in os.listdir(d) if x.endswith(suf))
        counts.append({"label": label, "rel": rel, "count": n})
    # store_v2 激活版本 (junction 目标)
    sv = os.path.join(root, "factors", "store_v2")
    version = ""
    try:
        if os.path.exists(sv):
            tgt = os.path.realpath(sv)
            version = os.path.basename(tgt.rstrip("\\/"))
    except OSError:
        pass
    cache = DL.load_content_cache()
    return jsonify({"data_root": root, "counts": counts, "store_v2_version": version,
                    "groups": DL.group_summary(root),
                    "lineage": DL.lineage_available(),
                    "content_scanned_at": cache.get("scanned_at", "")})


@app.route("/api/data/layer")
def api_data_layer():
    """某一组 (external / intermediate / product) 的数据集明细。"""
    group = request.args.get("group", "external")
    if group not in {k for k, _ in DL.GROUPS}:
        return jsonify({"error": "bad group"}), 400
    mk = _machine_arg()
    if mk:
        b, resp = _remote_or_error(mk)
        if resp:
            return resp
        ov = b.get("overview") or {}
        return jsonify({"rows": [r for r in ov.get("rows") or [] if r.get("group") == group],
                        "scanned_at": "", "judged_at": ov.get("judged_at", "")})
    return jsonify(DL.overview(C.DATA_ROOT, group))


@app.route("/api/data/content-scan", methods=["POST"])
def api_data_content_scan():
    """把内容级复检作为 managed process 拉起 (重活不占 web 线程, 日志可查)。"""
    group = (request.get_json(silent=True) or {}).get("group") or ""
    cmd = "python tools/stream_dashboard/data_layers.py --scan"
    label = "数据内容级复检"
    if group in {k for k, _ in DL.GROUPS}:
        cmd += " --group " + group
        label += " " + group
    pid, dup = _proc_start(cmd, C.REPO_ROOT, label)
    if pid is None:
        return jsonify({"rc": 1, "dup": dup,
                        "out": "复检已在 #%d 运行" % dup}), 409
    return jsonify({"rc": 0, "id": pid})


# 血缘点亮图 (2026-10-15): 快照由 lineage_snapshot.py 以 managed process 生成, 页面只读文件。
# 快照放 dashboard 自己的目录 (同 _data_content_cache.json), 不进 git。
LINEAGE_SNAPSHOT = getattr(C, "LINEAGE_SNAPSHOT",
                           os.path.join(os.path.dirname(os.path.abspath(__file__)), "_lineage_snapshot.json"))


@app.route("/api/data/lineage")
def api_data_lineage():
    """血缘快照 (节点 / wave / 上游 / strict 判定 / 内容终点 / 最近运行)。没有快照时返回 {missing: true}。"""
    mk = _machine_arg()
    if mk:
        b, resp = _remote_or_error(mk)
        if resp:
            return resp
        snap = dict(b.get("lineage") or {})
        snap["snapshot_age_s"] = b.get("fetched_age_s")
        snap["machine"] = mk
        return jsonify(snap)
    try:
        with open(LINEAGE_SNAPSHOT, encoding="utf-8") as f:
            snap = json.load(f)
    except (OSError, ValueError):
        return jsonify({"missing": True, "path": LINEAGE_SNAPSHOT})
    try:
        snap["snapshot_age_s"] = int(time.time() - os.path.getmtime(LINEAGE_SNAPSHOT))
    except OSError:
        pass
    return jsonify(snap)


@app.route("/api/data/lineage/refresh", methods=["POST"])
def api_data_lineage_refresh():
    """在指定检出 (日更跑在哪就选哪, 默认主目录) 里重算快照; 重活走 managed process。"""
    path = (request.get_json(silent=True) or {}).get("path") or C.REPO_ROOT
    if not os.path.isdir(path):
        return jsonify({"rc": 1, "out": "路径不存在: %s" % path}), 400
    # 脚本用 dashboard 自己这份 (目标检出可能还没有它), 血缘/判据/历史则来自 cwd=path 那个检出
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lineage_snapshot.py")
    cmd = 'python "%s" --out "%s"' % (script.replace("\\", "/"), LINEAGE_SNAPSHOT.replace("\\", "/"))
    feats = os.path.join(C.REPO_ROOT, "results", "v6.3_specific_return_1d", "models", "round23_meta.json")
    if os.path.exists(feats):              # 模型特征清单不在 git 里, 目标检出可能没有; 用主目录那份高亮
        cmd += ' --features "%s"' % feats.replace("\\", "/")
    pid, dup = _proc_start(cmd, path, "血缘快照 (点亮图) @ " + os.path.basename(path.rstrip("\\/")))
    if pid is None:
        return jsonify({"rc": 1, "dup": dup, "out": "快照已在 #%d 生成中" % dup}), 409
    return jsonify({"rc": 0, "id": pid})


# ---------------- 会话恢复 (claude 持久化 transcript) ----------------

USER_HOME = os.path.expanduser("~")
CLAUDE_PROJECTS_DIR = os.path.join(USER_HOME, ".claude", "projects")

# session id 必须长这样才允许拼进 shell 命令 / onclick (防注入, 源头校验)
_ID_RE = re.compile(r"^[0-9a-fA-F-]{8,64}$")
# 跳过"模型选择"类 token (codex 首条常是 gpt-5.5 / opus 等, 非真实 prompt)
_MODEL_TOK = re.compile(r"^(gpt|o\d|claude|opus|sonnet|haiku|codex|gemini)[\w.\-]*$", re.I)


def _safe_id(sid):
    """只放行形如 UUID/hex 的 id, 否则返回空串 (调用方据此跳过)."""
    return sid if (sid and _ID_RE.match(sid)) else ""


def claude_project_dir(path):
    """worktree 路径 -> claude transcript 目录 (~/.claude/projects/<编码路径>).

    claude 把工作目录里非字母数字的字符都替换成 '-' 作为项目目录名,
    例: F:\\zx\\multifactors_beta -> F--zx-multifactors-beta.
    盘符大小写可能不一致, 故先精确命中, 不中再做大小写不敏感解析.
    """
    enc = re.sub(r"[^A-Za-z0-9]", "-", os.path.abspath(path))
    cand = os.path.join(CLAUDE_PROJECTS_DIR, enc)
    if os.path.isdir(cand):
        return cand
    encl = enc.casefold()
    try:
        for d in os.listdir(CLAUDE_PROJECTS_DIR):
            if d.casefold() == encl:
                return os.path.join(CLAUDE_PROJECTS_DIR, d)
    except OSError:
        pass
    return cand


def _session_meta(fp, info):
    """读 jsonl 头部取会话名(summary)+首条用户消息预览, 只扫前 80 行保持轻量."""
    try:
        with open(fp, encoding="utf-8", errors="replace") as f:
            for i, line in enumerate(f):
                if i > 80:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(r, dict):
                    continue
                t = r.get("type")
                if t == "summary" and not info["name"]:
                    info["name"] = (r.get("summary") or "")[:60]
                if not info["preview"] and t == "user":
                    m = r.get("message") or {}
                    c = m.get("content")
                    txt = ""
                    if isinstance(c, str):
                        txt = c
                    elif isinstance(c, list):
                        for b in c:
                            if isinstance(b, dict) and b.get("type") == "text":
                                txt = b.get("text", "")
                                break
                    txt = (txt or "").strip()
                    # 跳过工具结果 / caveat / slash 命令噪声, 取第一条真人提问
                    if txt and txt[0] not in "<[" and not txt.startswith("Caveat"):
                        info["preview"] = txt.replace("\n", " ")[:90]
                if info["name"] and info["preview"]:
                    break
    except OSError:
        pass


@app.route("/api/sessions")
def api_sessions():
    """列出某 worktree 已保存的 claude 会话 (供窗口关闭后恢复)."""
    path = request.args.get("path")
    if not path or not os.path.isdir(path):
        return jsonify({"dir": "", "sessions": []})
    pdir = claude_project_dir(path)
    out = []
    if os.path.isdir(pdir):
        for fn in os.listdir(pdir):
            if not fn.endswith(".jsonl"):
                continue
            sid = _safe_id(fn[:-6])
            if not sid:
                continue
            fp = os.path.join(pdir, fn)
            try:
                st = os.stat(fp)
            except OSError:
                continue
            info = {"id": sid, "ts": st.st_mtime,
                    "mtime": time.strftime("%m-%d %H:%M", time.localtime(st.st_mtime)),
                    "size_kb": round(st.st_size / 1024), "name": "", "preview": ""}
            _session_meta(fp, info)
            out.append(info)
    out.sort(key=lambda x: x["ts"], reverse=True)
    return jsonify({"dir": pdir, "sessions": out})


@app.route("/api/agents")
def api_agents():
    """claude 原生后台 agent 列表 (claude agents --json, 不挂终端的后台会话)."""
    try:
        p = subprocess.run("claude agents --json", cwd=C.REPO_ROOT, shell=True,
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=20)
        body = (p.stdout or "").strip()
        data = json.loads(body) if body.startswith("[") else []
        return jsonify({"ok": True, "agents": data, "raw": "" if data else body[:400]})
    except Exception as e:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(e), "agents": []})


# ---------------- 会话恢复 (codex rollout) ----------------

CODEX_SESSIONS_DIR = os.path.join(USER_HOME, ".codex", "sessions")


def _norm_path(p):
    """统一路径用于比较: 大小写折叠 + 统一斜杠. codex 存反斜杠, git 给正斜杠.
    空串直接返回空 (否则 abspath('') 会变成 dashboard 自身 cwd, 造成误归属)."""
    if not p:
        return ""
    try:
        return os.path.normcase(os.path.abspath(p)).replace("\\", "/")
    except Exception:  # noqa: BLE001
        return (p or "").replace("\\", "/").lower()


def _codex_session_brief(fp):
    """读 codex rollout 头部, 取 session_meta(id/cwd) + 首条真人 prompt 预览."""
    sid = cwd = ""
    preview = ""
    try:
        with open(fp, encoding="utf-8", errors="replace") as f:
            for i, line in enumerate(f):
                if i > 60:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(r, dict):
                    continue
                t = r.get("type")
                pl = r.get("payload")
                if not isinstance(pl, dict):
                    pl = {}
                if t == "session_meta":
                    sid = pl.get("id", "")
                    cwd = pl.get("cwd", "")
                elif t == "event_msg" and pl.get("type") == "user_message" and not preview:
                    msg = (pl.get("message") or "").strip()
                    # 跳过注入(# / <)与"模型选择"token(gpt-5.5/opus...), 短问候保留
                    if msg and msg[0] not in "#<" and not (_MODEL_TOK.match(msg) and " " not in msg):
                        preview = msg.replace("\n", " ")[:90]
                if sid and preview:
                    break
    except OSError:
        return None
    sid = _safe_id(sid)
    return {"id": sid, "cwd": cwd, "preview": preview} if sid else None


@app.route("/api/codex_sessions")
def api_codex_sessions():
    """列出某 worktree 的 codex 会话 (rollout 文件按 cwd 过滤)."""
    path = request.args.get("path")
    if not path or not os.path.isdir(path):
        return jsonify({"dir": CODEX_SESSIONS_DIR, "sessions": []})
    target = _norm_path(path)
    out = []
    if os.path.isdir(CODEX_SESSIONS_DIR):
        for root, _dirs, files in os.walk(CODEX_SESSIONS_DIR):
            for fn in files:
                if not (fn.startswith("rollout-") and fn.endswith(".jsonl")):
                    continue
                fp = os.path.join(root, fn)
                br = _codex_session_brief(fp)
                if not br:
                    continue
                nc = _norm_path(br["cwd"])
                # 精确命中本 worktree, 或在其子目录下启动的会话 (用分隔符防止 _old 误配)
                if not nc or not (nc == target or nc.startswith(target + "/")):
                    continue
                try:
                    st = os.stat(fp)
                except OSError:
                    continue
                out.append({"id": br["id"], "preview": br["preview"], "name": "",
                            "mtime": time.strftime("%m-%d %H:%M", time.localtime(st.st_mtime)),
                            "ts": st.st_mtime, "size_kb": round(st.st_size / 1024)})
    out.sort(key=lambda x: x["ts"], reverse=True)
    return jsonify({"dir": CODEX_SESSIONS_DIR, "sessions": out})


# ---------------- 稳定会话阅读 (transcript -> chat messages) ----------------

def _clip_text(s, n=20000):
    s = (s or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if len(s) <= n:
        return s
    return s[:n] + "\n\n[truncated]"


def _content_text(content, include_tools=False):
    """Extract human-readable text from Claude/Codex message content blocks."""
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        return _content_text(content.get("content") or content.get("text") or "", include_tools)
    if not isinstance(content, list):
        return ""
    parts = []
    for b in content:
        if isinstance(b, str):
            parts.append(b)
            continue
        if not isinstance(b, dict):
            continue
        typ = b.get("type")
        if typ in ("text", "input_text", "output_text"):
            parts.append(b.get("text", ""))
        elif include_tools and typ == "tool_use":
            parts.append("[tool] %s" % (b.get("name") or "tool_use"))
        elif include_tools and typ == "tool_result":
            parts.append("[tool result]\n%s" % _content_text(b.get("content"), include_tools))
    return "\n".join(p for p in parts if p)


def _append_msg(out, role, text, ts="", meta=None):
    text = _clip_text(text)
    if not text:
        return
    item = {"role": role, "text": text, "ts": ts or ""}
    if meta:
        item.update(meta)
    if out and out[-1]["role"] == item["role"] and out[-1]["text"] == item["text"]:
        return
    out.append(item)


def _claude_session_path(path, sid):
    sid = _safe_id(sid)
    if not sid or not path or not os.path.isdir(path):
        return ""
    fp = os.path.join(claude_project_dir(path), sid + ".jsonl")
    return fp if os.path.isfile(fp) else ""


def _codex_session_path(path, sid):
    sid = _safe_id(sid)
    if not sid or not path or not os.path.isdir(path):
        return ""
    target = _norm_path(path)
    if not os.path.isdir(CODEX_SESSIONS_DIR):
        return ""
    for root, _dirs, files in os.walk(CODEX_SESSIONS_DIR):
        for fn in files:
            if not (fn.startswith("rollout-") and fn.endswith(".jsonl")):
                continue
            fp = os.path.join(root, fn)
            br = _codex_session_brief(fp)
            if not br or br["id"] != sid:
                continue
            nc = _norm_path(br["cwd"])
            if nc == target or nc.startswith(target + "/"):
                return fp
    return ""


def _parse_claude_transcript(fp, include_tools=False):
    messages, meta = [], {"title": "", "cwd": "", "model": ""}
    try:
        with open(fp, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(r, dict):
                    continue
                typ = r.get("type")
                if typ == "ai-title" and not meta["title"]:
                    meta["title"] = r.get("aiTitle", "")
                    continue
                if typ == "summary" and not meta["title"]:
                    meta["title"] = r.get("summary", "")
                    continue
                if typ not in ("user", "assistant") or r.get("isMeta") or r.get("isSidechain"):
                    continue
                msg = r.get("message") or {}
                if not isinstance(msg, dict):
                    continue
                role = msg.get("role") or typ
                if role not in ("user", "assistant"):
                    continue
                meta["cwd"] = meta["cwd"] or r.get("cwd", "")
                meta["model"] = meta["model"] or msg.get("model", "")
                text = _content_text(msg.get("content"), include_tools)
                _append_msg(messages, role, text, r.get("timestamp", ""))
    except OSError:
        pass
    return meta, messages


def _parse_codex_transcript(fp, include_tools=False):
    messages, meta = [], {"title": "", "cwd": "", "model": "", "cli_version": ""}
    try:
        with open(fp, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(r, dict):
                    continue
                typ = r.get("type")
                ts = r.get("timestamp", "")
                pl = r.get("payload")
                if not isinstance(pl, dict):
                    pl = {}
                if typ == "session_meta":
                    meta["cwd"] = pl.get("cwd", "") or meta["cwd"]
                    meta["cli_version"] = pl.get("cli_version", "") or meta["cli_version"]
                    meta["model"] = pl.get("model", "") or meta["model"]
                elif typ == "event_msg" and pl.get("type") == "user_message":
                    msg = (pl.get("message") or "").strip()
                    if msg and msg[0] not in "#<" and not (_MODEL_TOK.match(msg) and " " not in msg):
                        _append_msg(messages, "user", msg, ts)
                elif typ == "response_item":
                    ptyp = pl.get("type")
                    if ptyp == "message" and pl.get("role") == "assistant":
                        text = _content_text(pl.get("content"), include_tools)
                        _append_msg(messages, "assistant", text, ts, {"phase": pl.get("phase", "")})
                    elif include_tools and ptyp == "function_call":
                        _append_msg(messages, "tool", "%s\n%s" % (pl.get("name", "tool"), pl.get("arguments", "")), ts)
                    elif include_tools and ptyp == "function_call_output":
                        _append_msg(messages, "tool", pl.get("output", ""), ts)
    except OSError:
        pass
    return meta, messages


@app.route("/api/session_transcript")
def api_session_transcript():
    """Render saved Claude/Codex JSONL into stable chat messages, independent of TUI redraw."""
    kind = (request.args.get("kind") or "").lower()
    sid = request.args.get("id") or ""
    path = request.args.get("path") or ""
    include_tools = request.args.get("tools") == "1"
    try:
        limit = max(20, min(800, int(request.args.get("limit") or 300)))
    except ValueError:
        limit = 300
    if kind == "claude":
        fp = _claude_session_path(path, sid)
        meta, messages = _parse_claude_transcript(fp, include_tools) if fp else ({}, [])
    elif kind == "codex":
        fp = _codex_session_path(path, sid)
        meta, messages = _parse_codex_transcript(fp, include_tools) if fp else ({}, [])
    else:
        return jsonify({"ok": False, "error": "unsupported kind", "messages": []})
    if not fp:
        return jsonify({"ok": False, "error": "session not found", "messages": []})
    total = len(messages)
    return jsonify({"ok": True, "kind": kind, "id": sid, "path": fp, "meta": meta,
                    "total": total, "shown": min(total, limit),
                    "truncated": total > limit, "messages": messages[-limit:]})


# ---------------- 产出查看器 (实验输出: docx/png/pdf/html/csv/parquet...) ----------------

_IMG_EXT = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".svg", ".ico"}
_TABLE_EXT = {".csv", ".parquet", ".pq", ".xlsx", ".xls"}
_TEXT_EXT = {".txt", ".log", ".json", ".yaml", ".yml", ".md", ".markdown",
             ".py", ".js", ".css", ".html", ".htm", ".csv", ".tsv", ".ini", ".cfg", ".toml"}


def _artifact_kind(ext):
    ext = ext.lower()
    if ext in _IMG_EXT:
        return "image"
    if ext == ".pdf":
        return "pdf"
    if ext == ".docx":
        return "docx"
    if ext in (".html", ".htm"):
        return "html"
    if ext in (".md", ".markdown"):
        return "md"
    if ext in _TABLE_EXT:
        return "table"
    if ext in _TEXT_EXT:
        return "text"
    return "binary"


def _artifact_root(key):
    return next((r for r in C.ARTIFACT_ROOTS if r["key"] == key), None)


def _stream_doc_classes():
    return list(getattr(C, "ARTIFACT_STREAM_CLASSES", []))


def _stream_doc_class(stream):
    return next((x for x in _stream_doc_classes() if x.get("stream") == stream), None)


def _name_matches_patterns(name, patterns):
    lname = name.lower()
    return any(fnmatch.fnmatch(lname, str(p).lower()) for p in patterns)


def _doc_stream_for_name(name):
    for cls in _stream_doc_classes():
        if _name_matches_patterns(name, cls.get("patterns") or []):
            return cls.get("stream")
    return "shared"


def _artifact_entry_visible(root, rel, name):
    patterns = root.get("patterns") or []
    if rel or not patterns:
        return True
    return _name_matches_patterns(name, patterns)


def _virtual_stream_doc_rel(root, rel):
    base = os.path.realpath(root["path"])
    clean = (rel or "").replace("\\", "/").strip("/")
    if not clean:
        return base
    parts = [p for p in clean.split("/") if p]
    if len(parts) < 2:
        return base
    stream, subrel = parts[0], "/".join(parts[1:])
    cls = _stream_doc_class(stream)
    if stream != "shared" and cls is None:
        return None
    top = parts[1]
    target = os.path.realpath(os.path.join(base, subrel))
    if target != base and not target.startswith(base + os.sep):
        return None
    if stream == "shared":
        if _doc_stream_for_name(top) != "shared":
            return None
    elif not _name_matches_patterns(top, cls.get("patterns") or []):
        return None
    return target


def _resolve_artifact(key, rel):
    """把 (root key, 相对路径) 安全解析为绝对路径; 防目录穿越. 返回 None 表示非法."""
    root = _artifact_root(key)
    if not root:
        return None
    if root.get("virtual") == "stream_docs":
        return _virtual_stream_doc_rel(root, rel)
    base = os.path.realpath(root["path"])
    target = os.path.realpath(os.path.join(base, rel or ""))
    if target != base and not target.startswith(base + os.sep):
        return None
    return target


@app.route("/api/artifacts/roots")
def api_artifact_roots():
    out = []
    for r in C.ARTIFACT_ROOTS:
        path = r.get("path", "")
        if r.get("virtual") == "stream_charts":
            exists = bool(_stream_chart_sources(r.get("stream", "")))
        else:
            exists = os.path.isdir(path)
        out.append({"key": r["key"], "label": r["label"],
                    "group": r.get("group", "未分类"),
                    "stream": r.get("stream", ""),
                    "task": r.get("task", ""),
                    "patterns": r.get("patterns", []),
                    "virtual": r.get("virtual", ""),
                    "exists": exists, "path": path})
    streams = list(getattr(C, "ARTIFACT_STREAMS", []))
    return jsonify({"streams": streams, "roots": out})


def _artifact_entry(fp, base, rel_prefix=""):
    try:
        st = os.stat(fp)
    except OSError:
        return None
    is_dir = os.path.isdir(fp)
    ext = "" if is_dir else os.path.splitext(os.path.basename(fp))[1].lower()
    rel = os.path.relpath(fp, base).replace("\\", "/")
    if rel_prefix:
        rel = rel_prefix.strip("/") + "/" + rel
    return {
        "name": os.path.basename(fp),
        "rel": rel,
        "is_dir": is_dir,
        "ext": ext,
        "kind": "dir" if is_dir else _artifact_kind(ext),
        "size": 0 if is_dir else st.st_size,
        "mtime": time.strftime("%Y-%m-%d %H:%M", time.localtime(st.st_mtime)),
    }


def _list_stream_docs(root, rel):
    base = os.path.realpath(root["path"])
    if not os.path.isdir(base):
        return jsonify({"error": "无效目录", "entries": []}), 400
    clean = (rel or "").replace("\\", "/").strip("/")
    if not clean:
        streams = _stream_doc_classes() + [{"stream": "shared", "label": "shared", "patterns": []}]
        entries = []
        names = os.listdir(base)
        for cls in streams:
            stream = cls["stream"]
            count = sum(1 for name in names if _doc_stream_for_name(name) == stream)
            if count <= 0:
                continue
            entries.append({
                "name": "%s (%d)" % (cls.get("label") or stream, count),
                "rel": stream,
                "is_dir": True,
                "ext": "",
                "kind": "dir",
                "size": 0,
                "mtime": "",
            })
        return jsonify({"rel": rel, "entries": entries})
    parts = clean.split("/")
    stream = parts[0]
    if stream != "shared" and _stream_doc_class(stream) is None:
        return jsonify({"error": "未知 stream", "entries": []}), 400
    if len(parts) > 1:
        target = _virtual_stream_doc_rel(root, clean)
        if target is None or not os.path.isdir(target):
            return jsonify({"error": "无效目录", "entries": []}), 400
        entries = []
        for name in os.listdir(target):
            fp = os.path.join(target, name)
            e = _artifact_entry(fp, base, rel_prefix=stream)
            if e:
                entries.append(e)
        entries.sort(key=lambda x: (not x["is_dir"], x["name"].lower()))
        return jsonify({"rel": rel, "entries": entries})
    entries = []
    for name in os.listdir(base):
        if _doc_stream_for_name(name) != stream:
            continue
        fp = os.path.join(base, name)
        e = _artifact_entry(fp, base, rel_prefix=stream)
        if e:
            entries.append(e)
    entries.sort(key=lambda x: (not x["is_dir"], x["name"].lower()))
    return jsonify({"rel": rel, "entries": entries})


def _stream_chart_sources(stream):
    """该 stream 名下所有非虚拟、且目录存在的 root, 作为图表递归扫描源."""
    out = []
    for src in C.ARTIFACT_ROOTS:
        if src.get("virtual") or src.get("stream") != stream:
            continue
        base = src.get("path")
        if base and os.path.isdir(base):
            out.append(src)
    return out


_CHART_MAX = 800  # 画廊最多收集多少张图, 防极端目录扫爆


def _list_stream_charts(root):
    """递归汇总该 stream 所有产出目录里的 png/jpg/svg, 拍平成一屏画廊.
    每条 entry 带 srcKey=来源 root, rel 相对该 root, 供 raw 端点逐张解析."""
    stream = root.get("stream", "")
    entries, seen = [], set()

    def add_image(src_key, fp, base_real):
        if len(entries) >= _CHART_MAX:
            return False
        rel = os.path.relpath(fp, base_real).replace("\\", "/")
        kk = (src_key, rel)
        if kk in seen:
            return True
        seen.add(kk)
        e = _artifact_entry(fp, base_real)
        if e:
            e["srcKey"] = src_key
            e["name"] = rel  # 显示相对路径, 区分不同实验下的同名图
            entries.append(e)
        return True

    for src in _stream_chart_sources(stream):
        base_real = os.path.realpath(src["path"])
        patterns = src.get("patterns") or []
        try:
            tops = os.listdir(base_real)
        except OSError:
            continue
        for top in sorted(tops):
            if patterns and not _name_matches_patterns(top, patterns):
                continue  # 尊重该 root 顶层 patterns, 维持 stream 归类
            tp = os.path.join(base_real, top)
            if os.path.isdir(tp):
                for dirpath, _dirs, files in os.walk(tp):
                    for fn in sorted(files):
                        if os.path.splitext(fn)[1].lower() in _IMG_EXT:
                            if not add_image(src["key"], os.path.join(dirpath, fn), base_real):
                                break
                    if len(entries) >= _CHART_MAX:
                        break
            elif os.path.splitext(top)[1].lower() in _IMG_EXT:
                add_image(src["key"], tp, base_real)
            if len(entries) >= _CHART_MAX:
                break
        if len(entries) >= _CHART_MAX:
            break

    entries.sort(key=lambda x: x["name"].lower())
    return jsonify({"rel": "", "entries": entries, "truncated": len(entries) >= _CHART_MAX})


@app.route("/api/artifacts/list")
def api_artifact_list():
    key = request.args.get("key", "")
    rel = request.args.get("rel", "")
    root = _artifact_root(key)
    if root and root.get("virtual") == "stream_charts":
        return _list_stream_charts(root)
    if root and root.get("virtual") == "stream_docs":
        return _list_stream_docs(root, rel)
    target = _resolve_artifact(key, rel)
    if target is None or not os.path.isdir(target):
        return jsonify({"error": "无效目录", "entries": []}), 400
    entries = []
    try:
        root = root or _artifact_root(key) or {}
        for name in os.listdir(target):
            if not _artifact_entry_visible(root, rel, name):
                continue
            fp = os.path.join(target, name)
            e = _artifact_entry(fp, _resolve_artifact(key, ""))
            if not e:
                continue
            entries.append(e)
    except OSError as e:
        return jsonify({"error": str(e), "entries": []}), 400
    entries.sort(key=lambda x: (not x["is_dir"], x["name"].lower()))
    return jsonify({"rel": rel, "entries": entries})


@app.route("/api/artifacts/raw")
def api_artifact_raw():
    """直出原始文件 (img/pdf/html/docx 下载或 iframe). 大文件浏览器自行处理."""
    target = _resolve_artifact(request.args.get("key", ""), request.args.get("rel", ""))
    if target is None or not os.path.isfile(target):
        return "not found", 404
    as_download = request.args.get("download") == "1"
    return send_file(target, as_attachment=as_download,
                     download_name=os.path.basename(target))


@app.route("/api/artifacts/text")
def api_artifact_text():
    target = _resolve_artifact(request.args.get("key", ""), request.args.get("rel", ""))
    if target is None or not os.path.isfile(target):
        return jsonify({"error": "not found"}), 404
    try:
        size = os.path.getsize(target)
        with open(target, "r", encoding="utf-8", errors="replace") as f:
            text = f.read(C.ARTIFACT_TEXT_MAX)
        return jsonify({"text": text, "truncated": size > C.ARTIFACT_TEXT_MAX, "size": size})
    except OSError as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/artifacts/table")
def api_artifact_table():
    """csv/parquet/xlsx -> 前 N 行 JSON 预览 (服务端 pandas)."""
    target = _resolve_artifact(request.args.get("key", ""), request.args.get("rel", ""))
    if target is None or not os.path.isfile(target):
        return jsonify({"error": "not found"}), 404
    ext = os.path.splitext(target)[1].lower()
    try:
        import pandas as pd  # 延迟导入, 保持 app 启动轻量
        nrows = C.ARTIFACT_TABLE_ROWS
        if ext in (".csv", ".tsv"):
            sep = "\t" if ext == ".tsv" else ","
            df = pd.read_csv(target, sep=sep, nrows=nrows)
            total = None
        elif ext in (".parquet", ".pq"):
            df = pd.read_parquet(target)
            total = len(df)
            df = df.head(nrows)
        elif ext in (".xlsx", ".xls"):
            df = pd.read_excel(target, nrows=nrows)
            total = None
        else:
            return jsonify({"error": "不支持的表格格式 %s" % ext}), 400
        df = df.reset_index() if df.index.name or isinstance(df.index, pd.MultiIndex) else df
        cols = [str(c) for c in df.columns]
        rows = df.astype(object).where(pd.notnull(df), None).values.tolist()
        rows = [[str(v) if v is not None else "" for v in row] for row in rows]
        return jsonify({"columns": cols, "rows": rows, "shown": len(rows),
                        "total": total, "truncated": (total is not None and total > nrows)})
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": "读取失败: %s" % e}), 400


# ---------------- 交互终端 (持久 PTY, 多终端) ----------------
#
# 设计: 每个终端 = 一个常驻 winpty 进程 + 回放缓冲 + 常驻 reader 线程, 登记在
# TERMS 注册表里. 浏览器 ws 连进来是"附着"到已存在的终端 (附着时先发回放缓冲
# 重绘屏幕); ws 断开只 detach 不 kill. 进程只在 shell 内 exit 或显式
# /api/terminal/close 时结束. => 刷新浏览器/断线不再杀会话 (但重启本服务会, 因
# winpty 子进程随父进程死).

TERM_SCROLLBACK = 200_000  # 每终端保留的回放字符数 (供重连后重绘)
_TID_RE = re.compile(r"^[0-9a-fA-F-]{8,64}$")

# 终端忙/等状态判定参数
BUSY_OUTPUT_SEC = 3.0      # 最近这么多秒内有输出 => 视为"正在产出"
CPU_BUSY_FRAC = 0.08       # 子树 CPU 占用超过此比例(单核当量) => 视为在干活
_INFRA_NAMES = {"conhost.exe", "winpty-agent.exe", "openconsole.exe"}  # shell 子树里的基础设施进程, 不算"干活"

TERMS = {}
TERMS_LOCK = threading.Lock()


class _NodePtyProc:
    """winpty.PtyProcess 接口兼容封装, 底层用 node-pty 桥接子进程 (新版 ConPTY)。

    暴露 _Term 用到的接口: pid / isalive() / read() / write() / setwinsize() / terminate()。
    => _Term 主体代码无需改动, 状态扫描 _scan_tree 仍按 pid(=真实 shell pid) 遍历进程树。
    桥接协议见 term-sidecar/pty-bridge.js。
    """

    def __init__(self, shell_cmd, cwd, cols=100, rows=30, env=None):
        args = [C.NODE_EXE, C.PTY_BRIDGE_JS, str(cols), str(rows), cwd,
                shell_cmd[0]] + list(shell_cmd[1:])
        self._popen = subprocess.Popen(
            args, cwd=C.TERM_SIDECAR_DIR,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            bufsize=0, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            env=env,                  # 桥接把 process.env 交给 shell (研究库终端带 PYTHONPATH)
        )
        self._dec = codecs.getincrementaldecoder("utf-8")("replace")
        self._shell_pid = None        # 桥接经 stderr 报告的真实 shell pid (供状态扫描)
        self._wlock = threading.Lock()
        self._outfd = self._popen.stdout.fileno()
        threading.Thread(target=self._drain_stderr, daemon=True).start()

    def _drain_stderr(self):
        try:
            for line in iter(self._popen.stderr.readline, b""):
                m = re.search(rb"pid=(\d+)", line)
                if m:
                    self._shell_pid = int(m.group(1))
        except Exception:  # noqa: BLE001
            pass

    @property
    def pid(self):
        return self._shell_pid or self._popen.pid

    def isalive(self):
        return self._popen.poll() is None

    def read(self, size=65536):
        try:
            data = os.read(self._outfd, size)  # 返回当前可用字节(阻塞至少1字节), 类似 winpty.read
        except OSError:
            raise EOFError
        if not data:
            raise EOFError
        return self._dec.decode(data)          # 增量 utf8 解码, 跨读边界不切断多字节

    @staticmethod
    def _frame(t, payload_bytes):
        return bytes([t]) + len(payload_bytes).to_bytes(4, "big") + payload_bytes

    def _send(self, frame):
        with self._wlock:
            try:
                self._popen.stdin.write(frame)
                self._popen.stdin.flush()
            except Exception:  # noqa: BLE001
                pass

    def write(self, s):
        self._send(self._frame(0x69, s.encode("utf-8", "replace")))

    def setwinsize(self, rows, cols):  # 与 winpty 签名一致: (rows, cols)
        self._send(self._frame(0x72, ("%d,%d" % (cols, rows)).encode("ascii")))

    def terminate(self, force=False):
        try:
            self._popen.stdin.close()  # 桥接收到 stdin EOF 会自行 kill shell
        except Exception:  # noqa: BLE001
            pass
        try:
            self._popen.terminate()
        except Exception:  # noqa: BLE001
            pass


class _Term:
    def __init__(self, tid, cwd, label):
        self.id = tid
        self.cwd = cwd
        self.label = label or (os.path.basename(cwd.rstrip("\\/")) or "term")
        # 研究库终端 (2026-10-29): cwd 在已登记研究库里 -> 新 shell 自带 PYTHONPATH = 研究库根;pin 检出,
        # 不用再手工 . ./env.ps1. 只在建终端时算一次: 升级 pin 后要新开终端才换.
        self.research, self.pythonpath, note = research_term_env(cwd)
        env = dict(os.environ, PYTHONPATH=self.pythonpath) if self.pythonpath else None
        if C.USE_NODE_PTY:
            self.proc = _NodePtyProc(C.SHELL, cwd, cols=100, rows=30, env=env)
        else:
            self.proc = winpty.PtyProcess.spawn(C.SHELL, cwd=cwd, dimensions=(30, 100), env=env)
        self.buf = ""
        if self.research:
            self.buf = "\x1b[90m[研究库 %s] %s\x1b[0m\r\n" % (
                self.research, ("PYTHONPATH=" + self.pythonpath + " (按 base.pin 设; 升级 pin 后新开终端才换)")
                if self.pythonpath else note)
        self.lock = threading.Lock()       # 保护 buf / ws 状态
        self.send_lock = threading.Lock()  # 串行化对 ws 的写, 防回放与实时帧交错
        self.ws = None
        self.alive = True
        self.created = time.time()
        self.last_out = time.monotonic()   # 最近一次 PTY 输出时刻 (判"正在产出")
        self._cpu_prev = None              # (wall, cpu_seconds) 上次采样, 用于算 CPU 占用
        threading.Thread(target=self._reader, daemon=True).start()

    def _reader(self):
        try:
            while self.alive and self.proc.isalive():
                try:
                    data = self.proc.read(65536)
                except (EOFError, Exception):  # noqa: BLE001
                    break
                if not data:
                    continue
                self.last_out = time.monotonic()
                with self.lock:
                    combined = self.buf + data
                    if len(combined) > TERM_SCROLLBACK:
                        # 截断回放缓冲时不能切在转义序列中途, 否则重连重放的首段会让 xterm
                        # 解析器从半截 CSI 开始 -> 顶部花屏. 把起点对齐到下一个 ESC 边界.
                        cut = len(combined) - TERM_SCROLLBACK
                        nxt = combined.find("\x1b", cut)
                        combined = combined[nxt if nxt != -1 else cut:]
                    self.buf = combined
                    w = self.ws
                if w is not None:
                    with self.send_lock:
                        try:
                            w.send(data)
                        except Exception:  # noqa: BLE001
                            pass
        finally:
            self.alive = False
            with self.lock:
                w = self.ws
            if w is not None:
                try:
                    w.close()
                except Exception:  # noqa: BLE001
                    pass

    def attach(self, ws):
        """绑定新 ws 并立即回放历史; 踢掉旧 viewer. 回放与实时帧靠 send_lock 串行."""
        with self.send_lock:
            with self.lock:
                old = self.ws
                self.ws = ws
                buf = self.buf
            try:
                ws.send(buf)
            except Exception:  # noqa: BLE001
                pass
        if old is not None and old is not ws:
            try:
                old.close()
            except Exception:  # noqa: BLE001
                pass

    def detach(self, ws):
        with self.lock:
            if self.ws is ws:
                self.ws = None

    def write(self, s):
        try:
            self.proc.write(s)
        except Exception:  # noqa: BLE001
            pass

    def resize(self, r):
        try:
            self.proc.setwinsize(int(r[1]), int(r[0]))
        except Exception:  # noqa: BLE001
            pass

    def _scan_tree(self):
        """遍历该终端 shell 进程子树, 返回 (agent类型, 子树CPU秒, 是否有干活子进程).
        agent: claude/codex/none — 看 cmdline; 干活: agent 有子进程, 或(无 agent 时)shell 有非基础设施子进程.
        """
        agent, cpu_sum, work = None, 0.0, False
        if psutil is None:
            return agent, None, work
        try:
            root = psutil.Process(self.proc.pid)
        except Exception:  # noqa: BLE001
            return agent, None, work
        try:
            procs = [root] + root.children(recursive=True)
        except Exception:  # noqa: BLE001
            procs = [root]
        agent_proc = None
        for p in procs:
            try:
                ct = p.cpu_times()
                cpu_sum += float(ct.user + ct.system)
            except Exception:  # noqa: BLE001
                pass
            if agent is None:
                try:
                    cl = " ".join(p.cmdline()).lower()
                except Exception:  # noqa: BLE001
                    cl = ""
                if "claude" in cl:
                    agent, agent_proc = "claude", p
                elif "codex" in cl:
                    agent, agent_proc = "codex", p
        try:
            if agent_proc is not None:
                work = len(agent_proc.children()) > 0      # agent 起了工具子进程 = 在干活
            else:
                for p in procs:
                    if p.pid != root.pid and p.name().lower() not in _INFRA_NAMES:
                        work = True
                        break
        except Exception:  # noqa: BLE001
            pass
        return agent, cpu_sum, work

    def status(self):
        """返回该终端的忙/等状态. state: working / waiting / idle / dead."""
        out_ago = round(time.monotonic() - self.last_out, 1)
        if not self.alive:
            return {"state": "dead", "agent": "none", "out_ago": out_ago, "cpu": None}
        recent_out = out_ago < BUSY_OUTPUT_SEC
        agent, cpu_sum, work = self._scan_tree()
        # CPU 占用 = 两次采样间 cpu 秒增量 / 墙钟增量 (单核当量)
        cpu_frac, now = None, time.monotonic()
        if cpu_sum is not None:
            if self._cpu_prev is not None:
                dw = now - self._cpu_prev[0]
                if dw > 0:
                    cpu_frac = max(0.0, (cpu_sum - self._cpu_prev[1]) / dw)
            self._cpu_prev = (now, cpu_sum)
        cpu_busy = cpu_frac is not None and cpu_frac > CPU_BUSY_FRAC
        if recent_out or work or cpu_busy:
            state = "working"
        elif agent:
            state = "waiting"   # agent 在, 但静默/低 CPU/无工具子进程 => 大概率等你回话 (启发式)
        else:
            state = "idle"      # 空 shell 停在提示符
        return {"state": state, "agent": agent or "none", "out_ago": out_ago,
                "cpu": round(cpu_frac, 2) if cpu_frac is not None else None}

    def kill(self):
        self.alive = False
        try:
            self.proc.terminate(force=True)
        except Exception:  # noqa: BLE001
            pass


def _get_term(tid):
    with TERMS_LOCK:
        t = TERMS.get(tid)
        if t and not t.alive:
            TERMS.pop(tid, None)
            t = None
        return t


@app.route("/api/terminals")
def api_terminals():
    """列出存活终端, 供浏览器刷新后重连. 顺手清理已死的."""
    want_status = request.args.get("status") == "1"
    with TERMS_LOCK:
        for k in [k for k, v in TERMS.items() if not v.alive]:
            TERMS.pop(k, None)
        terms = list(TERMS.values())
    items = []
    for t in terms:
        d = {"id": t.id, "cwd": t.cwd, "label": t.label, "created": int(t.created),
             "research": t.research, "pythonpath": t.pythonpath}
        if want_status:
            d.update(t.status())   # state / agent / out_ago / cpu
        items.append(d)
    return jsonify({"terminals": items})


@app.route("/api/terminal/close", methods=["POST"])
def api_terminal_close():
    tid = (request.get_json(silent=True) or {}).get("id", "")
    with TERMS_LOCK:
        t = TERMS.pop(tid, None)
    if t:
        t.kill()
    return jsonify({"ok": True})


@sock.route("/pty")
def pty(ws):
    tid = request.args.get("term") or ""
    cwd = request.args.get("cwd") or C.REPO_ROOT
    label = request.args.get("label") or ""
    is_new = request.args.get("new") == "1"
    if not os.path.isdir(cwd):
        cwd = C.REPO_ROOT
    if not _TID_RE.match(tid):
        return  # 非法 id, 直接关闭
    t = _get_term(tid)
    if t is None:
        if not is_new:
            return  # 客户端以为存在但已不在 (服务重启过) -> 关闭, 客户端会清理该标签
        t = _Term(tid, cwd, label)
        with TERMS_LOCK:
            TERMS[tid] = t
    t.attach(ws)
    try:
        while t.alive:
            msg = ws.receive()
            if msg is None:
                break
            try:
                obj = json.loads(msg)
            except (ValueError, TypeError):
                continue
            if "i" in obj:
                t.write(obj["i"])
            elif "r" in obj:
                t.resize(obj["r"])
    except Exception:  # noqa: BLE001
        pass
    finally:
        t.detach(ws)  # 注意: 只 detach, 不 kill -> 刷新/断线不杀会话


if __name__ == "__main__":
    print("Stream Dashboard v1.3 -> http://%s:%d  (repo: %s)" % (C.HOST, C.PORT, C.REPO_ROOT))
    app.run(host=C.HOST, port=C.PORT, threaded=True, debug=False)
