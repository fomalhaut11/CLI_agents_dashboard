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
    allowed = C.STREAM_FOLDER.get(branch)
    if allowed and f.startswith(allowed + "/"):
        return ("ok", "own", "")
    # 别的 stream 文件夹
    for b, folder in C.STREAM_FOLDER.items():
        if b != branch and f.startswith(folder + "/"):
            return ("violation", "wrong_stream", "属于 %s" % b)
    # 因子型 stream: 因子文件 OK
    if branch == C.FACTOR_STREAM:
        if f.startswith("factors/repository/") or f.startswith("tests/"):
            return ("ok", "factor", "")
    # 共享核心
    for pre in C.CORE_PREFIXES:
        if f.startswith(pre):
            return ("violation", "core", "共享核心, 应走 main")
    return ("warn", "unclassified", "未归类, 建议放 stream 文件夹")


def guard_report(path, branch):
    """该 worktree 相对 main 的改动 (已提交+工作区) 的约定检查."""
    files = set()
    rc, out = run_git(path, ["diff", "--name-only", "main...HEAD"])
    if rc == 0:
        files.update(l.strip() for l in out.splitlines() if l.strip())
    rc, out = run_git(path, ["status", "--porcelain=v1"])
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
    out = []
    for wt in list_worktrees():
        info = worktree_info(wt)
        info["meta"] = meta.get(info["branch"], {"status": "ACTIVE", "note": ""})
        out.append(info)
    active = sum(1 for x in out if x["branch"] != "main" and x["meta"]["status"] == "ACTIVE")
    return jsonify({"streams": out, "wip_active": active})


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

PROCS = {}          # id -> {proc, label, cmd, path, started, log}
PROC_SEQ = {"n": 0}
PROC_LOCK = threading.Lock()


@app.route("/api/proc/start", methods=["POST"])
def api_proc_start():
    b = request.get_json(force=True)
    path, cmd, label = b.get("path") or C.REPO_ROOT, b.get("cmd"), b.get("label") or b.get("cmd")
    if not cmd:
        return jsonify({"rc": 1, "out": "缺命令"}), 400
    with PROC_LOCK:
        PROC_SEQ["n"] += 1
        pid = PROC_SEQ["n"]
    logpath = os.path.join(C.PROC_LOG_DIR, "proc_%d.log" % pid)
    logf = open(logpath, "w", encoding="utf-8", errors="replace")
    proc = subprocess.Popen(cmd, cwd=path, shell=True, stdout=logf,
                            stderr=subprocess.STDOUT, text=True)
    PROCS[pid] = {"proc": proc, "label": label, "cmd": cmd, "path": path,
                  "started": time.strftime("%H:%M:%S"), "log": logpath, "logf": logf}
    return jsonify({"rc": 0, "id": pid})


@app.route("/api/proc/list")
def api_proc_list():
    out = []
    for pid, p in sorted(PROCS.items()):
        rc = p["proc"].poll()
        out.append({"id": pid, "label": p["label"], "cmd": p["cmd"], "path": p["path"],
                    "started": p["started"],
                    "status": "running" if rc is None else "exited",
                    "rc": rc})
    return jsonify(out)


@app.route("/api/proc/log")
def api_proc_log():
    pid = int(request.args.get("id", 0))
    p = PROCS.get(pid)
    if not p:
        return jsonify({"text": "(无此进程)"})
    try:
        with open(p["log"], encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        text = ""
    return jsonify({"text": text[-20000:]})


@app.route("/api/proc/stop", methods=["POST"])
def api_proc_stop():
    pid = int(request.get_json(force=True).get("id", 0))
    p = PROCS.get(pid)
    if p and p["proc"].poll() is None:
        p["proc"].terminate()
    return jsonify({"rc": 0})


@app.route("/api/proc/clear", methods=["POST"])
def api_proc_clear():
    for pid in [k for k, v in PROCS.items() if v["proc"].poll() is not None]:
        try:
            PROCS[pid]["logf"].close()
        except Exception:  # noqa: BLE001
            pass
        del PROCS[pid]
    return jsonify({"rc": 0})


# ---------------- 路由: 数据管理可视化 ----------------

@app.route("/api/data/overview")
def api_data_overview():
    root = C.DATA_ROOT
    now = time.time()
    datasets = []
    for label, rel, max_age in C.KEY_DATASETS:
        fp = os.path.join(root, rel.replace("/", os.sep))
        if os.path.exists(fp):
            st = os.stat(fp)
            age_d = (now - st.st_mtime) / 86400.0
            stale = (max_age is not None and age_d > max_age)
            datasets.append({"label": label, "rel": rel, "exists": True,
                             "mtime": time.strftime("%Y-%m-%d %H:%M", time.localtime(st.st_mtime)),
                             "age_days": round(age_d, 1), "size_mb": round(st.st_size / 1e6, 1),
                             "stale": stale, "max_age": max_age})
        else:
            datasets.append({"label": label, "rel": rel, "exists": False})
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
    return jsonify({"data_root": root, "datasets": datasets, "counts": counts,
                    "store_v2_version": version})


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

    def __init__(self, shell_cmd, cwd, cols=100, rows=30):
        args = [C.NODE_EXE, C.PTY_BRIDGE_JS, str(cols), str(rows), cwd,
                shell_cmd[0]] + list(shell_cmd[1:])
        self._popen = subprocess.Popen(
            args, cwd=C.TERM_SIDECAR_DIR,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            bufsize=0, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
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
        if C.USE_NODE_PTY:
            self.proc = _NodePtyProc(C.SHELL, cwd, cols=100, rows=30)
        else:
            self.proc = winpty.PtyProcess.spawn(C.SHELL, cwd=cwd, dimensions=(30, 100))
        self.buf = ""
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
        d = {"id": t.id, "cwd": t.cwd, "label": t.label, "created": int(t.created)}
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
