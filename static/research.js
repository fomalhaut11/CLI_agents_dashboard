// ===== 研究页签 (2026-10-26): 读研究库登记册, 卡片 + 详情 =====
// 研究库是独立 git 库, 钉基座某提交 (base.pin). 卡片的脏 / 提交只算本库; "pin 落后 main N" 是提示不是故障;
// 影响提示 ! = pin 之后基座改动碰到本库 import 的模块 (升级 pin 后需复核结果), ^ = 只改了同一顶层包里别的 .py
// (可能间接用到, 建议升级), o = 都没有. 数据 = 登记册 lineage_nodes 在血缘快照里的 strict 判定 (数据页点亮图同源).
// 已归档 (登记册 status: archived): 卡片变灰收进 "已归档", 不给升级 pin / 发起晋升 (2026-10-29 第 4 步).
// 没有 "同步 main" 按钮: 升级 pin 在详情页 "升级 pin" 区块 (对照 -> 提交 -> 回退, 都调基座 tools/research_pin.py).
let researchSel=null;

function researchBadges(r){
  const b=[];
  const dirty=r.staged+r.unstaged+r.untracked;
  b.push(dirty===0?'<span class="badge clean">clean</span>':`<span class="badge dirty">脏 ${dirty}</span>`);
  if(r.status&&r.status!=="active") b.push(`<span class="badge st-PARKED">${esc(r.status)}</span>`);
  if(r.pin){
    const behind=r.pin_behind;
    b.push(`<span class="badge rs-pin" title="base.pin ${esc(r.pin)}">pin ${esc(r.pin.slice(0,8))}</span>`);
    if(behind) b.push(`<span class="badge rs-behind" title="pin 之后基座 main 的提交数 (提示, 不是故障)">落后 main ${behind}</span>`);
    if(behind) b.push(r.impact_level==="!"
      ? `<span class="badge rs-bang" title="pin 之后改动碰到本库 import 的模块: ${esc(r.impact.join(", "))} (需升级并复核结果)">! 影响 ${r.impact.length}</span>`
      : r.impact_level==="^"
      ? `<span class="badge rs-soft" title="没改到本库直接 import 的模块, 但同一顶层包里有 ${r.impact_soft} 个 .py 改了 (可能间接用到, 建议升级)">^ 间接 ${r.impact_soft}</span>`
      : '<span class="badge rs-ok" title="pin 之后的基座改动没碰到本库 import 的模块及其所在包">o 无影响</span>');
  }
  const dt=r.data;
  if(dt){
    const bad=dt.stale.length+dt.unknown.length;
    const tip=dt.snapshot?`血缘快照 ${dt.snapshot} (strict 判定, 同数据页点亮图)`:"没有血缘快照: 数据页点亮图里生成";
    b.push(bad
      ? `<span class="badge ${dt.stale.length?"rs-bad":"rs-behind"}" title="${esc(tip)}; 不新鲜: ${esc(dt.stale.join(", ")||"-")}; 快照里没有: ${esc(dt.unknown.join(", ")||"-")}">数据 ${dt.fresh}/${dt.n} 新鲜</span>`
      : `<span class="badge rs-ok" title="${esc(tip)}">数据 ${dt.n} 新鲜</span>`);
  }
  if(!r.link_ok) b.push('<span class="badge rs-bad" title="pin 检出里 streams/&lt;名字&gt; 不是指向本库的联接: python tools/research_pin.py prepare &lt;pin&gt; --execute">联接缺</span>');
  if(!r.hook_ok) b.push('<span class="badge rs-bad" title="研究档位门没装: python tools/research_pin.py hook &lt;库&gt; --execute">门缺</span>');
  if(r.registered_pin&&r.pin&&r.registered_pin!==r.pin) b.push('<span class="badge rs-behind" title="登记册 base_pin 还是 '+esc(r.registered_pin.slice(0,8))+', 库内 base.pin 为准 (提示, 不是故障; 有空去 main 改 config/research_repos.yaml)">登记册待跟进</span>');
  if(r.knowledge) b.push(`<span class="badge rs-pin" title="已晋升到基座结论台账的现行结论 / 收件箱排队 (知识页签)">结论 ${r.knowledge.findings} · 排队 ${r.knowledge.queued}</span>`);
  const ro=r.requests_open;
  if(ro&&(ro.promote||ro.other)) b.push(`<span class="badge rs-behind" title="本库提给基座、仍未结的请求 (跨线请求台账; 基座开发页签的晋升队列)">晋升 ${ro.promote}${ro.other?" · 请求 "+ro.other:""}</span>`);
  return b.join("");
}

