# -*- coding: utf-8 -*-
"""实盘跟踪页签的数据包 (2026-10-31) -- 在**跑实盘的那台机器上**跑 (现为 .22), 也可在本机对本机检出跑.

由 remote_fetch.py --live 单独 scp 到远端临时目录, 用远端解释器在远端代码检出 (--root) 上只读跑, 产出一个 JSON 拉回本机.
只读文件, 不 import 基座代码, 不写数据根 / 检出; 输出只落 --out. 几秒跑完 (不做血缘判定).

    account     模拟账户逐日 (results/v3ra/paper_account/account.csv) + state.json 摘要
    positions   模拟账户最新一天持仓 (positions/<D>.csv) 与成交 (trades/<D>.csv, 含状态计数); 近若干天成交状态计数
    paper       近若干个目标持仓日 (results/v3ra/paper/<D>/): summary.json 要点 + input_gate.json 判定;
                最新一天 holdings.csv 全表
    gate_latest 20:00 日更末尾的输入闸 (pipeline_runs/gate_latest/input_gate.json)
    lanes       各链路日志 (20:00 日更 / 07:30 清晨 / 09:25 竞价 / 09:28 开盘 30 分钟 / 22:00 分钟复刻):
                最近几次的时间 / 各步返回码 / 有无 END / 末尾若干行; 锁文件
    live        DATA_ROOT/live 下竞价快照 / open_today.pkl / 开盘 30 分钟 vwap30 的最新文件
    tasks       本机计划任务里我们的那几个 (zx_* / Codex_GS540*): 状态 / 上次运行与结果 / 下次运行 / 是否到点没触发;
                lanes 按链路对上任务名 (LANE_TASKS), 不存在记 missing
    tracking    多指数跟踪账户 (config/production.yaml `tracking`, 2026-11-01): 每个账户同 account / positions / paper
                三块 (目录 <root>/<账户>/account 与 /paper), 外加 _shared/<D>/run.json (当天各账户出持仓成败)
    calendar    最近若干交易日 (<= 今天) 各自有没有: 目标持仓 / 账户记账 / 竞价日志 / 清晨日志 / 日更日志
    meta        主机 / 生成时间 / 生产模型目录 (config/production.yaml)

    <解释器> live_snapshot.py --root <检出> --data-root <数据根> --runs-dir <日志目录> --out <json>
"""
import argparse
import csv
import glob
import json
import os
import re
import socket
import sys
import time

N_DAYS = 15            # 目标持仓 / 成交计数回看天数
N_LOGS = 7             # 每条链路回看几次日志
TAIL = 40              # 每份日志留末尾几行
# (键, 标签, 文件名前缀, 计划时间): 日志名 = <前缀><YYYYMMDD>[_HHMM].log, 见 tools/deploy/*_22.cmd
LANES = [
    ("daily", "20:00 日更 (wave 0-5 + 输入闸)", "daily_", "20:00"),
    ("morning", "07:30 清晨 (分钟线 + 出持仓 + 记账)", "morning_", "07:30"),
    ("auction", "09:25 集合竞价快照", "live_auction_", "09:25"),
    ("intraday", "09:28 开盘 30 分钟行情", "live_intraday_", "09:28"),
    ("min1", "22:00 当日分钟线复刻", "hf22_min1_", "22:00"),
    ("track", "08:10 多指数跟踪账户 (出持仓 + 记账)", "track_", "08:10"),
]
_RC_RE = re.compile(r"^(?:([A-Z][A-Z0-9_]*)_)?RC=(-?\d+)")
# 链路 -> Windows 计划任务名 (.22 上的注册见 tools/deploy/*_22.cmd 头注释). 任务没触发就没有日志, 只看日志会把
# "没跑" 显示成空格子; 这里直接读任务调度器的状态 / 上次结果 / 下次时间.
LANE_TASKS = {"daily": "zx_daily_pipeline", "morning": "zx_morning_minute", "auction": "zx_live_auction",
              "intraday": "zx_live_intraday", "min1": "zx_hf22_min1"}
