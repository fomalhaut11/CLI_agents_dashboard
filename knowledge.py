# -*- coding: utf-8 -*-
"""知识页签的数据层 (知识库改造第 6 步, docs/reports/knowledge_base_design_20261016.md 第 8 节).

只读. 每个函数显式拿路径 (由 config.py 给), 不 import 基座代码 -- 独立仓的面板也能用同一份.
来源:
  结论      <repo>/core/research/finding_ledger.jsonl        (现行 = 未被取代且未撤回, 同 research_ledger.current)
  复现      <repo>/core/research/replication_ledger.jsonl    + 研报库目录 catalog.jsonl (漏斗顶层)
  因子      <repo>/core/factors/meta/factor_registry.json    (test_results) + 复现台账评级 + 单因子测试归档
  教训      <repo>/core/research/lessons.jsonl               (未导入时为空, 页面提示)
  待处理    收件箱 <inbox>/<用户>/*.json 未入账的结论提案, 跨线请求 (tools/harness/requests.py list --open --json),
            provisional 结论, 带 reopen_if 的结论, severity=high 且只是 recorded 的教训
"""
import json
import os
import subprocess
import sys

LIST_MAX = 2000          # 单个列表最多回多少行 (前端自己筛)
SEARCH_MAX = 30          # 全局搜索每个来源最多回多少条


def load_jsonl(path):
    rows = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        pass
    return rows


def _ledger(repo, name):
    return os.path.join(repo, "core", "research", name)


def _current(rows):
    return [r for r in rows if not r.get("superseded_by") and r.get("status") != "retracted"
            and r.get("verdict") != "withdrawn"]


# ---------------------------------------------------------------- 结论
CONCLUSION_LIST_KEYS = ("id", "stream", "title", "verdict", "status", "created", "accepted", "direction",
                        "supersedes", "superseded_by", "evidence_level")


def conclusions(repo, include_history=False):
    rows = load_jsonl(_ledger(repo, "finding_ledger.jsonl"))
    shown = rows if include_history else _current(rows)
    out = [{k: r.get(k) for k in CONCLUSION_LIST_KEYS} for r in shown][:LIST_MAX]
    for o, r in zip(out, shown):
        o["reopen_if"] = bool(r.get("reopen_if"))
    return {"rows": out, "total": len(rows), "current": len(_current(rows))}


def conclusion_detail(repo, fid):
    rows = load_jsonl(_ledger(repo, "finding_ledger.jsonl"))
    by_id = {r.get("id"): r for r in rows}
    rec = by_id.get(fid)
    if rec is None:
        return None
    chain, seen, cur = [], set(), rec          # supersedes 链: 往前追到最早
    while cur and cur.get("id") not in seen:
        seen.add(cur.get("id"))
        chain.append({k: cur.get(k) for k in ("id", "title", "verdict", "status", "created")})
        prev = cur.get("supersedes")
        prev = prev[0] if isinstance(prev, list) and prev else prev
        cur = by_id.get(prev) if prev else None
    return {"record": rec, "chain": chain}


# ---------------------------------------------------------------- 复现漏斗
REPLICATION_KEYS = ("id", "batch", "report", "report_date", "factor", "category", "status", "rating",
                    "report_path", "rejected_reason", "match_pct")


def funnel(repo, catalog_path):
    rows = load_jsonl(_ledger(repo, "replication_ledger.jsonl"))
    catalog = load_jsonl(catalog_path) if catalog_path else []
    by_status = {}
    for r in rows:
        by_status[r.get("status") or "?"] = by_status.get(r.get("status") or "?", 0) + 1
    reports = {r.get("report") for r in rows if r.get("report")}
    linked = [r for r in rows if ((r.get("links") or {}).get("report"))]
    cat_by_id = {c.get("id"): c for c in catalog if c.get("id")}
    items = []
    for r in rows[:LIST_MAX]:
        o = {k: r.get(k) for k in REPLICATION_KEYS}
        rid = (r.get("links") or {}).get("report")
        o["report_id"] = rid
        o["report_title"] = (cat_by_id.get(rid) or {}).get("title") if rid else None
        items.append(o)
    return {"library": len(catalog), "library_found": bool(catalog), "attempted": len(rows),
            "reports_attempted": len(reports), "linked_rows": len(linked), "by_status": by_status, "rows": items}