function researchCard(r){
  if(!r.exists) return `<div class="card rs-missing"><div class="row1"><span class="branch">${esc(r.name)}</span>
    <span class="name">本机无 (${esc(r.path||"路径未配置")})</span></div></div>`;
  const p=r.path.replace(/\\/g,"\\\\");
  const dirty=r.staged+r.unstaged+r.untracked;
  return `<div class="card${researchSel===r.name?" active":""}${r.archived?" rs-archived":""}" data-research="${esc(r.name)}">
    <div class="row1"><span class="branch">${esc(r.name)}</span><span class="name">${esc(r.branch)}</span></div>
    <div class="badges">${researchBadges(r)}</div>
    <div class="commits">${r.commits.map(c=>"· "+esc(c)).join("<br>")}</div>
    <div class="actions">
      <button class="primary" onclick="researchTerminal('${p}')">终端</button>
      <button onclick="openSessions('${p}','${esc(r.name)}')">会话</button>
      <button onclick="openDiff('${p}','${esc(r.name)}')">改动${dirty?` (${dirty})`:""}</button>
      <button onclick="researchDetail('${esc(r.name)}')">详情</button>
    </div></div>`;
}

async function loadResearch(){
  const left=document.getElementById("research-cards");
  try{
    const d=await jget("/api/research");
    if(!d.configured){ left.innerHTML='<div class="artempty">未配置研究库登记册 (config: RESEARCH_REGISTRY / researchRegistry)</div>'; return; }
    let html=`<div class="wipbar">研究库 ${d.repos.length} 个 · 基座 main ${esc(d.main)}`+
      (d.can_create?' <button onclick="researchNew()">+ 新建研究库</button>':"")+"</div>";
    if(d.unregistered.length) html+=`<div class="wipbar warn">研究库根下未登记: ${d.unregistered.map(esc).join(", ")}
      (把 research_new.py 打印的条目经 worktree 加进登记册)</div>`;
    const live=d.repos.filter(r=>!r.archived), arch=d.repos.filter(r=>r.archived);
    html+=live.map(researchCard).join("");
    if(arch.length) html+=`<details class="rs-archbox"><summary>已归档 (${arch.length})</summary>${arch.map(researchCard).join("")}</details>`;
    left.innerHTML=html;
    document.getElementById("meta").textContent=d.repos.length+" 研究库 · "+new Date().toLocaleTimeString();
  }catch(e){ left.innerHTML='<div class="artempty">加载失败: '+esc(String(e))+'</div>'; }
}

// 终端在 "基座开发" 页签的终端区 (两个页签共用终端); 后端按库内 base.pin 给新 shell 设 PYTHONPATH = 研究库根;pin 检出
// (2026-10-29, 不用再 . ./env.ps1; 标签前缀 "研", 悬停看 PYTHONPATH). Claude 会话读 .claude/settings.json, 同值.
function researchTerminal(path){
  document.querySelector('.tab[data-tab="streams"]').click();
  activePath=path;
  openTerminal(path);
}

