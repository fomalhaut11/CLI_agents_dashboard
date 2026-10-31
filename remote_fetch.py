# -*- coding: utf-8 -*-
"""把别的机器 (如 .22) 的数据页数据包拉回本机 (2026-10-29). 在**本机**跑, 数据页 "刷新" 按钮以受管进程拉起.

步骤 (全走 ssh / scp, 别名见 ~/.ssh/config; 会话沙箱里 ControlMaster socket 写不了, 一律 ControlMaster=no):
  1. 远端临时目录里放 dashboard 自己这份 lineage_snapshot.py / data_layers.py / remote_snapshot.py + 目录计数清单
     (远端代码检出可能还没有新版脚本; 判定用的血缘定义 / 执行器 / 运行历史仍取远端检出, 与本机 "刷新快照" 同理)
  2. 在远端检出里用远端解释器跑 remote_snapshot.py (只读: 不写数据根与检出, 输出落远端临时目录)
  3. scp 回本机 <dashboard 目录>/_remote_<key>.json (先写 .tmp 再替换, 失败不覆盖旧包)

机器清单: config.REMOTE_DATA_MACHINES ({key, label, ssh, root, data_root, python, tmp, runs_dir}).

--live (2026-10-31): 改拉实盘跟踪页签的数据包 -- 只推 live_snapshot.py, 远端几秒跑完, 拉回 _live_<key>.json.

    python tools/stream_dashboard/remote_fetch.py --machine 22 [--live]
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import time

DASH_DIR = os.path.dirname(os.path.abspath(__file__))
SSH_OPTS = ["-o", "ControlMaster=no", "-o", "ControlPath=none", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15"]
SHIP = ("lineage_snapshot.py", "data_layers.py", "remote_snapshot.py")


def bundle_path(key):
    return os.path.join(DASH_DIR, "_remote_%s.json" % key)


def live_bundle_path(key):
    return os.path.join(DASH_DIR, "_live_%s.json" % key)


def machines():
    if DASH_DIR not in sys.path:
        sys.path.insert(0, DASH_DIR)
    import config as C
    return {m["key"]: m for m in getattr(C, "REMOTE_DATA_MACHINES", None) or []}, list(getattr(C, "DIR_COUNTS", []))


def _win(p):
    return str(p).replace("/", "\\")


def _run(args, timeout):
    t0 = time.time()
    p = subprocess.run(args, stdin=subprocess.DEVNULL, capture_output=True, timeout=timeout)
    out = (p.stdout + p.stderr).decode("utf-8", "replace")
    if p.returncode != 0:
        raise SystemExit("失败 (rc=%d, %.0fs): %s\n%s" % (p.returncode, time.time() - t0, " ".join(args[:3]), out[-2000:]))
    return out


def fetch(m, counts, timeout=1800):
    host, tmp = m["ssh"], m["tmp"].rstrip("/")
    print("[1/3] %s: 推脚本到 %s:%s" % (m["label"], host, tmp), flush=True)
    _run(["ssh"] + SSH_OPTS + [host, "if not exist %s mkdir %s" % (_win(tmp), _win(tmp))], 60)
    with tempfile.TemporaryDirectory() as td:
        cj = os.path.join(td, "counts.json")
        with open(cj, "w", encoding="utf-8") as f:
            json.dump([list(c) for c in counts], f, ensure_ascii=False)
        files = [os.path.join(DASH_DIR, x) for x in SHIP] + [cj]
        _run(["scp"] + SSH_OPTS + ["-q"] + files + ["%s:%s/" % (host, tmp)], 120)
    print("[2/3] 在 %s 的 %s 上跑 (数据根 %s; 血缘判定要读数据, 约 5-10 分钟)" % (m["label"], m["root"], m["data_root"]),
          flush=True)
    cmd = ("cd /d {root} && {py} {tmp}\\remote_snapshot.py --root {root} --data-root {data} --out {tmp}\\bundle.json "
           "--counts {tmp}\\counts.json{runs}").format(
        root=_win(m["root"]), py=_win(m["python"]), tmp=_win(tmp), data=_win(m["data_root"]),
        runs=(" --runs-dir " + _win(m["runs_dir"])) if m.get("runs_dir") else "")
    out = _run(["ssh"] + SSH_OPTS + [host, cmd], timeout)
    tail = [l for l in out.splitlines() if l.startswith("bundle:")]
    print("      " + (tail[-1] if tail else out.strip().splitlines()[-1] if out.strip() else ""), flush=True)
    dst = bundle_path(m["key"])
    print("[3/3] 拉回 -> %s" % dst, flush=True)
    _run(["scp"] + SSH_OPTS + ["-q", "%s:%s/bundle.json" % (host, tmp), dst + ".tmp"], 300)
    with open(dst + ".tmp", encoding="utf-8") as f:
        json.load(f)                                       # 拉回的包坏了就别覆盖旧包
    os.replace(dst + ".tmp", dst)
    return dst


def fetch_live(m, timeout=300):
    """实盘跟踪数据包: 推 live_snapshot.py -> 远端只读跑 -> 拉回 _live_<key>.json (坏包不覆盖旧包)."""
    host, tmp = m["ssh"], m["tmp"].rstrip("/")
    if not m.get("runs_dir"):
        raise SystemExit("机器 %s 没配 runs_dir (日志目录), 实盘数据包要读它" % m["key"])
    print("[1/3] %s: 推 live_snapshot.py 到 %s:%s" % (m["label"], host, tmp), flush=True)
    _run(["ssh"] + SSH_OPTS + [host, "if not exist %s mkdir %s" % (_win(tmp), _win(tmp))], 60)
    _run(["scp"] + SSH_OPTS + ["-q", os.path.join(DASH_DIR, "live_snapshot.py"), "%s:%s/" % (host, tmp)], 120)
    print("[2/3] 在 %s 上只读跑 (检出 %s, 日志 %s)" % (m["label"], m["root"], m["runs_dir"]), flush=True)
    cmd = ("cd /d {root} && {py} {tmp}\\live_snapshot.py --root {root} --data-root {data} --runs-dir {runs} "
           "--out {tmp}\\live.json").format(root=_win(m["root"]), py=_win(m["python"]), tmp=_win(tmp),
                                            data=_win(m["data_root"]), runs=_win(m["runs_dir"]))
    out = _run(["ssh"] + SSH_OPTS + [host, cmd], timeout)
    tail = [l for l in out.splitlines() if l.startswith("bundle:")]
    print("      " + (tail[-1] if tail else out.strip()[-300:]), flush=True)
    dst = live_bundle_path(m["key"])
    print("[3/3] 拉回 -> %s" % dst, flush=True)
    _run(["scp"] + SSH_OPTS + ["-q", "%s:%s/live.json" % (host, tmp), dst + ".tmp"], 120)
    with open(dst + ".tmp", encoding="utf-8") as f:
        json.load(f)
    os.replace(dst + ".tmp", dst)
    return dst


def main(argv=None):
    ap = argparse.ArgumentParser(description="拉别的机器的数据页数据包")
    ap.add_argument("--machine", required=True)
    ap.add_argument("--live", action="store_true", help="拉实盘跟踪数据包 (live_snapshot.py) 而不是数据页数据包")
    a = ap.parse_args(argv)
    ms, counts = machines()
    if a.machine not in ms:
        raise SystemExit("config.REMOTE_DATA_MACHINES 里没有 %s (有: %s)" % (a.machine, ", ".join(ms) or "无"))
    t0 = time.time()
    dst = fetch_live(ms[a.machine]) if a.live else fetch(ms[a.machine], counts)
    print("完成 %.0fs: %s" % (time.time() - t0, dst), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
