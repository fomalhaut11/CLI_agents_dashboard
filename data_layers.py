# -*- coding: utf-8 -*-
"""数据页的数据集目录: 从血缘 (config/derived_data.yaml) 派生, 分三组呈现新鲜度.

为什么不再手写清单
------------------
旧版数据页读 `config.KEY_DATASETS` -- 一张手写死的 10 条清单。2026-08-16 核对发现它
只覆盖血缘中 37 个"直接挂 EXTERNAL 上游"节点里的 7 个 (19%), 且那 10 条里有 4 条根本
不是外部源而是下游派生。于是页面既不是"外部依赖"也不是某一层, 是按当时手感挑的混合表,
新增外部源永远不会自己出现。改为从真源派生 (同 gen_inventory.py / gen_stream_index.py 的套路)。

分组判据 (显式写死在这里, 方便审计)
-----------------------------------
- external     直接连外部世界: `upstream` 里含 `EXTERNAL:<token>`
- product      可消费的最终产物: 层 3_ml_prep / 4_ml_output / 5_backtest,
               外加 6_factor_test 中 path 落在 `results/` 下的 (真的是结果)
- intermediate 其余: 清洗层 / 因子层 / alpha191 等中间计算物

注意 6_factor_test 里有 190 个 `alpha_*` 节点, 它们是因子 pkl (upstream=Price) 而非
测试结果, 层名是血缘的历史归类。按 path 判而不是按层名判, 才不会把它们错报成"最终产物"。

新鲜度两层
----------
1. mtime 快判 -- 页面加载即算, 便宜。但 **mtime 会骗人**: 陈旧上游 + 重算下游 =
   文件新、内容旧 (实测 Vol_20 mtime 新 28 天而内容原地不动; RiskFreeRate mtime 当天
   而内容停在 2025-03-04)。所以 mtime 只用来提示"可能没人动过", 不作新鲜度结论。
2. 内容终点日 -- 真判据, 但要读文件, 贵。放缓存, 键 = (path, mtime, size);
   文件没变就直接命中, 因此只有首次全扫慢, 之后近乎免费。

CLI (供数据页的"内容级复检"按钮以 managed-process 拉起):
    python tools/stream_dashboard/data_layers.py --scan
    python tools/stream_dashboard/data_layers.py --scan --group external
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time

DASH_DIR = os.path.dirname(os.path.abspath(__file__))


def _repo_root():
    """仓库根. 优先问 config —— 独立仓 (CLI_agents_dashboard) 是扁平布局, 写死
    "往上两级" 会算出盘符根这种荒唐值, 进而去找 <盘符根>/config/derived_data.yaml。
    那个文件通常不存在所以"碰巧对了", 但换台机器就可能读到毫不相干的 yaml。"""
    try:
        if DASH_DIR not in sys.path:
            sys.path.insert(0, DASH_DIR)
        import config as _c
        if getattr(_c, "REPO_ROOT", ""):
            return _c.REPO_ROOT
    except Exception:                                  # noqa: BLE001
        pass
    return os.path.abspath(os.path.join(DASH_DIR, "..", ".."))


REPO_ROOT = _repo_root()
LINEAGE_YAML = os.path.join(REPO_ROOT, "config", "derived_data.yaml")
CONTENT_CACHE = os.path.join(DASH_DIR, "_data_content_cache.json")

GROUPS = [
    ("external", "外部依赖"),
    ("intermediate", "中间数据"),
    ("product", "最终产物"),
]

PRODUCT_LAYERS = {"3_ml_prep", "4_ml_output", "5_backtest"}

# frequency -> 允许的最大静止天数 (None = 不判, 如 on_demand / static)
MAX_AGE_BY_FREQ = {
    "daily": 4,
    "monthly+daily": 4,
    "weekly": 10,
    "monthly": 40,
    "quarterly": 100,
}

# 本仓日期列命名很杂: tradingday / sel_day / 写入时间 / date / 日期 ...
# 漏一个就整条数据集判不出终点日, 所以宁可宽一点, 再靠 _series_max_date 的量程过滤误配。
_DATE_COL_RE = re.compile(r"date|day|dt$|日期|交易日|时间|月份", re.I)

# 写入审计列: 值随"我们什么时候跑的更新"前进, 与数据覆盖到哪天无关 --
# 拿它当内容终点日, 就是把 mtime 的毛病换个地方再犯一遍
# (实测 SectorChanges_data.写入时间 = 今天, 而真实 sel_day 止于 2026-08-14)。
_AUDIT_COL_RE = re.compile(
    r"写入时间|入库时间|insert_time|update_time|updated_at|created_at|create_time|load_time|etl_",
    re.I,
)


def _is_date_col(name):
    n = str(name)
    return bool(_DATE_COL_RE.search(n)) and not _AUDIT_COL_RE.search(n)


# ---------------- 目录构建 ----------------

def lineage_available():
    """本项目特有的血缘文件在不在。不在 = 跑在可移植版 (独立仓) 上。"""
    return os.path.isfile(LINEAGE_YAML)


def _load_yaml():
    if not lineage_available():
        return {}
    import yaml
    with open(LINEAGE_YAML, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _catalog_from_config(data_root):
    """可移植版回退: 没有 config/derived_data.yaml 时, 退回 config 里的手写清单。

    本模块的分组来自多因子项目的血缘, 是项目特有能力; 独立仓 (CLI_agents_dashboard)
    没有那个文件, 不能让数据页整个崩掉 —— 退化成"一组 + 只有 mtime 判"即可。"""
    sys.path.insert(0, DASH_DIR)
    import config as C
    out = []
    for item in (getattr(C, "KEY_DATASETS", None) or []):
        vals = list(item) + [None, None, None]
        label, rel, max_age = vals[0], vals[1], vals[2]
        abs_path = os.path.normpath(os.path.join(data_root, str(rel).replace("/", os.sep)))
        out.append({
            "name": label, "layer": "config", "group": "external",
            "rel": str(rel), "abs": abs_path, "is_pattern": False,
            "frequency": "", "max_age": max_age, "generator": "",
            "daily_call": "", "external_tokens": [],
        })
    return out


def classify(layer, node):
    """分组判据. 见模块 docstring。"""
    ups = node.get("upstream") or []
    if any(isinstance(u, str) and u.startswith("EXTERNAL:") for u in ups):
        return "external"
    if layer in PRODUCT_LAYERS:
        return "product"
    path = (node.get("path") or "").replace("\\", "/")
    if layer == "6_factor_test" and "/results/" in path:
        return "product"
    return "intermediate"


def resolve_path(path_tmpl, data_root):
    """`${data_root}/x` -> 绝对路径; 无前缀的按仓库根解析。返回 (abs, is_pattern)。"""
    if not path_tmpl:
        return None, False
    p = path_tmpl.strip().replace("\\", "/")
    if p.startswith("${data_root}"):
        p = p.replace("${data_root}", data_root.replace("\\", "/"), 1)
    elif not re.match(r"^[A-Za-z]:/|^/", p):
        p = REPO_ROOT.replace("\\", "/") + "/" + p
    # 必须在替换 ${data_root} 之后判: 否则那对花括号会让每一条都被当成通配模式
    # (2026-08-16 首次跑扫描时 37 项只剩 1 项就是这么来的)
    is_pattern = ("{" in p) or ("*" in p)
    return os.path.normpath(p), is_pattern


def build_catalog(data_root):
    """返回全部节点的静态信息 (不做 stat, 不读内容)。"""
    data = _load_yaml()
    if not data:
        return _catalog_from_config(data_root)
    out = []
    for layer, items in data.items():
        for name, node in (items or {}).items():
            if not isinstance(node, dict):
                continue
            abs_path, is_pattern = resolve_path(node.get("path"), data_root)
            ext_tokens = [u.split(":", 1)[1] for u in (node.get("upstream") or [])
                          if isinstance(u, str) and u.startswith("EXTERNAL:")]
            out.append({
                "name": name,
                "layer": layer,
                "group": classify(layer, node),
                "rel": node.get("path") or "",
                "abs": abs_path,
                "is_pattern": is_pattern,
                "frequency": node.get("frequency"),
                "max_age": MAX_AGE_BY_FREQ.get(node.get("frequency")),
                "generator": (node.get("generator") or "").split("::")[0],
                "daily_call": node.get("daily_call") or "",
                "external_tokens": ext_tokens,
            })
    out.sort(key=lambda x: (x["group"], x["layer"], x["name"].lower()))
    return out


# ---------------- mtime 快判 ----------------

def stat_entry(entry, now=None):
    """就地补 exists / mtime / age_days / size_mb / stale。目录取其下最新文件。"""
    now = now or time.time()
    p = entry.get("abs")
    entry.update({"exists": False, "mtime": "", "age_days": None, "size_mb": None,
                  "stale": False, "n_files": None})
    if not p:
        return entry
    try:
        if os.path.isdir(p):
            newest, total, n = 0.0, 0, 0
            for dp, _dn, fn in os.walk(p):
                for name in fn:
                    fp = os.path.join(dp, name)
                    try:
                        st = os.stat(fp)
                    except OSError:
                        continue
                    newest = max(newest, st.st_mtime)
                    total += st.st_size
                    n += 1
                if n > 20000:      # 目录极大 (store_v2/minute) 时不必走完
                    break
            if n == 0:
                return entry
            mt, size = newest, total
            entry["n_files"] = n
        elif os.path.exists(p):
            st = os.stat(p)
            mt, size = st.st_mtime, st.st_size
        else:
            return entry
    except OSError:
        return entry

    age = (now - mt) / 86400.0
    entry.update({
        "exists": True,
        "mtime": time.strftime("%Y-%m-%d %H:%M", time.localtime(mt)),
        "age_days": round(age, 1),
        "size_mb": round(size / 1e6, 1),
        "stale": bool(entry.get("max_age") is not None and age > entry["max_age"]),
    })
    return entry


# ---------------- 内容终点日 (缓存) ----------------

def _cache_key(path):
    try:
        st = os.stat(path) if not os.path.isdir(path) else None
    except OSError:
        return None
    if st is None:
        return "dir"
    return "%d:%d" % (int(st.st_mtime), st.st_size)


def load_content_cache():
    try:
        with open(CONTENT_CACHE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"scanned_at": "", "entries": {}}


def _save_content_cache(cache):
    tmp = CONTENT_CACHE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=1)
    os.replace(tmp, CONTENT_CACHE)


def _series_max_date(s):
    """一列 -> 最大日期. 关键: 本仓大量日期列是 int/float 的 YYYYMMDD
    (ST_stocks.tradingday = 20260814), 直接 to_datetime 会当成 epoch 纳秒
    解析成 1970-01-01 -- 静默错值比读不出更坏, 所以数值列单独按 %Y%m%d 走。"""
    import pandas as pd
    if pd.api.types.is_numeric_dtype(s):
        v = s.dropna()
        if not len(v):
            return None
        v = v.astype("int64")
        v = v[(v >= 19000101) & (v <= 21001231)]
        if not len(v):
            return None
        return pd.to_datetime(str(int(v.max())), format="%Y%m%d")
    v = pd.to_datetime(s, errors="coerce").dropna()
    if not len(v):
        return None
    m = pd.Timestamp(v.max())
    return m if m.year >= 1990 else None      # 1970 一律视为解析失败, 不外报


def _max_from_obj(obj):
    """从 DataFrame/Series/dict 里挖内容终点日, 挖不到返回 None。"""
    import pandas as pd

    def frame_max(o):
        idx = None
        if isinstance(o.index, pd.MultiIndex):
            for lvl in range(o.index.nlevels):
                vals = o.index.get_level_values(lvl)
                if isinstance(vals, pd.DatetimeIndex):
                    idx = vals
                    break
        elif isinstance(o.index, pd.DatetimeIndex):
            idx = o.index
        if idx is not None and len(idx):
            return pd.Timestamp(idx.max())
        if hasattr(o, "columns"):
            best = None
            for c in o.columns:                     # 扫全部日期列取最大, 别只认第一列
                if _is_date_col(c):
                    m = _series_max_date(o[c])
                    if m is not None and (best is None or m > best):
                        best = m
            return best
        return None

    if isinstance(obj, dict):
        best = None
        for v in obj.values():
            if hasattr(v, "index"):
                m = frame_max(v)
                if m is not None and (best is None or m > best):
                    best = m
        return best
    if hasattr(obj, "index"):
        return frame_max(obj)
    return None


def content_end_date(path):
    """读文件求内容终点日. 返回 'YYYY-MM-DD' 或 None (读不出/不适用)。"""
    if not path or not os.path.exists(path):
        return None
    low = path.lower()
    try:
        if os.path.isdir(path):
            # 按 date=YYYYMMDD 分区的 parquet store (minute / store_v2 / returns)
            parts = [d for d in os.listdir(path) if d.startswith("date=")]
            if parts:
                d = max(parts)[5:]
                return "%s-%s-%s" % (d[:4], d[4:6], d[6:8])
            years = [d for d in os.listdir(path) if d.startswith("year=")]
            if years:
                return "%s (year 分区)" % max(years)[5:]
            return None
        if low.endswith(".pkl"):
            import pandas as pd
            m = _max_from_obj(pd.read_pickle(path))
            return m.strftime("%Y-%m-%d") if m is not None else None
        if low.endswith(".parquet"):
            import pandas as pd
            import pyarrow.parquet as pq
            cols = [c for c in pq.ParquetFile(path).schema.names if _is_date_col(c)]
            if not cols:
                return None
            df = pd.read_parquet(path, columns=cols[:1])
            m = _series_max_date(df.iloc[:, 0])
            return m.strftime("%Y-%m-%d") if m is not None else None
    except Exception as exc:                      # 读不出就说读不出, 不装作新鲜
        return "ERR:%s" % type(exc).__name__
    return None


def scan_content(data_root, group=None, verbose=True, force=False):
    """扫内容终点日并落缓存. 键 (path, mtime, size) 命中则跳过重读。

    force=True 忽略缓存重读 -- 改了读取器逻辑时必须用, 否则旧的错值 (文件没变
    -> 键命中) 会一直留在缓存里。"""
    cache = load_content_cache()
    entries = {} if force else (cache.get("entries") or {})
    cat = build_catalog(data_root)
    todo = [e for e in cat if (group in (None, e["group"])) and e["abs"] and not e["is_pattern"]]
    hit = read = 0
    for i, e in enumerate(todo, 1):
        key = _cache_key(e["abs"])
        if key is None:
            continue
        prev = entries.get(e["abs"])
        if prev and prev.get("key") == key:
            hit += 1
            continue
        val = content_end_date(e["abs"])
        entries[e["abs"]] = {"key": key, "date": val}
        read += 1
        if verbose and read % 20 == 0:
            print("  [%d/%d] 已读 %d, 缓存命中 %d" % (i, len(todo), read, hit), flush=True)
    cache["entries"] = entries
    cache["scanned_at"] = time.strftime("%Y-%m-%d %H:%M")
    _save_content_cache(cache)
    if verbose:
        print("内容扫描完成: 目标 %d, 实读 %d, 缓存命中 %d -> %s"
              % (len(todo), read, hit, CONTENT_CACHE), flush=True)
    return cache


# ---------------- 内容终点判定 (血缘快照) ----------------
# 2026-10-16: 表格/卡片原先只按 mtime 判, on_demand 节点没有阈值永远"活跃" (10-16 实测漏掉 41 个
# 内容终点停在 09-18 的节点), 又不认 frozen / REMOTE, 把冻结件报成缺失/静止。现优先采用点亮图同一份
# 快照 (lineage_snapshot.py, 判据 = core.data_endpoint strict, 同 manage.py data scan); 没有快照
# (可移植版 / 从未刷新) 才退回 mtime。快照不自动刷新, 页面显示其生成时间。
LINEAGE_SNAPSHOT = os.path.join(DASH_DIR, "_lineage_snapshot.json")
JUDGE_STALE = {"STALE", "UNKNOWN", "UNREADABLE", "UNSCANNED", "UNJUDGED"}


def load_judge():
    """{节点名: 快照节点}, 快照生成时间; 没有/读不出快照 -> ({}, "")。"""
    try:
        with open(LINEAGE_SNAPSHOT, encoding="utf-8") as f:
            snap = json.load(f)
        return {n["name"]: n for n in snap.get("nodes") or [] if n.get("name")}, snap.get("generated_at", "")
    except (OSError, ValueError, TypeError, KeyError):
        return {}, ""


def _apply_judge(e, judge):
    j = judge.get(e["name"])
    if not j:
        e["judge"] = None
        return
    st = j.get("status")
    e["judge"] = st
    e["judge_why"] = j.get("why") or ""
    if j.get("endpoint"):
        e["content_date"] = j["endpoint"]
        e["content_cached"] = True
    e["stale"] = st in JUDGE_STALE
    e["judge_missing"] = st == "MISSING"


def _is_missing(e):
    return e["judge_missing"] if e.get("judge") else not e["exists"]


def overview(data_root, group=None):
    """页面用: 目录 + 内容终点判定 (血缘快照, 无快照时 mtime 快判) + 内容终点日。"""
    cache = load_content_cache()
    entries = cache.get("entries") or {}
    judge, judged_at = load_judge()
    now = time.time()
    rows = []
    for e in build_catalog(data_root):
        if group and e["group"] != group:
            continue
        stat_entry(e, now)
        c = entries.get(e["abs"]) or {}
        e["content_date"] = c.get("date")
        e["content_cached"] = bool(c) and c.get("key") == _cache_key(e["abs"] or "")
        _apply_judge(e, judge)
        e["missing"] = _is_missing(e)
        rows.append(e)
    return {"rows": rows, "scanned_at": cache.get("scanned_at", ""), "judged_at": judged_at}


def group_summary(data_root):
    """三组的计数/过期数, 给页面顶部的卡片 (判据同 overview)。"""
    now = time.time()
    judge, _ = load_judge()
    agg = {k: {"key": k, "label": lbl, "total": 0, "stale": 0, "missing": 0}
           for k, lbl in GROUPS}
    for e in build_catalog(data_root):
        stat_entry(e, now)
        _apply_judge(e, judge)
        a = agg[e["group"]]
        a["total"] += 1
        if _is_missing(e):
            a["missing"] += 1
        elif e["stale"]:
            a["stale"] += 1
    return [agg[k] for k, _ in GROUPS]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scan", action="store_true", help="扫内容终点日写缓存")
    ap.add_argument("--group", choices=[k for k, _ in GROUPS], default=None)
    ap.add_argument("--force", action="store_true", help="忽略缓存重读 (改了读取器后必用)")
    ap.add_argument("--data-root", default=None)
    args = ap.parse_args(argv)

    data_root = args.data_root or os.environ.get("DATA_ROOT")
    if not data_root:
        sys.path.insert(0, DASH_DIR)
        import config as C
        data_root = C.DATA_ROOT

    if args.scan:
        scan_content(data_root, args.group, force=args.force)
        return 0
    for g in group_summary(data_root):
        print("%-12s total=%-4d stale=%-4d missing=%d"
              % (g["label"], g["total"], g["stale"], g["missing"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