TASK_OURS_RE = re.compile(r"^(zx_|Codex_GS540)", re.I)   # 同机同事的任务不列
_TASK_PS = (
    "[Console]::OutputEncoding=[Text.Encoding]::UTF8; "
    "@(Get-ScheduledTask | Where-Object { $_.TaskPath -eq '\\' } | ForEach-Object { $i = $_ | Get-ScheduledTaskInfo; "
    "[pscustomobject]@{name=$_.TaskName; state=[string]$_.State; "
    "action=(($_.Actions | ForEach-Object { ($_.Execute + ' ' + $_.Arguments).Trim() }) -join ' | '); "
    "last=$(if($i.LastRunTime -and $i.LastRunTime.Year -gt 2000){$i.LastRunTime.ToString('yyyy-MM-dd HH:mm:ss')}else{''}); "
    "next=$(if($i.NextRunTime){$i.NextRunTime.ToString('yyyy-MM-dd HH:mm:ss')}else{''}); "
    "result=[int64]$i.LastTaskResult; missed=[int]$i.NumberOfMissedRuns} }) | ConvertTo-Json -Depth 3 -Compress")
# LastTaskResult 里不算失败的值: 0 成功, 0x41301 正在运行, 0x41303 从未运行
TASK_RESULT_OK = {0: "成功", 0x41301: "正在运行", 0x41303: "从未运行"}


def _read_csv(path):
    try:
        with open(path, encoding="utf-8-sig", newline="") as f:
            return list(csv.DictReader(f))
    except OSError:
        return None


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _num(v):
    if v is None or v == "":
        return None
    try:
        x = float(v)
    except ValueError:
        return v
    return int(x) if x.is_integer() and "." not in v and "e" not in v.lower() else x


def _mtime(p):
    try:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(p)))
    except OSError:
        return None