async function researchDetail(name){
  researchSel=name;
  document.querySelectorAll("#research-cards .card").forEach(c=>c.classList.toggle("active",c.dataset.research===name));
  const box=document.getElementById("research-detail");
  box.innerHTML="加载中...";
  const r=await jget("/api/research/detail?name="+encodeURIComponent(name));
  if(r.error){ box.innerHTML=esc(r.error); return; }
  if(!r.exists){ box.innerHTML="本机没有这个研究库"; return; }
  const impact=(r.impact_files&&r.impact_files.length
    ? `<h4>! pin 之后改到本库 import 的基座文件 (${r.impact_files.length})</h4><pre class="rs-pre">${r.impact_files.map(esc).join("\n")}</pre>`
    : "")+(r.impact_soft_files&&r.impact_soft_files.length
    ? `<h4>^ 同一顶层包里别的改动 .py (${r.impact_soft_files.length}, 可能间接用到)</h4><pre class="rs-pre">${r.impact_soft_files.map(esc).join("\n")}</pre>`
    : "");
  const pinLog=r.pin_log&&r.pin_log.length
    ? `<h4>pin 之后的基座提交 (最近 ${r.pin_log.length})</h4><pre class="rs-pre">${r.pin_log.map(esc).join("\n")}</pre>` : "";
  const summary=r.summary
    ? (typeof marked!=="undefined" ? marked.parse(r.summary) : `<pre class="rs-pre">${esc(r.summary)}</pre>`)
    : '<div class="artempty">没有 streams/'+esc(name)+'/RESEARCH_SUMMARY.md</div>';
  const p=r.path.replace(/\\/g,"\\\\");
  const dirty=r.staged+r.unstaged+r.untracked;
  const ro=r.requests_open||{};
  const dt=r.data;
  const tabs=[["overview","概览"],["pin","升级 pin"+(r.pin_behind?" ("+r.pin_behind+")":"")],
    ["data","数据"+(dt?" ("+dt.fresh+"/"+dt.n+")":"")],
    ["promote","晋升与请求"+((ro.promote||ro.other)?" ("+((ro.promote||0)+(ro.other||0))+")":"")],["summary","RESEARCH_SUMMARY"]];
  const archBtn=r.archived
    ? `<button onclick="researchArchive('${esc(name)}',true)">取消归档</button>`
    : `<button onclick="researchArchive('${esc(name)}',false)" title="登记册 status 改为 archived (临时 worktree 提交 + --ff-only 进 main); 不动研究库与数据">归档</button>`;
  box.innerHTML=`<h3>${esc(name)} <span class="rs-sub">${esc(r.path)}</span></h3>
    ${r.archived?'<div class="wipbar warn">已归档 (登记册 status: archived): 只读, 不升级 pin、不发起晋升; 数据与研究库原样保留。</div>':""}
    <div class="badges">${researchBadges(r)}</div>
    <div class="rs-subtabs">${tabs.map(([k,t])=>`<button class="rs-subtab" data-sub="${k}" onclick="researchSubTab('${k}')">${esc(t)}</button>`).join("")}</div>
    <div class="rs-pane" data-sub="overview">
      <div class="rs-meta">base.pin <code>${esc(r.pin)}</code> · 数据命名空间 <code>${esc(r.data_namespace||"-")}</code>
        ${r.lineage_nodes.length?" · 血缘节点 "+r.lineage_nodes.map(esc).join(", "):""}</div>
      <div class="actions"><button class="primary" onclick="researchTerminal('${p}')">终端</button>
        <button onclick="openSessions('${p}','${esc(name)}')">会话</button>
        <button onclick="openDiff('${p}','${esc(name)}')">改动${dirty?` (${dirty})`:""}</button> ${archBtn}</div>
      <div class="rs-hint">终端 PYTHONPATH: ${r.term_pythonpath?`<code>${esc(r.term_pythonpath)}</code> (面板新开的终端自动设好, 按库内 base.pin; 升级 pin 后新开终端才换)`:esc(r.term_note||"未设")}</div>
      <h4>最近提交</h4><pre class="rs-pre">${r.commits.map(esc).join("\n")}</pre></div>
    <div class="rs-pane" data-sub="pin">${r.archived?'<div class="rs-hint">已归档, 不升级 pin</div>':'<div id="rs-pin">加载中...</div>'}${impact}${pinLog}</div>
    <div class="rs-pane" data-sub="data"><div id="rs-data">加载中...</div></div>
    <div class="rs-pane" data-sub="promote">
      <h4>晋升与请求 ${r.archived?"":`<button onclick="promoteForm('${esc(name)}')">发起晋升</button>`}</h4>
      <div id="rs-promote-form"></div><div id="rs-requests">加载中...</div></div>
    <div class="rs-pane" data-sub="summary"><div class="rs-summary md">${summary}</div></div>`;
  researchSubTab(researchSub);
  loadResearchRequests(name);
  if(!r.archived) loadPinBox(name);
  loadResearchData(name);
}

