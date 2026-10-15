// ===== 数据页: 血缘点亮图 (2026-10-15) =====
// 数据源: /api/data/lineage (快照 JSON, 由 tools/stream_dashboard/lineage_snapshot.py 以 managed process 生成)。
// 列 = wave (拓扑层, 来自 config/derived_data.yaml 的 upstream), 芯片 = 节点, 点亮 = strict judge 为 OK/FROZEN/STATIC。
// 点一个节点: 高亮它的全部上游闭包 + 直接下游, 画连线, 右侧给出上游树 (树状展开) 与最近一次运行结果。
const LIN = {snap: null, sel: null, filter: "", onlyBad: false, onlyFeat: false, showAll: false, byName: {}, down: {}};
const LIN_BIG = 60;   // 一列超过这个数时默认只显示 非OK / 模型特征 / 搜索命中, 其余折叠成计数

function linLit(n){ return n.status === "OK" || n.status === "FROZEN" || n.status === "STATIC"; }
function linCls(n){
  if (n.status === "OK") return "ok";
  if (n.status === "FROZEN" || n.status === "STATIC") return "frozen";
  if (n.status === "STALE") return "stale";
  if (n.status === "MISSING" || n.status === "UNREADABLE" || n.status === "ERR") return "missing";
  return "unknown";
}
function linRunCls(n){ const r = n.last_run; if (!r) return ""; return "run-" + (r.status || "").toLowerCase() + (r.today ? " today" : ""); }

async function loadLineage(){
  const wrap = document.getElementById("lineagewrap");
  if (!wrap) return;
  const d = await jget("/api/data/lineage");
  if (!d || !d.nodes) {
    wrap.innerHTML = `<div class="linhead"><b>血缘点亮图</b>
      <span class="dim">尚无快照。</span>
      <button class="primary" onclick="refreshLineage()">生成快照</button>
      <span class="dim">在当前选中的 worktree 里跑 lineage_snapshot.py (约 1-2 分钟, 可在“进程”页看日志)</span></div>`;
    return;
  }
  LIN.snap = d; LIN.byName = {}; LIN.down = {};
  d.nodes.forEach(n => { LIN.byName[n.name] = n; });
  d.nodes.forEach(n => n.upstream.forEach(u => { (LIN.down[u] = LIN.down[u] || []).push(n.name); }));
  if (LIN.sel && !LIN.byName[LIN.sel]) LIN.sel = null;
  renderLineage();
}

async function refreshLineage(){
  if (!activePath) { toast("先在 Streams 页点某条线的「终端」选个 worktree (日更在哪跑就选哪)"); return; }
  const r = await jpost("/api/data/lineage/refresh", {path: activePath});
  if (r && r.rc === 0) toast("快照生成中 #" + r.id + ", 完成后点「重载」");
  else toast((r && r.out) || "启动失败");
}

function linAncestors(name){
  const seen = new Set(); const stack = [name];
  while (stack.length) { const x = stack.pop(); const n = LIN.byName[x]; if (!n) continue;
    n.upstream.forEach(u => { if (!seen.has(u)) { seen.add(u); stack.push(u); } }); }
  return seen;
}
function linVisible(n, col){
  if (LIN.filter && n.name.toLowerCase().indexOf(LIN.filter) < 0) return false;
  if (LIN.onlyBad && linLit(n)) return false;
  if (LIN.onlyFeat && !n.feature) return false;
  if (LIN.sel) {   // 选中态: 只看闭包
    return n.name === LIN.sel || LIN._anc.has(n.name) || (LIN.down[LIN.sel] || []).indexOf(n.name) >= 0;
  }
  if (!LIN.showAll && !LIN.filter && col.length > LIN_BIG) return !linLit(n) || n.feature;
  return true;
}