def _prod_cfg(root):
    """config/production.yaml 里模型目录 / 持仓目录 / 账户目录; 读不到用默认值."""
    out = {"models_dir": None, "holdings_dir": "results/v3ra/paper", "out_dir": "results/v3ra/paper_account"}
    try:
        with open(os.path.join(root, "config", "production.yaml"), encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return out
    for k in out:
        m = re.search(r"^\s*%s:\s*([^\s#]+)" % k, text, re.M)
        if m:
            out[k] = m.group(1)
    return out


def _account(acc_dir):
    rows = _read_csv(os.path.join(acc_dir, "account.csv")) or []
    keep = ("day", "price_source", "equity_open", "nav", "cash", "market_value", "n_hold", "n_filled", "n_blocked",
            "cash_dividend", "buy_amount", "sell_amount", "fees", "turnover", "target_gap_l1", "corporate_actions",
            "holdings", "bench_close", "ret", "bench_ret", "excess", "nav_rel")
    rows = [{k: (r.get(k) if k in ("day", "price_source", "holdings") else _num(r.get(k))) for k in keep} for r in rows]
    st = _read_json(os.path.join(acc_dir, "state.json")) or {}
    state = {"last_day": st.get("last_day"), "cash": st.get("cash"), "n_shares": len(st.get("shares") or {}),
             "cfg": st.get("cfg")}
    return {"dir": acc_dir, "rows": rows, "state": state, "mtime": _mtime(os.path.join(acc_dir, "account.csv"))}


def _trade_counts(rows):
    c = {}
    for r in rows or []:
        k = "%s:%s" % (r.get("side") or "?", r.get("status") or "?")
        c[k] = c.get(k, 0) + 1
    return c


def _positions(acc_dir):
    pd_ = sorted(glob.glob(os.path.join(acc_dir, "positions", "*.csv")))
    td = sorted(glob.glob(os.path.join(acc_dir, "trades", "*.csv")))
    out = {"day": None, "positions": [], "trades": [], "trade_counts": {}, "trade_history": []}
    if pd_:
        out["day"] = os.path.basename(pd_[-1])[:8]
        out["positions"] = [{k: (v if k == "code" else _num(v)) for k, v in r.items()} for r in _read_csv(pd_[-1]) or []]
    if td:
        rows = _read_csv(td[-1]) or []
        out["trade_day"] = os.path.basename(td[-1])[:8]
        out["trades"] = [{k: (v if k in ("code", "side", "status") else _num(v)) for k, v in r.items()} for r in rows]
        out["trade_counts"] = _trade_counts(rows)
    for p in td[-N_DAYS:]:
        out["trade_history"].append({"day": os.path.basename(p)[:8], "counts": _trade_counts(_read_csv(p))})
    return out


def _gate_brief(g):
    if not isinstance(g, dict):
        return None
    opt = g.get("optimizer") or {}
    return {"day": g.get("day"), "ok": g.get("ok"), "n_features": g.get("n_features"), "n_blocking": g.get("n_blocking"),
            "blocking": g.get("blocking"), "allowed": g.get("allowed"), "last_dates": g.get("last_dates"),
            "features_ok": g.get("features_ok"), "optimizer_ok": opt.get("ok"),
            "optimizer_blocking": opt.get("blocking"), "optimizer_detail": opt.get("detail")}


def _paper(hold_dir):
    days = sorted(d for d in os.listdir(hold_dir) if re.match(r"^\d{8}$", d)) if os.path.isdir(hold_dir) else []
    keep = ("day", "models", "round", "source", "row_filter", "n_hold", "weight_sum", "te_ex_ante", "turnover",
            "score_cov", "n_relaxed", "n_fail", "n_model_features", "n_stocks", "all_nan_on_day",
            "model_feature_nan_share", "input_gate_allowed", "raw_problems")
    out = []
    for d in days[-N_DAYS:]:
        p = os.path.join(hold_dir, d)
        s = _read_json(os.path.join(p, "summary.json")) or {}
        out.append({"dir": d, "summary": {k: s.get(k) for k in keep}, "has_holdings": os.path.exists(os.path.join(p, "holdings.csv")),
                    "gate": _gate_brief(_read_json(os.path.join(p, "input_gate.json"))),
                    "mtime": _mtime(os.path.join(p, "holdings.csv")) or _mtime(p)})
    latest = []
    if days:
        latest = [{k: (v if k == "code" else _num(v)) for k, v in r.items()}
                  for r in _read_csv(os.path.join(hold_dir, days[-1], "holdings.csv")) or []]
    return {"dir": hold_dir, "days": out, "latest_day": days[-1] if days else None, "latest_holdings": latest}


def _log(path):
    rcs, ended, lines = {}, False, []
    with open(path, "rb") as fh:
        for raw in fh:
            line = raw.decode("utf-8", "replace").rstrip()
            if not line.strip():
                continue
            lines.append(line)
            s = line.strip()
            m = _RC_RE.match(s)
            if m:
                rcs[(m.group(1) or "RC").lower()] = int(m.group(2))
            elif s.startswith("END"):
                ended = True
    st = os.stat(path)
    return {"name": os.path.basename(path), "mtime": _mtime(path), "size_kb": st.st_size // 1024, "rcs": rcs,
            "ended": ended, "tail": lines[-TAIL:], "first": lines[0][:200] if lines else ""}


def _lanes(runs_dir):
    out = []
    for key, label, prefix, at in LANES:
        files = sorted(glob.glob(os.path.join(runs_dir, prefix + "[0-9]*.log")), reverse=True)[:N_LOGS]
        logs = []
        for p in files:
            try:
                logs.append(_log(p))
            except OSError:
                continue
        out.append({"key": key, "label": label, "prefix": prefix, "at": at, "logs": logs})
    lock = os.path.join(runs_dir, "daily.lock")
    lk = None
    if os.path.exists(lock):
        try:
            with open(lock, encoding="utf-8", errors="replace") as f:
                who = f.read().strip()[:80]
        except OSError:
            who = ""
        lk = {"mtime": _mtime(lock), "who": who}
    skipped = []
    sk = os.path.join(runs_dir, "daily_skipped.log")
    if os.path.exists(sk):
        with open(sk, "rb") as f:
            skipped = [l.decode("utf-8", "replace").rstrip() for l in f.read().splitlines()[-10:]]
    return {"dir": runs_dir, "lanes": out, "lock": lk, "skipped": skipped}


def _live(data_root):
    d = os.path.join(data_root, "live")
    q = sorted(glob.glob(os.path.join(d, "quotes_*.parquet")))
    intr = sorted(x for x in glob.glob(os.path.join(d, "intraday", "*")) if os.path.isdir(x))
    out = {"dir": d, "quotes": [{"name": os.path.basename(p), "mtime": _mtime(p)} for p in q[-6:]],
           "open_today": _mtime(os.path.join(d, "open_today.pkl")), "intraday": []}
    for p in intr[-6:]:
        day = os.path.basename(p)
        v = os.path.join(p, "vwap30_%s.parquet" % day)
        out["intraday"].append({"day": day, "n_files": len(os.listdir(p)), "vwap30": _mtime(v)})
    return out


def _tasks():
    """本机计划任务里我们的那几个 (zx_* / Codex_GS540*): 状态 / 上次运行与结果 / 下次运行 / 错过次数.

    非 Windows 或 PowerShell 调不了 -> {"error": ...}; 链路对应的任务不存在 -> 该链路 missing.
    """
    if os.name != "nt":
        return {"error": "非 Windows, 不读计划任务", "tasks": [], "lanes": {}}
    import subprocess
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", _TASK_PS], capture_output=True, timeout=60)
        raw = json.loads(r.stdout.decode("utf-8", "replace") or "[]")
    except Exception as e:  # noqa: BLE001 -- 任务状态只是辅助视图, 读不了不拖垮整个包
        return {"error": str(e)[:200], "tasks": [], "lanes": {}}
    raw = raw if isinstance(raw, list) else [raw]
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    tasks = []
    for t in raw:
        if not TASK_OURS_RE.match(t.get("name") or ""):
            continue
        res = t.get("result")
        t["result_ok"] = res in TASK_RESULT_OK
        t["result_text"] = TASK_RESULT_OK.get(res, "0x%X" % res if isinstance(res, int) and res >= 0 else str(res))
        # 下次运行时间已过却还挂着 = 到点没触发 (机器关机 / 调度器卡住); 留 10 分钟余量给正在启动的任务
        t["overdue"] = bool(t.get("next")) and t["state"] != "Disabled" and t["next"] < _minus_min(now, 10)
        tasks.append(t)
    by = {t["name"]: t for t in tasks}
    lanes = {k: (by.get(n) or {"name": n, "missing": True}) for k, n in LANE_TASKS.items()}
    return {"tasks": sorted(tasks, key=lambda t: t["name"]), "lanes": lanes, "at": now}


def _minus_min(ts, m):
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.mktime(time.strptime(ts, "%Y-%m-%d %H:%M:%S")) - 60 * m))