// ===== 数据 (2026-10-29, 设计稿 5.3 第 5 项): 本库血缘节点 + 上游 (多来自基座) / 下游 (谁在用) =====
// 判定读血缘快照 (数据页 "点亮图", strict judge, 与 manage.py data scan 同口径); 快照旧了到数据页刷新.
function nodeChip(n){
  const cls=!n.in_snapshot?"rs-behind":(n.fresh?"rs-ok":"rs-bad");
  const tip=n.in_snapshot?`${n.status} · ${n.why||""}${n.endpoint?" · 终点 "+n.endpoint:""}${n.frequency?" · "+n.frequency:""}`:"快照里没有";
  return `<span class="badge ${cls}" title="${esc(tip)}">${esc(n.name)}${n.owner!=="基座"?" @"+esc(n.owner):""}</span>`;
}
async function loadResearchData(name){
  const box=document.getElementById("rs-data");
  if(!box) return;
  const d=await jget("/api/research/data?name="+encodeURIComponent(name));
  if(researchSel!==name) return;
  if(d.error){ box.innerHTML=`<div class="wipbar warn">${esc(d.error)}</div>`; return; }
  const snap=d.missing_snapshot
    ? '<div class="wipbar warn">没有血缘快照: 到数据页签 "点亮图" 生成后再看</div>'
    : `<div class="rs-hint">血缘快照 ${esc(d.snapshot||"")} (${Math.round((d.age_s||0)/3600)} 小时前; strict 判定, 新鲜 = OK / FROZEN / STATIC)。快照旧了到数据页签 "点亮图" 刷新。</div>`;
  if(!d.nodes.length){ box.innerHTML=snap+'<div class="artempty">登记册里本库没有血缘节点 (lineage_nodes 为空)</div>'; return; }
  const rows=d.nodes.map(n=>`<tr class="${n.in_snapshot&&!n.fresh?"rq-overdue":""}"><td><b>${esc(n.name)}</b>
      <div class="rs-sub">${esc(n.frequency||"")}${n.wave!=null?" · wave "+n.wave:""}${n.frozen?" · frozen":""}</div></td>
    <td>${n.in_snapshot?esc(n.status):"快照里没有"}<div class="rs-sub">${esc(n.why||"")}</div></td><td>${esc(n.endpoint||"")}</td>
    <td>${n.upstream.map(nodeChip).join(" ")||"-"}${n.external.length?`<div class="rs-sub">外部 ${n.external.map(esc).join(", ")}</div>`:""}</td>
    <td>${n.downstream.map(nodeChip).join(" ")||"-"}</td></tr>`).join("");
  box.innerHTML=snap+`<table class="rq-table"><tr><th>本库节点</th><th>判定</th><th>内容终点</th><th>上游</th><th>下游 (谁在用)</th></tr>${rows}</table>
    <div class="rs-hint">上游 / 下游标签: 绿 = 新鲜, 红 = 不新鲜, 黄 = 快照里没有; "@库名" = 产者在别的研究库, 否则是基座节点。悬停看判据。</div>`;
}

// ===== 归档 (2026-10-29, 设计稿 5.4): 登记册 status -> archived / active, pin 工具 archive 子命令 (走 main) =====
async function researchArchive(name, unarchive){
  const what=unarchive?"取消归档":"归档";
  if(!confirm(`${what} ${name}?\n\n基座 pin 工具在临时 worktree 里把 config/research_repos.yaml 的 ${name}.status 改为 ${unarchive?"active":"archived"} 并提交, `+
    `再 --ff-only 进 main (过基座提交门, 可能几十秒)。研究库与数据不动。`)) return;
  const j=await jpost("/api/research/archive",{name,unarchive});
  if(j.rc!==0){ toast(j.out||"拒绝"); return; }
  await startProcAt(j.path,j.cmd,what+" "+name);
  const id=procSel;
  toast(what+"进行中 (进程 #"+id+", 日志在进程页签)");
  const poll=async()=>{
    const p=(await jget("/api/proc/list")).find(x=>x.id===id);
    if(p&&p.status==="running"){ setTimeout(poll,3000); return; }
    toast(p&&p.rc===0?what+"完成":what+"失败, 看进程页签日志");
    loadResearch(); if(researchSel===name) researchDetail(name);
  };
  setTimeout(poll,3000);
}

// 详情分页签 (2026-10-29): 概览 / 升级 pin / 晋升与请求 / RESEARCH_SUMMARY; 记住上次停在哪个 (换库也保持)
let researchSub=(()=>{ try{ return localStorage.getItem("rs-sub")||"overview"; }catch(e){ return "overview"; } })();
function researchSubTab(k){
  const box=document.getElementById("research-detail");
  if(!box.querySelector(`.rs-pane[data-sub="${k}"]`)) k="overview";
  researchSub=k;
  try{ localStorage.setItem("rs-sub",k); }catch(e){}
  box.querySelectorAll(".rs-subtab").forEach(b=>b.classList.toggle("active",b.dataset.sub===k));
  box.querySelectorAll(".rs-pane").forEach(x=>x.classList.toggle("active",x.dataset.sub===k));
}