# ---------------------------------------------------------------- 因子知识
METRIC_KEYS = ("ic_mean", "icir", "rank_ic_mean", "rank_icir", "long_short_annual", "long_short_sharpe")


def factors(repo):
    path = os.path.join(repo, "core", "factors", "meta", "factor_registry.json")
    try:
        with open(path, encoding="utf-8") as f:
            reg = json.load(f)
    except (OSError, ValueError):
        return {"rows": [], "found": False}
    if isinstance(reg, dict) and isinstance(reg.get("factors"), (dict, list)):
        reg = reg["factors"]
    items = reg.items() if isinstance(reg, dict) else [(x.get("name"), x) for x in reg]
    rating = {}
    for r in load_jsonl(_ledger(repo, "replication_ledger.jsonl")):
        if r.get("factor") and not r.get("superseded_by"):
            rating[r["factor"]] = r.get("rating")
    rows = []
    for name, v in items:
        if not isinstance(v, dict):
            continue
        tr = v.get("test_results") if isinstance(v.get("test_results"), dict) else {}
        row = {"name": name, "category": v.get("category"), "is_active": v.get("is_active"),
               "source_paper": v.get("source_paper"), "rating": rating.get(name) or v.get("linear_rating"),
               "tested": bool(tr)}
        row.update({k: tr.get(k) for k in METRIC_KEYS})
        rows.append(row)
    return {"rows": rows, "found": True}


def factor_archive(test_dir, name, limit=200):
    """单因子测试归档里属于该因子的文件 (<日期目录>/<因子名>_...), 新的在前."""
    if not test_dir or not os.path.isdir(test_dir) or not name:
        return []
    out = []
    try:
        dates = sorted((d for d in os.listdir(test_dir) if os.path.isdir(os.path.join(test_dir, d))), reverse=True)
    except OSError:
        return []
    prefix = name + "_"
    for d in dates:
        try:
            files = sorted(os.listdir(os.path.join(test_dir, d)))
        except OSError:
            continue
        for fn in files:
            if fn.startswith(prefix):
                out.append(d + "/" + fn)
                if len(out) >= limit:
                    return out
    return out


def archive_file(test_dir, rel):
    """归档内文件的绝对路径; 越出归档目录 -> None."""
    if not test_dir or not rel:
        return None
    base = os.path.realpath(test_dir)
    full = os.path.realpath(os.path.join(base, rel))
    if os.path.commonpath([base, full]) != base or not os.path.isfile(full):
        return None
    return full


# ---------------------------------------------------------------- 教训
LESSON_LIST_KEYS = ("id", "kind", "date", "title", "rule", "class", "severity", "state", "scope")


def lessons(repo):
    path = _ledger(repo, "lessons.jsonl")
    rows = load_jsonl(path)
    out = []
    for r in rows[:LIST_MAX]:
        o = {k: r.get(k) for k in LESSON_LIST_KEYS}
        o["alarm"] = r.get("severity") == "high" and r.get("state") == "recorded"
        o["enforcement"] = (r.get("enforcement") or {}).get("type")
        out.append(o)
    return {"rows": out, "found": os.path.isfile(path)}


def lesson_detail(repo, lid):
    for r in load_jsonl(_ledger(repo, "lessons.jsonl")):
        if r.get("id") == lid:
            return r
    return None


# ---------------------------------------------------------------- 待处理
def inbox_pending(repo, inbox_dir):
    """收件箱里 kind=finding 且还没入账的提案."""
    done = {str((r.get("source") or {}).get("proposal_id")) for r in load_jsonl(_ledger(repo, "finding_ledger.jsonl"))}
    out = []
    if not inbox_dir or not os.path.isdir(inbox_dir):
        return out
    for user in sorted(os.listdir(inbox_dir)):
        d = os.path.join(inbox_dir, user)
        if not os.path.isdir(d):
            continue
        for fn in sorted(os.listdir(d)):
            if not fn.endswith(".json"):
                continue
            try:
                with open(os.path.join(d, fn), encoding="utf-8") as f:
                    prop = json.load(f)
            except (OSError, ValueError):
                continue
            pid = str(prop.get("id") or fn[:-5])
            if prop.get("kind") != "finding" or pid in done:
                continue
            body = prop.get("body") if isinstance(prop.get("body"), dict) else prop
            out.append({"id": pid, "user": user, "stream": prop.get("stream") or body.get("stream"),
                        "title": body.get("title") or prop.get("title"), "verdict": body.get("verdict")})
    return out