def _tracking(root):
    """多指数跟踪账户: config `tracking` 展开成 基准 x 约束档; 读不到配置 (或没有 yaml) -> 空."""
    try:
        import yaml
        with open(os.path.join(root, "config", "production.yaml"), encoding="utf-8") as f:
            t = (yaml.safe_load(f) or {}).get("tracking")
    except Exception as e:  # noqa: BLE001 -- 跟踪账户只是附加视图
        return {"error": str(e)[:200], "accounts": []}
    if not t:
        return {"accounts": []}
    base = os.path.join(root, t["root"])
    runs = sorted(d for d in os.listdir(os.path.join(base, "_shared")) if re.match(r"^\d{8}$", d)) \
        if os.path.isdir(os.path.join(base, "_shared")) else []
    last_run = _read_json(os.path.join(base, "_shared", runs[-1], "run.json")) if runs else None
    out = []
    for bench, code in (t.get("benchmarks") or {}).items():
        for cname, c in (t.get("constraints") or {}).items():
            name = "%s_%s" % (bench, cname)
            acc_dir, hold_dir = os.path.join(base, name, "account"), os.path.join(base, name, "paper")
            out.append({"key": name, "benchmark": bench, "benchmark_code": str(code), "constraint": cname,
                        "label": c.get("label", cname),
                        "params": {k: c.get(k) for k in ("te_limit", "active_bound", "style_dev")},
                        "account": _account(acc_dir), "positions": _positions(acc_dir), "paper": _paper(hold_dir)})
    m = t.get("model") or {}
    return {"start_day": str(t.get("start_day")), "root": t["root"], "model": m.get("name"), "model_dirs": m.get("dirs"),
            "round": m.get("round"), "last_run_day": runs[-1] if runs else None, "last_run": last_run, "accounts": out}