// ===== 升级 pin 页面化 (2026-10-29): 选基座提交 -> 对照 (旧 / 新 pin 下本库测试 + import 判定) -> 提交 -> 可一键回退 =====
// 三步都跑基座 tools/research_pin.py (trial / upgrade --commit / revert); 提交要求同一 HEAD 上 "无新增失败" 的对照报告.
function pinSide(s){ return s.ran ? `${s.n}${s.summary?" ("+esc(s.summary)+")":""}` : `未跑${s.summary?" ("+esc(s.summary)+")":""}`; }
function pinDiffList(c){
  const rows=c.only_new.map(x=>`+ 新 pin 才有  ${esc(x)}`).concat(c.only_old.map(x=>`- 旧 pin 才有  ${esc(x)}`));
  return rows.length?`<pre class="rs-pre">${rows.join("\n")}</pre>`:"";
}
function pinTrialRow(t, pin){
  const ok=t.verdict==="same";
  const why=t.usable?"可提交":(t.old!==pin?"旧 pin 已不是当前 pin":(t.repo_dirty?"对照时工作区不干净":(!ok?"新 pin 下多出失败":"之后有新提交, 需重跑")));
  return `<tr class="${ok?"":"rq-overdue"}"><td><code>${esc(t.old.slice(0,8))}</code> → <code>${esc(t.new.slice(0,8))}</code></td>
    <td>${ok?"o 无新增失败":"! 有新增失败"}</td><td>${esc(t.started)}${t.seconds!=null?" · "+t.seconds+"s":""}</td>
    <td>测试 旧 ${pinSide(t.tests.old)} / 新 ${pinSide(t.tests.new)}${pinDiffList(t.tests)}
      <div>import 落空 旧 ${pinSide(t.imports.old)} / 新 ${pinSide(t.imports.new)}</div>${pinDiffList(t.imports)}</td>
    <td>${t.usable?`<button class="primary" onclick="pinUpgrade('${esc(t.new)}')">提交升级</button>`:esc(why)}</td></tr>`;
}
async function loadPinBox(name){
  const box=document.getElementById("rs-pin");
  if(!box||researchSel!==name) return;
  const d=await jget("/api/research/pin_state?name="+encodeURIComponent(name));
  if(d.error){ box.innerHTML=`<div class="wipbar warn">${esc(d.error)}</div>`; return; }
  const opts=d.commits.map((c,i)=>`<option value="${esc(c.sha)}">${esc(c.sha.slice(0,8))} ${esc(c.date)} ${esc(c.subject.slice(0,70))}${i===0?"  (main 最新)":""}</option>`).join("");
  const pick=d.commits.length
    ? `<div class="rq-form"><label>新 pin <select id="pin-rev">${opts}</select></label>
        <div><button class="primary" ${d.enabled?"":"disabled"} onclick="pinTrial()">对照 (旧 pin vs 选中提交)</button></div>
        <div class="rs-hint">对照 = 基座 <code>research_pin.py trial</code> 受管进程 (日志在进程页签): 同一份本库代码分别在旧 / 新 pin 下跑
        tests/streams/${esc(name)} 与研究档位 import 判定, 新 pin 下没有多出失败才能提交。缺的 pin 检出会先建。${d.dirty?" <b>本库工作区不干净: 对照结论不能用于提交, 先在本库提交或清理。</b>":""}</div></div>`
    : '<div class="rs-hint">pin 与基座 main 一致</div>';
  const trials=d.trials.length
    ? `<table class="rq-table"><tr><th>对照</th><th>结论</th><th>时间</th><th>细节</th><th></th></tr>${d.trials.map(t=>pinTrialRow(t,d.pin)).join("")}</table>` : "";
  const lu=d.last_upgrade;
  const last=lu
    ? `<div class="rs-hint">最近一次页面/工具升级: <code>${esc(lu.commit.slice(0,8))}</code> ${esc(lu.line)}
        (<code>${esc(lu.old.slice(0,8))}</code> → <code>${esc(lu.new.slice(0,8))}</code>)
        ${lu.revertable&&d.enabled?` <button onclick="pinRevert()">回退到 ${esc(lu.old.slice(0,8))}</button>`:(lu.revertable?"":" · 之后 pin 已变, 不可一键回退")}</div>` : "";
  box.innerHTML=pick+trials+last+(d.enabled?"":'<div class="rs-hint">本面板未配置 pin 工具命令 (researchPinCmd), 只读</div>')+
    '<div class="rs-hint">登记册 config/research_repos.yaml 的 base_pin 在基座 main 上, 页面不改; 升级 / 回退后卡片显示黄色 "登记册待跟进" (提示, 以库内 base.pin 为准), 有空去 main 的 worktree 改。</div>';
}
async function pinTrial(){
  const name=researchSel, rev=document.getElementById("pin-rev").value;
  const j=await jpost("/api/research/pin_trial",{name,rev});
  if(j.rc!==0){ toast(j.out||"拒绝"); return; }
  await startProcAt(j.path,j.cmd,"pin 对照 "+name+" -> "+rev.slice(0,8));
  const id=procSel;
  toast("对照已启动 (进程 #"+id+"), 跑完自动刷新");
  const box=document.getElementById("rs-pin");
  if(box) box.insertAdjacentHTML("afterbegin",`<div class="wipbar" id="pin-running">对照进行中 (进程 #${id}, 日志在进程页签)...</div>`);
  const poll=async()=>{
    if(researchSel!==name) return;
    const p=(await jget("/api/proc/list")).find(x=>x.id===id);
    if(p&&p.status==="running"){ setTimeout(poll,5000); return; }
    loadPinBox(name);
  };
  setTimeout(poll,5000);
}
async function pinUpgrade(rev){
  const name=researchSel;
  if(!confirm(`把 ${name} 的 base.pin 升到 ${rev.slice(0,8)} 并在本库提交?\n(改 base.pin / env.ps1 / .claude/settings.json / CLAUDE.md; 过本库研究档位门)`)) return;
  const j=await jpost("/api/research/pin_upgrade",{name,rev});
  alert((j.rc===0?"已提交\n\n":"失败\n\n")+(j.out||""));
  researchDetail(name); loadResearch();
}
async function pinRevert(){
  const name=researchSel;
  if(!confirm(`回退 ${name} 最近一次 pin 升级 (git revert, 本库新增一个提交)?`)) return;
  const j=await jpost("/api/research/pin_revert",{name});
  alert((j.rc===0?"已回退\n\n":"失败\n\n")+(j.out||""));
  researchDetail(name); loadResearch();
}