function renderLineage(){
  const d = LIN.snap, wrap = document.getElementById("lineagewrap");
  const cols = {};
  d.nodes.forEach(n => { (cols[n.wave] = cols[n.wave] || []).push(n); });
  const waves = Object.keys(cols).map(Number).sort((a, b) => a - b);
  LIN._anc = LIN.sel ? linAncestors(LIN.sel) : new Set();
  const todayBad = d.nodes.filter(n => n.last_run && n.last_run.today && ["FAIL", "TIMEOUT", "NOOP"].indexOf(n.last_run.status) >= 0).length;
  const head = `<div class="linhead">
      <b>血缘点亮图</b>
      <span class="dim">快照 ${esc(d.generated_at)} | ${esc(d.root)} | 日历止 ${esc(d.calendar_end || "?")} |
        点亮 <span class="fresh">${d.n_lit}</span> / ${d.n_nodes} | 今天运行失败 <span class="${todayBad ? "stale" : "fresh"}">${todayBad}</span></span>
      <input id="lin-filter" placeholder="搜节点名" value="${esc(LIN.filter)}" oninput="LIN.filter=this.value.toLowerCase(); LIN.sel=null; renderLineage()">
      <label><input type="checkbox" ${LIN.onlyBad ? "checked" : ""} onchange="LIN.onlyBad=this.checked; renderLineage()"> 只看未点亮</label>
      <label><input type="checkbox" ${LIN.onlyFeat ? "checked" : ""} onchange="LIN.onlyFeat=this.checked; renderLineage()"> 只看模型特征 (${d.n_features})</label>
      <label><input type="checkbox" ${LIN.showAll ? "checked" : ""} onchange="LIN.showAll=this.checked; renderLineage()"> 大列全展开</label>
      ${LIN.sel ? `<button onclick="LIN.sel=null; renderLineage()">取消选中</button>` : ""}
      <button onclick="loadLineage()">重载</button>
      <button class="primary" onclick="refreshLineage()">重新生成快照</button>
    </div>
    <div class="linlegend">
      <span class="linchip ok">OK 点亮</span><span class="linchip stale">STALE</span><span class="linchip missing">MISSING / 读不出</span>
      <span class="linchip frozen">冻结 / 静态</span><span class="linchip unknown">UNKNOWN</span>
      <span class="dim">左上角小点 = 最近一次运行: <i class="rundot run-ok"></i>OK <i class="rundot run-skip"></i>SKIP <i class="rundot run-fail"></i>FAIL/TIMEOUT/NOOP (实心 = 今天)</span>
      <span class="dim">★ = round23 模型特征</span>
    </div>`;
  const colHtml = waves.map(k => {
    const col = cols[k];
    const lit = col.filter(linLit).length;
    const vis = col.filter(n => linVisible(n, col));
    const hidden = col.length - vis.length;
    const chips = vis.map(n => {
      const cls = ["linchip", linCls(n), linRunCls(n)];
      if (LIN.sel === n.name) cls.push("sel");
      else if (LIN.sel && LIN._anc.has(n.name)) cls.push("up");
      else if (LIN.sel && (LIN.down[LIN.sel] || []).indexOf(n.name) >= 0) cls.push("down");
      const tip = `${n.status} ${n.endpoint || ""}\n${n.why}` + (n.last_run ? `\n最近运行 ${n.last_run.ts} ${n.last_run.status} ${n.last_run.sec}s` : "");
      return `<span class="${cls.join(" ")}" data-n="${esc(n.name)}" title="${esc(tip)}" onclick="linSelect('${esc(n.name)}')">` +
        `<i class="rundot ${linRunCls(n)}"></i>${n.feature ? "★" : ""}${esc(n.name)}</span>`;
    }).join("");
    return `<div class="lincol" data-wave="${k}">
      <div class="lincolhead">wave ${k} <span class="dim">${lit}/${col.length}</span>${hidden ? `<span class="dim"> (折叠 ${hidden})</span>` : ""}</div>
      <div class="lincolbody">${chips || '<span class="dim">无</span>'}</div></div>`;
  }).join("");
  wrap.innerHTML = head + `<div class="linbody"><div class="lingrid" id="lingrid"><svg class="linsvg" id="linsvg"></svg>${colHtml}</div>${linDetailHtml()}</div>`;
  linDrawEdges();
}

function linSelect(name){ LIN.sel = (LIN.sel === name) ? null : name; renderLineage(); }