def _calendar(data_root, hold_dir, acc_rows, lanes, n=12):
    """最近 n 个交易日 (<= 今天) 各链路有没有产物; 交易日历读不了 (没有 pandas 等) -> 空."""
    try:
        import pandas as pd
        td = pd.to_datetime(pd.Series(pd.read_pickle(os.path.join(data_root, "auxiliary", "TradingDates.pkl"))).values)
    except Exception as e:  # noqa: BLE001 -- 日历只是辅助视图, 读不了不拖垮整个包
        return {"error": str(e)[:200], "days": []}
    today = pd.Timestamp(time.strftime("%Y-%m-%d"))
    days = [d.strftime("%Y%m%d") for d in sorted(td[td <= today])[-n:]]
    acc = {str(r.get("day") or "").replace("-", "") for r in acc_rows}
    logs = {l["key"]: {re.sub(r"\D", "", x["name"])[:8] for x in l["logs"]} for l in lanes["lanes"]}
    out = []
    for d in days:
        out.append({"day": d, "holdings": os.path.exists(os.path.join(hold_dir, d, "holdings.csv")),
                    "account": d in acc, **{k: d in v for k, v in logs.items()}})
    return {"days": out, "last_trading_day": days[-1] if days else None}


def build(root, data_root, runs_dir):
    t0 = time.time()
    cfg = _prod_cfg(root)
    hold_dir = os.path.join(root, cfg["holdings_dir"])
    acc_dir = os.path.join(root, cfg["out_dir"])
    acc = _account(acc_dir)
    lanes = _lanes(runs_dir)
    b = {"account": acc, "positions": _positions(acc_dir), "paper": _paper(hold_dir),
         "gate_latest": _gate_brief(_read_json(os.path.join(runs_dir, "gate_latest", "input_gate.json"))),
         "gate_latest_mtime": _mtime(os.path.join(runs_dir, "gate_latest", "input_gate.json")),
         "lanes": lanes, "live": _live(data_root), "tasks": _tasks(), "tracking": _tracking(root),
         "calendar": _calendar(data_root, hold_dir, acc["rows"], lanes)}
    b["meta"] = {"host": socket.gethostname(), "root": root, "data_root": data_root, "runs_dir": runs_dir,
                 "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"), "seconds": round(time.time() - t0, 1),
                 "models_dir": cfg["models_dir"], "holdings_dir": cfg["holdings_dir"], "out_dir": cfg["out_dir"]}
    return b


def main(argv=None):
    ap = argparse.ArgumentParser(description="实盘跟踪数据包 (只读)")
    ap.add_argument("--root", required=True, help="代码检出 (results/v3ra 在其下)")
    ap.add_argument("--data-root", required=True)
    ap.add_argument("--runs-dir", required=True, help="日更 / 清晨 / 竞价日志目录")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    b = build(a.root, a.data_root, a.runs_dir)
    tmp = a.out + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(b, f, ensure_ascii=False, default=str)
    os.replace(tmp, a.out)
    print("bundle: %s (%.1fs, %d KB)" % (a.out, b["meta"]["seconds"], os.path.getsize(a.out) // 1024))
    return 0


if __name__ == "__main__":
    sys.exit(main())