// ===== 晋升 (2026-10-27, H-106): 研究库发起 -> 基座跨线请求台账 (kind=promote) -> 基座开发页签的晋升队列 =====
function reqRow(q, withLand){
  const promo=q.kind==="promote";
  const land=withLand&&q.land ? `<div><button class="primary" onclick="landRequest('${esc(q.id)}')" title="开基座 worktree (分支 land/&lt;id&gt;, 从 main) + 任务书, 在那里起 Claude 会话按契约落地">${q.land_wt?"继续处理 (worktree 已开)":"一键处理"}</button></div>
    <details><summary class="rs-sub">手工命令</summary><pre class="rs-pre">${q.land.map(esc).join("\n")}</pre></details>` : "";
  return `<tr class="${q.overdue?"rq-overdue":""}"><td><code>${esc(q.id)}</code></td><td>${esc(q.from)}</td>
    <td>${esc(q.kind)}${promo&&q.artifact?" / "+esc(q.artifact):""}</td><td>${esc(q.status)}</td>
    <td>${q.age_days}d${q.overdue?" 超期":""}</td>
    <td>${esc(q.title)}${promo&&q.target?`<div class="rs-sub">落点 ${esc(q.target)}</div>`:""}
      <div class="rs-sub">依据 ${esc(q.evidence_ref||"")}</div>${land}</td></tr>`;
}
function reqTable(rows, withLand){
  if(!rows.length) return '<div class="artempty">没有</div>';
  return `<table class="rq-table"><tr><th>id</th><th>来自</th><th>类型</th><th>状态</th><th>等待</th><th>标题 / 落点 / 依据</th></tr>${
    rows.map(q=>reqRow(q,withLand)).join("")}</table>`;
}
async function loadResearchRequests(name, refresh){
  const box=document.getElementById("rs-requests");
  if(!box) return;
  const d=await jget("/api/research/requests?name="+encodeURIComponent(name)+(refresh?"&refresh=1":""));
  box.innerHTML=(d.error?`<div class="wipbar warn">${esc(d.error)}</div>`:"")+reqTable(d.requests||[], false)+
    '<div class="rs-hint">请求 = 本库 main 上提交的 requests/'+esc(name)+'.jsonl; 基座 main 处理 (ack / close / decline)。撤回: 本库里 <code>python &lt;基座&gt;/tools/harness/requests.py withdraw &lt;id&gt; --reason "..."</code> 后提交。</div>';
}
function promoteForm(name){
  const box=document.getElementById("rs-promote-form");
  const opts=["factor","dataset","tool","finding","signal","other"].map(a=>`<option>${a}</option>`).join("");
  box.innerHTML=`<div class="rq-form">
    <label>成果类型 <select id="pf-artifact">${opts}</select></label>
    <label>基座落点 <input id="pf-target" placeholder="factors/repository/technical/ 或 config/derived_data.yaml"></label>
    <label>标题 <input id="pf-title" placeholder="一句话: 晋升什么"></label>
    <label>依据 <input id="pf-evidence" placeholder="库内路径 / 报告 / 提交 (成果与证据在哪)"></label>
    <label>涉及路径 <input id="pf-paths" placeholder="可选, 逗号分隔 (库内路径)"></label>
    <div><button class="primary" onclick="promoteSubmit('${esc(name)}')">写请求并提交到本库</button>
      <button onclick="document.getElementById('rs-promote-form').innerHTML=''">取消</button></div>
    <div class="rs-hint">只写并提交本库的 requests/${esc(name)}.jsonl (须在本库 main 分支上), 不改基座; 落地由基座在 worktree 里按契约做。</div></div>`;
}
async function promoteSubmit(name){
  const v=id=>(document.getElementById(id).value||"").trim();
  const body={name, artifact:v("pf-artifact"), target:v("pf-target"), title:v("pf-title"), evidence:v("pf-evidence"),
    paths:v("pf-paths").split(",").map(s=>s.trim()).filter(Boolean)};
  if(!body.target||!body.title||!body.evidence){ toast("落点 / 标题 / 依据 都要填"); return; }
  const j=await jpost("/api/research/promote", body);
  if(j.rc!==0){ toast(j.out||"失败"); return; }
  toast("已提交晋升请求 "+j.id);
  document.getElementById("rs-promote-form").innerHTML="";
  loadResearchRequests(name, true);
  loadPromoBar(true);
}