def open_requests(repo, python=None, timeout=60):
    """跨线请求 (未结); 工具不在 / 出错 -> (None, 原因)."""
    tool = os.path.join(repo, "tools", "harness", "requests.py")
    if not os.path.isfile(tool):
        return None, "本仓没有 tools/harness/requests.py"
    try:
        p = subprocess.run([python or sys.executable, tool, "list", "--open", "--json"], cwd=repo,
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        return None, "跑不了 requests.py: %s" % e
    try:
        data = json.loads(p.stdout or "null")
    except ValueError:
        return None, (p.stderr or p.stdout or "").strip()[-300:]
    rows = data.get("requests") if isinstance(data, dict) else data
    keep = ("id", "from", "to", "kind", "status", "age_days", "overdue", "title")
    return [{k: r.get(k) for k in keep} for r in (rows or []) if isinstance(r, dict)], ""


def pending(repo, inbox_dir, requests_fn=open_requests):
    finds = load_jsonl(_ledger(repo, "finding_ledger.jsonl"))
    cur = _current(finds)
    reqs, req_err = requests_fn(repo)
    return {
        "inbox": inbox_pending(repo, inbox_dir),
        "requests": reqs or [], "requests_error": req_err,
        "provisional": [{k: r.get(k) for k in ("id", "stream", "title", "verdict", "created")}
                        for r in cur if r.get("status") == "provisional"],
        "reopen_if": [{"id": r.get("id"), "title": r.get("title"), "reopen_if": r.get("reopen_if")}
                      for r in cur if r.get("reopen_if")],
        "unguarded_high": [{k: r.get(k) for k in ("id", "title", "rule", "state")}
                           for r in load_jsonl(_ledger(repo, "lessons.jsonl"))
                           if r.get("severity") == "high" and r.get("state") == "recorded"],
    }


def research_counts(repo, inbox_dir):
    """研究页签卡片用: {研究线: {"findings": 已晋升现行结论数, "queued": 收件箱排队数}}."""
    out = {}
    for r in _current(load_jsonl(_ledger(repo, "finding_ledger.jsonl"))):
        out.setdefault(str(r.get("stream") or ""), {"findings": 0, "queued": 0})["findings"] += 1
    for p in inbox_pending(repo, inbox_dir):
        out.setdefault(str(p.get("stream") or ""), {"findings": 0, "queued": 0})["queued"] += 1
    return out


# ---------------------------------------------------------------- 全局搜索
def _hit(q, *fields):
    return any(q in str(f).lower() for f in fields if f)


def search(repo, catalog_path, q):
    q = (q or "").strip().lower()
    if not q:
        return {}
    finds = load_jsonl(_ledger(repo, "finding_ledger.jsonl"))
    reps = load_jsonl(_ledger(repo, "replication_ledger.jsonl"))
    less = load_jsonl(_ledger(repo, "lessons.jsonl"))
    facs = factors(repo)["rows"]
    cat = load_jsonl(catalog_path) if catalog_path else []
    return {
        "conclusions": [{"id": r.get("id"), "title": r.get("title"), "verdict": r.get("verdict"),
                         "status": r.get("status")}
                        for r in finds if _hit(q, r.get("id"), r.get("title"), r.get("question"), r.get("notes"))
                        ][:SEARCH_MAX],
        "replications": [{"id": r.get("id"), "factor": r.get("factor"), "report": r.get("report"),
                          "status": r.get("status"), "rating": r.get("rating")}
                         for r in reps if _hit(q, r.get("id"), r.get("factor"), r.get("report"), r.get("notes"))
                         ][:SEARCH_MAX],
        "factors": [{"name": r["name"], "category": r.get("category"), "rank_icir": r.get("rank_icir"),
                     "rating": r.get("rating")}
                    for r in facs if _hit(q, r["name"], r.get("source_paper"))][:SEARCH_MAX],
        "lessons": [{"id": r.get("id"), "title": r.get("title"), "state": r.get("state"),
                     "severity": r.get("severity")}
                    for r in less if _hit(q, r.get("id"), r.get("title"), r.get("rule"), r.get("root_cause"))
                    ][:SEARCH_MAX],
        "reports": [{"id": c.get("id"), "title": c.get("title"), "broker": c.get("broker"), "date": c.get("date"),
                     "path": c.get("path")}
                    for c in cat if _hit(q, c.get("id"), c.get("title"), c.get("filename"))][:SEARCH_MAX],
    }
