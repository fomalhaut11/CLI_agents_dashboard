# -*- coding: utf-8 -*-
"""血缘更新快照 (dashboard 数据页"点亮图"的数据源, 2026-10-15).

以 managed process 在某个检出 (cwd) 里跑: 用那个检出的 config/derived_data.yaml 分 wave, 对每个节点做
strict judge (内容终点 vs 期望), 再把 logs/pipeline_runtime_history.json 里该节点命令的最近一次运行结果贴上。
输出一个 JSON 快照 (--out), 页面只读快照, 不在 web 线程里判 562 个节点。

    python tools/stream_dashboard/lineage_snapshot.py --out tools/stream_dashboard/_lineage_snapshot.json

判定口径与 `manage.py data scan` 相同 (core.data_endpoint.judge strict=True); 点亮 = OK / FROZEN / STATIC。
"""
import argparse
import collections
import json
import os
import sys
import time


def build(root, features=None):
    sys.path.insert(0, root)
    from tools.data_management import lineage_pipeline as LP
    from tools.pipeline_observe import runtime_key, load_history
    from core.data_endpoint import judge, trading_days

    mgr, waves = LP.waves()
    mgr, edges = LP.build_graph(mgr)
    node_wave = {n: k for k, ns in waves.items() for n in ns}

    cmd_of = {}
    for k in waves:
        _, rows = LP.commands_of_wave(k, mgr)
        for cmd, nodes, tmo, fam, heavy in rows:
            for n in nodes:
                cmd_of[n] = {'cmd': cmd, 'budget': tmo, 'family': fam, 'heavy': bool(heavy),
                             'siblings': [x for x in nodes if x != n]}

    history = load_history()
    today = time.strftime('%Y-%m-%d')

    def last_run(cmd):
        runs = history.get(runtime_key(cmd), [])
        if not runs:
            return None
        e = runs[-1]
        return {'ts': e.get('ts'), 'status': e.get('status'), 'sec': e.get('sec'),
                'note': str(e.get('note', ''))[:120], 'today': str(e.get('ts', '')).startswith(today)}

    feats = set()
    cands = ([features] if features else []) + [os.path.join(root, 'results/v6.3_specific_return_1d/models/round23_meta.json')]
    for p in cands:
        if p and os.path.exists(p):
            try:
                feats = set(json.load(open(p, encoding='utf-8')).get('feature_names') or [])
                break
            except Exception:                    # noqa: BLE001 特征清单只是高亮, 读不到就不高亮
                feats = set()

    names = sorted(mgr.items)
    eps = LP._endpoints(mgr, names)
    nodes = []
    for n in names:
        it = mgr.items[n]
        try:
            st, why = judge(it, mgr=mgr, allow_read=True, strict=True)
        except Exception as e:                   # noqa: BLE001
            st, why = 'ERR', str(e)[:120]
        ext = [u for u in (it.upstream or []) if str(u).startswith(('EXTERNAL:', 'REMOTE:', 'SQL:'))]
        c = cmd_of.get(n)
        nodes.append({
            'name': n, 'wave': node_wave.get(n, -1), 'layer': getattr(it, 'layer', '') or '',
            'frequency': getattr(it, 'frequency', '') or '', 'frozen': bool(getattr(it, 'frozen', False)),
            'upstream': edges.get(n, []), 'external': ext,
            'status': st, 'why': str(why)[:160],
            'endpoint': (str(eps.get(n))[:10] if eps.get(n) is not None else None),
            'cmd': (c['cmd'] if c else None), 'budget': (c['budget'] if c else None),
            'family': (c['family'] if c else None), 'heavy': (c['heavy'] if c else False),
            'last_run': (last_run(c['cmd']) if c else None),
            'feature': n in feats,
        })
    lit = sum(1 for x in nodes if x['status'] in ('OK', 'FROZEN', 'STATIC'))
    cal = trading_days()
    return {
        'generated_at': time.strftime('%Y-%m-%d %H:%M:%S'), 'root': root,
        'calendar_end': (str(cal[-1]) if cal else None),
        'n_nodes': len(nodes), 'n_lit': lit, 'n_features': len(feats),
        'waves': {str(k): len(v) for k, v in sorted(waves.items())},
        'nodes': nodes,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description='血缘更新快照 (dashboard 数据页)')
    ap.add_argument('--out', required=True, help='快照 JSON 输出路径')
    ap.add_argument('--root', default=os.getcwd(), help='仓库检出 (默认 cwd)')
    ap.add_argument('--features', default=None, help='模型特征清单 json (feature_names), 只用于页面高亮')
    a = ap.parse_args(argv)
    root = os.path.abspath(a.root)
    t0 = time.time()
    snap = build(root, features=a.features)
    tmp = a.out + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(snap, f, ensure_ascii=False)
    os.replace(tmp, a.out)
    print('snapshot: %d nodes, %d lit, %.1fs -> %s' % (snap['n_nodes'], snap['n_lit'], time.time() - t0, a.out), flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