// 基座开发页签顶部: 晋升队列计数 + 模态 (全部研究库的未结请求, 晋升优先, 再按等待天数)
async function loadPromoBar(refresh){
  const bar=document.getElementById("promobar");
  if(!bar) return;
  try{
    const d=await jget("/api/promotion"+(refresh?"?refresh=1":""));
    bar.className="wipbar"+(d.n_overdue?" warn":"");
    bar.innerHTML=`晋升队列: 晋升 ${d.n_promote} · 未结请求共 ${d.queue.length} (研究库 ${d.n_research}${d.n_overdue?" · 超期 "+d.n_overdue:""})
      <button onclick="openPromo()">查看</button> <button onclick="openOverview()" title="pin 分布 + 影响预览">基座总览</button>`+
      (d.error?` <span title="${esc(d.error)}">台账读不了</span>`:"");
  }catch(e){ bar.textContent="晋升队列读不了: "+e; }
}
async function openPromo(){
  const m=document.getElementById("promomodal");
  m.style.display="flex";
  const body=document.getElementById("promobody");
  body.innerHTML="加载中...";
  const d=await jget("/api/promotion?refresh=1");
  body.innerHTML=(d.error?`<div class="wipbar warn">${esc(d.error)}</div>`:"")+
    '<div class="rs-hint">处理在基座 main 的 worktree 里做 (页面不写台账): ack -> 开 worktree 按契约落地 (测试 / 血缘 / 前视审计 / 门) -> --ff-only 进 main -> close 引用落地提交。设计稿 research_repos_design 4.4 / 5.5。</div>'+
    reqTable(d.queue, true);
}
// 一键处理 (2026-10-29, 设计稿 5.4): 后端开 worktree + 写任务书 (worktree 的 git 目录里 LAND_BRIEF.md), 这里在该
// worktree 开终端标签起 Claude 会话; ack / 落地 / --ff-only / close 由会话按任务书做 (ack 前先问人), 页面不写台账.
async function landRequest(id){
  if(!confirm(`处理请求 ${id}?\n\n开 (或复用) 基座 worktree + 分支 land/${id.replace(/_/g,"-")}, 写任务书, 在那里新开 Claude 会话。\n会话会先读依据, ack 之前问你。`)) return;
  const j=await jpost("/api/promotion/land",{id});
  if(j.rc!==0){ toast(j.out||"失败"); return; }
  document.getElementById("promomodal").style.display="none";
  document.querySelector('.tab[data-tab="streams"]').click();
  activePath=j.path;
  newTerm(j.path,"claude",j.cmd);
  toast((j.created?"已开 worktree ":"复用 worktree ")+j.path+" · 任务书 "+j.brief);
  refreshStreams();
}