function linTreeHtml(name, depth, seen){
  const n = LIN.byName[name]; if (!n) return `<div class="lintree" style="padding-left:${depth * 14}px">${esc(name)} <span class="dim">(仓外/未登记)</span></div>`;
  const dup = seen.has(name); seen.add(name);
  let h = `<div class="lintree" style="padding-left:${depth * 14}px"><span class="linchip ${linCls(n)}" onclick="linSelect('${esc(name)}')">${esc(name)}</span>
    <span class="dim">w${n.wave} ${esc(n.endpoint || "-")}</span>${dup ? '<span class="dim"> (已展开)</span>' : ""}</div>`;
  if (!dup && depth < 8) n.upstream.forEach(u => { h += linTreeHtml(u, depth + 1, seen); });
  if (!dup && n.external.length) h += `<div class="lintree dim" style="padding-left:${(depth + 1) * 14}px">${esc(n.external.join(", "))}</div>`;
  return h;
}
function linDetailHtml(){
  if (!LIN.sel) return `<div class="lindetail dim">点一个节点看上游树、下游与最近运行。</div>`;
  const n = LIN.byName[LIN.sel]; const r = n.last_run;
  const down = (LIN.down[n.name] || []);
  return `<div class="lindetail">
    <div class="lindtitle"><span class="linchip ${linCls(n)}">${esc(n.name)}</span> <span class="dim">wave ${n.wave} | ${esc(n.layer)} | ${esc(n.frequency)}${n.frozen ? " | frozen" : ""}${n.feature ? " | round23 特征" : ""}</span></div>
    <div><b>判定</b> ${esc(n.status)} <span class="dim">${esc(n.why)}</span></div>
    <div><b>内容终点</b> ${esc(n.endpoint || "-")}</div>
    <div><b>命令</b> <code>${esc(n.cmd || "(无 daily_call)")}</code> ${n.budget ? `<span class="dim">预算 ${n.budget}s${n.heavy ? " 重活" : ""}</span>` : ""}</div>
    <div><b>最近运行</b> ${r ? `${esc(r.ts)} <span class="${r.status === "OK" || r.status === "SKIP" ? "fresh" : "stale"}">${esc(r.status)}</span> ${r.sec}s <span class="dim">${esc(r.note)}</span>` : '<span class="dim">无记录</span>'}</div>
    <div><b>上游树</b> (${LIN._anc.size} 个祖先)</div>${linTreeHtml(n.name, 0, new Set())}
    <div style="margin-top:6px"><b>直接下游</b> (${down.length})</div>
    <div>${down.map(x => { const m = LIN.byName[x]; return `<span class="linchip ${linCls(m)}" onclick="linSelect('${esc(x)}')">${esc(x)}</span>`; }).join(" ") || '<span class="dim">无</span>'}</div>
  </div>`;
}

function linDrawEdges(){
  const svg = document.getElementById("linsvg"), grid = document.getElementById("lingrid");
  if (!svg || !grid) return;
  svg.setAttribute("width", grid.scrollWidth); svg.setAttribute("height", grid.scrollHeight);
  if (!LIN.sel) { svg.innerHTML = ""; return; }
  const g = grid.getBoundingClientRect();
  const pos = {};
  grid.querySelectorAll(".linchip[data-n]").forEach(el => {
    const r = el.getBoundingClientRect();
    pos[el.dataset.n] = {x1: r.left - g.left + grid.scrollLeft, x2: r.right - g.left + grid.scrollLeft, y: r.top - g.top + grid.scrollTop + r.height / 2};
  });
  const lines = [];
  const edge = (a, b, cls) => { const p = pos[a], q = pos[b]; if (!p || !q) return;
    const x1 = p.x2, y1 = p.y, x2 = q.x1, y2 = q.y, mx = (x1 + x2) / 2;
    lines.push(`<path class="linedge ${cls}" d="M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}"/>`); };
  // 上游闭包内的全部边 (树状), 加选中节点到直接下游的边
  const inside = new Set(LIN._anc); inside.add(LIN.sel);
  inside.forEach(x => { const n = LIN.byName[x]; if (n) n.upstream.forEach(u => { if (inside.has(u)) edge(u, x, "up"); }); });
  (LIN.down[LIN.sel] || []).forEach(x => edge(LIN.sel, x, "down"));
  svg.innerHTML = lines.join("");
}
window.addEventListener("resize", () => { if (LIN.snap) linDrawEdges(); });
