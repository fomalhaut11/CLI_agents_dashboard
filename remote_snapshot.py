# -*- coding: utf-8 -*-
"""别的机器 (如 .22) 的数据页数据包 -- 在**那台机器上**跑 (2026-10-29).

由 remote_fetch.py 连同 lineage_snapshot.py / data_layers.py 一起 scp 到远端临时目录, 用远端自己的解释器、
在远端的代码检出 (--root) 与数据根 (--data-root) 上跑, 产出一个 JSON, 再被拉回本机给数据页读:

    lineage   血缘快照 (lineage_snapshot.build, strict judge, 与 manage.py data scan 同口径; 含最近运行记录)
    overview  数据集目录 + 每项 mtime / 大小 + 血缘判定 (同 data_layers.overview, 但判定取自刚建的快照;
              不读内容终点缓存 -- 终点日来自快照的 endpoint)
    groups / counts / store_v2_version   数据页顶部卡片
    runs      日更日志目录里最近几次 daily_*.log (时间 / 大小 / 末行 / 有无 END) 与锁文件
    meta      主机名 / 生成时间 / 耗时 / 代码检出里血缘定义与执行器的 mtime (远端检出不是 git, 没有提交号)

只读: 不写数据根, 不写代码检出; 输出只落 --out.

    <远端解释器> remote_snapshot.py --root <检出> --data-root <数据根> --out <json> [--counts <json>] [--runs-dir <目录>]
"""
import argparse
import json
import os
import re
import socket
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
_RC_RE = re.compile(r"^(WAVE\d+|V3RA_GATE)_RC=(-?\d+)")     # tools/deploy/daily_22.cmd 每步写的返回码行


def _runs(runs_dir, n=6):
    if not runs_dir or not os.path.isdir(runs_dir):
        return {"dir": runs_dir, "logs": [], "lock": None}
    logs = sorted((f for f in os.listdir(runs_dir) if f.startswith("daily_") and f.endswith(".log")
                   and f != "daily_skipped.log"), reverse=True)[:n]
    out = []
    for f in logs:
        p = os.path.join(runs_dir, f)
        try:
            st = os.stat(p)
            rcs, ended, last = {}, False, ""
            with open(p, "rb") as fh:
                for raw in fh:
                    line = raw.decode("utf-8", "replace").strip()
                    if not line:
                        continue
                    last = line
                    m = _RC_RE.match(line)
                    if m:
                        rcs[m.group(1).lower()] = int(m.group(2))
                    elif line.startswith("END"):
                        ended = True
            out.append({"name": f, "mtime": time.strftime("%Y-%m-%d %H:%M", time.localtime(st.st_mtime)),
                        "size_kb": st.st_size // 1024, "last": last[:200], "ended": ended, "rcs": rcs})
        except OSError:
            continue
    lock = os.path.join(runs_dir, "daily.lock")
    lk = None
    if os.path.exists(lock):
        lk = time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(lock)))
    return {"dir": runs_dir, "logs": out, "lock": lk}


def build(root, data_root, counts_spec, runs_dir):
    t0 = time.time()
    os.environ.setdefault("DATA_ROOT", data_root)
    sys.path.insert(0, HERE)
    import lineage_snapshot as LS
    import data_layers as DL

    # 目标日记录在日更日志目录下的 run_targets/ (tools/deploy/daily_22.cmd --runs-dir), 不在检出里
    snap = LS.build(root, run_targets_dir=(os.path.join(runs_dir, "run_targets") if runs_dir else None))
    DL.REPO_ROOT = root                                    # 脚本在远端临时目录, 血缘定义在远端检出里
    DL.LINEAGE_YAML = os.path.join(root, "config", "derived_data.yaml")
    judge = {n["name"]: n for n in snap.get("nodes") or [] if n.get("name")}
    now = time.time()
    rows = []
    agg = DL.new_group_agg()
    for e in DL.build_catalog(data_root):
        DL.stat_entry(e, now)
        e["content_date"], e["content_cached"] = None, False
        DL._apply_judge(e, judge)
        e["missing"] = DL._is_missing(e)
        rows.append(e)
        DL.count_entry(agg, e)
    counts = []
    for label, rel, suf in counts_spec:
        d = os.path.join(data_root, rel.replace("/", os.sep))
        counts.append({"label": label, "rel": rel,
                       "count": sum(1 for x in os.listdir(d) if x.endswith(suf)) if os.path.isdir(d) else 0})
    version = ""
    sv = os.path.join(data_root, "factors", "store_v2")
    if os.path.exists(sv):
        version = os.path.basename(os.path.realpath(sv).rstrip("\\/"))
    stamp = {}
    for rel in ("config/derived_data.yaml", "tools/data_management/lineage_pipeline.py"):
        p = os.path.join(root, *rel.split("/"))
        stamp[rel] = time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(p))) if os.path.exists(p) else None
    return {
        "meta": {"host": socket.gethostname(), "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                 "seconds": round(time.time() - t0, 1), "root": root, "data_root": data_root, "code_mtime": stamp,
                 "lineage_available": os.path.isfile(DL.LINEAGE_YAML)},
        "lineage": snap,
        "overview": {"rows": rows, "judged_at": snap.get("generated_at", ""), "scanned_at": ""},
        "groups": [agg[k] for k, _ in DL.GROUPS],
        "counts": counts, "store_v2_version": version,
        "runs": _runs(runs_dir),
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description="别的机器的数据页数据包 (在那台机器上跑)")
    ap.add_argument("--root", required=True, help="该机代码检出")
    ap.add_argument("--data-root", required=True, help="该机数据根")
    ap.add_argument("--out", required=True)
    ap.add_argument("--counts", default=None, help="目录计数清单 JSON [[标签, 相对路径, 后缀], ...]")
    ap.add_argument("--runs-dir", default=None, help="日更日志目录 (daily_*.log / daily.lock)")
    a = ap.parse_args(argv)
    spec = []
    if a.counts and os.path.exists(a.counts):
        with open(a.counts, encoding="utf-8") as f:
            spec = json.load(f)
    bundle = build(os.path.abspath(a.root), a.data_root, spec, a.runs_dir)
    tmp = a.out + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(bundle, f, ensure_ascii=False)
    os.replace(tmp, a.out)
    print("bundle: %d nodes, %d rows, %.1fs -> %s" % (len(bundle["lineage"]["nodes"]), len(bundle["overview"]["rows"]),
                                                     bundle["meta"]["seconds"], a.out), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