// ===== 基座总览 (2026-10-27, 设计稿 5.5): pin 分布 + 影响预览 =====
// pin 分布: 各研究库钉的基座提交 / 落后 main / 过旧高亮; 影响预览: main 上某次提交或某 worktree 未进 main 的改动
// 碰到了哪些研究库 import 的模块 (只算 .py, 与研究卡片的影响提示同判据)
async function openOverview(){
  const m=document.getElementById("overviewmodal");
  m.style.display="flex";
  const body=document.getElementById("overviewbody");
  body.innerHTML="加载中...";
  const d=await jget("/api/base_overview");
  const pinRows=d.pins.map(p=>`<tr class="${p.stale?"rq-overdue":""}"><td>${esc(p.name)}</td>
    <td>${p.exists?`<code>${esc((p.pin||"").slice(0,8))}</code>`:"本机无"}</td><td>${esc(p.pin_date||"")}</td>
    <td>${p.behind==null?"":p.behind}</td><td>${p.age_days==null?"":p.age_days+"d"}</td>
    <td>${p.stale?"过旧":""}${p.registered_pin&&p.pin&&p.registered_pin!==p.pin?" 登记册待跟进":""}${p.status!=="active"?" "+esc(p.status):""}</td></tr>`).join("");
  const groups=d.pin_groups.map(g=>`<code>${esc(g.pin.slice(0,8))}</code>: ${g.repos.map(esc).join(", ")}`).join(" · ");
  const opts=d.commits.map(c=>`<option value="rev:${esc(c.sha)}">${esc(c.sha.slice(0,8))} ${esc(c.date)} ${esc(c.subject.slice(0,70))}</option>`).join("")+
    d.worktrees.map(w=>`<option value="path:${esc(w.path)}">worktree ${esc(w.branch)} (${esc(w.path)}) 未进 main 的改动</option>`).join("");
  body.innerHTML=`<div class="rs-meta">基座 main ${esc(d.main)}${d.main_dirty?` · main 检出有 ${d.main_dirty} 个未提交文件`:" · main 检出干净"}</div>
    <h4>pin 分布 (过旧 = 落后 main &gt; ${d.stale_commits} 个提交或 &gt; ${d.stale_days} 天)</h4>
    <div class="rs-hint">${groups||"没有研究库"}</div>
    <table class="rq-table"><tr><th>研究库</th><th>pin</th><th>pin 日期</th><th>落后 main</th><th>pin 天数</th><th></th></tr>${pinRows}</table>
    <h4>影响预览: 这次改动会碰到哪些研究库</h4>
    <div class="rq-form"><label>改动 <select id="ov-src">${opts}</select></label>
      <div><button class="primary" onclick="runImpact()">预览</button></div></div>
    <div id="ov-impact"></div>`;
}
async function runImpact(){
  const v=document.getElementById("ov-src").value;
  const box=document.getElementById("ov-impact");
  box.innerHTML="计算中...";
  const q=v.startsWith("rev:")?"rev="+encodeURIComponent(v.slice(4)):"path="+encodeURIComponent(v.slice(5));
  const d=await jget("/api/impact_preview?"+q);
  if(d.error){ box.innerHTML=`<div class="wipbar warn">${esc(d.error)}</div>`; return; }
  const rows=d.repos.map(r=>`<tr class="${r.level==="!"?"rq-overdue":""}"><td>${esc(r.name)}</td>
    <td>${r.level==="!"?"! "+r.hits.length:(r.level==="^"?"^ "+r.soft.length:"o")}</td>
    <td>${r.hits.length?`<pre class="rs-pre">${r.hits.map(esc).join("\n")}</pre>`:""}${r.soft.length?`<div class="rs-sub">^ 同包别的 .py:</div><pre class="rs-pre">${r.soft.map(esc).join("\n")}</pre>`:""}${r.level==="o"?"没碰到本库 import 的模块及其所在包":""}</td></tr>`).join("");
  box.innerHTML=`<div class="rs-hint">${esc(d.label)}: 改了 ${d.changed.length} 个文件 (.py ${d.n_py}); ! 受影响 ${d.affected.length?d.affected.map(esc).join(", "):"无"}; ^ 可能间接 ${d.maybe&&d.maybe.length?d.maybe.map(esc).join(", "):"无"}。
    判据: ! = 被改的 .py 是研究库 import 的基座模块 (或其包下文件); ^ = 只改了被 import 模块所在顶层包里别的 .py。只是近似, 不看改的是不是接口。</div>
    <table class="rq-table"><tr><th>研究库</th><th>影响</th><th>改到的文件 &lt;- 本库 import 的模块</th></tr>${rows}</table>`;
}
setInterval(()=>{ if(document.getElementById("tab-streams").classList.contains("active")) loadPromoBar(); },60000);
loadPromoBar();

async function researchNew(){
  const name=(prompt("新研究库名 (小写字母开头, 小写字母/数字/下划线):")||"").trim();
  if(!name) return;
  if(!/^[a-z][a-z0-9_]{1,40}$/.test(name)){ toast("名字不合规: "+name); return; }
  const j=await jpost("/api/research/new",{name});
  if(j.rc!==0){ toast(j.out||"拒绝"); return; }
  if(!confirm("在基座检出里以受管进程运行:\n"+j.cmd+"\n\n(建研究库目录 / pin / 联接 / 门 / 首次提交; 登记册条目要另行落 main)")) return;
  await startProcAt(j.path,j.cmd,"新建研究库 "+name);
  document.querySelector('.tab[data-tab="procs"]').click();
}
