// ===== 研究页签 (2026-10-26): 读研究库登记册, 卡片 + 详情 =====
// 研究库是独立 git 库, 钉基座某提交 (base.pin). 卡片的脏 / 提交只算本库; "pin 落后 main N" 是提示不是故障;
// 影响提示 o = pin 之后基座改动没碰到本库 import 的模块, ! = 碰到了 (升级 pin 后需复核结果).
// 没有 "同步 main" 按钮: 升级 pin 走基座 python tools/research_pin.py upgrade <库> <rev>.
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
    if(behind) b.push(r.impact.length
      ? `<span class="badge rs-bang" title="pin 之后改动碰到本库 import 的模块: ${esc(r.impact.join(", "))}">! 影响 ${r.impact.length}</span>`
      : '<span class="badge rs-ok" title="pin 之后的基座改动没碰到本库 import 的模块">o 无影响</span>');
  }
  if(!r.link_ok) b.push('<span class="badge rs-bad" title="pin 检出里 streams/&lt;名字&gt; 不是指向本库的联接: python tools/research_pin.py prepare &lt;pin&gt; --execute">联接缺</span>');
  if(!r.hook_ok) b.push('<span class="badge rs-bad" title="研究档位门没装: python tools/research_pin.py hook &lt;库&gt; --execute">门缺</span>');
  if(r.registered_pin&&r.pin&&r.registered_pin!==r.pin) b.push('<span class="badge rs-bad" title="登记册 base_pin 与库内 base.pin 不一致">登记册不一致</span>');
  if(r.knowledge) b.push(`<span class="badge rs-pin" title="已晋升到基座结论台账的现行结论 / 收件箱排队 (知识页签)">结论 ${r.knowledge.findings} · 排队 ${r.knowledge.queued}</span>`);
  const ro=r.requests_open;
  if(ro&&(ro.promote||ro.other)) b.push(`<span class="badge rs-behind" title="本库提给基座、仍未结的请求 (跨线请求台账; 基座开发页签的晋升队列)">晋升 ${ro.promote}${ro.other?" · 请求 "+ro.other:""}</span>`);
  if(r.lineage_nodes.length) b.push(`<span class="badge rs-pin" title="${esc(r.lineage_nodes.join(", "))}">血缘节点 ${r.lineage_nodes.length}</span>`);
  return b.join("");
}

function researchCard(r){
  if(!r.exists) return `<div class="card rs-missing"><div class="row1"><span class="branch">${esc(r.name)}</span>
    <span class="name">本机无 (${esc(r.path||"路径未配置")})</span></div></div>`;
  const p=r.path.replace(/\\/g,"\\\\");
  const dirty=r.staged+r.unstaged+r.untracked;
  return `<div class="card${researchSel===r.name?" active":""}" data-research="${esc(r.name)}">
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
    html+=d.repos.map(researchCard).join("");
    left.innerHTML=html;
    document.getElementById("meta").textContent=d.repos.length+" 研究库 · "+new Date().toLocaleTimeString();
  }catch(e){ left.innerHTML='<div class="artempty">加载失败: '+esc(String(e))+'</div>'; }
}

// 终端在 "基座开发" 页签的终端区 (两个页签共用终端); 新开 shell 先 . ./env.ps1 设 PYTHONPATH (Claude 会话读 .claude/settings.json)
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
  const pinHint=r.pin_behind
    ? `<div class="rs-hint">升级 pin (基座 main 检出里跑, 默认只打印计划):<br><code>python tools/research_pin.py prepare main</code> → 用新 PYTHONPATH 跑本库测试与旧 pin 对照 → <code>python tools/research_pin.py upgrade ${esc(name)} main</code></div>`
    : '<div class="rs-hint">pin 与基座 main 一致</div>';
  const impact=r.impact_files&&r.impact_files.length
    ? `<h4>pin 之后改到本库 import 的基座文件 (${r.impact_files.length})</h4><pre class="rs-pre">${r.impact_files.map(esc).join("\n")}</pre>`
    : "";
  const pinLog=r.pin_log&&r.pin_log.length
    ? `<h4>pin 之后的基座提交 (最近 ${r.pin_log.length})</h4><pre class="rs-pre">${r.pin_log.map(esc).join("\n")}</pre>` : "";
  const summary=r.summary
    ? (typeof marked!=="undefined" ? marked.parse(r.summary) : `<pre class="rs-pre">${esc(r.summary)}</pre>`)
    : '<div class="artempty">没有 streams/'+esc(name)+'/RESEARCH_SUMMARY.md</div>';
  box.innerHTML=`<h3>${esc(name)} <span class="rs-sub">${esc(r.path)}</span></h3>
    <div class="badges">${researchBadges(r)}</div>
    <div class="rs-meta">base.pin <code>${esc(r.pin)}</code> · 数据命名空间 <code>${esc(r.data_namespace||"-")}</code>
      ${r.lineage_nodes.length?" · 血缘节点 "+r.lineage_nodes.map(esc).join(", "):""}</div>
    ${pinHint}${impact}
    <h4>晋升与请求 <button onclick="promoteForm('${esc(name)}')">发起晋升</button></h4>
    <div id="rs-promote-form"></div><div id="rs-requests">加载中...</div>
    <h4>最近提交</h4><pre class="rs-pre">${r.commits.map(esc).join("\n")}</pre>
    ${pinLog}
    <h4>RESEARCH_SUMMARY</h4><div class="rs-summary md">${summary}</div>`;
  loadResearchRequests(name);
}

// ===== 晋升 (2026-10-27, H-106): 研究库发起 -> 基座跨线请求台账 (kind=promote) -> 基座开发页签的晋升队列 =====
function reqRow(q, withLand){
  const promo=q.kind==="promote";
  const land=withLand&&q.land ? `<pre class="rs-pre">${q.land.map(esc).join("\n")}</pre>` : "";
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
      <button onclick="openPromo()">查看</button>`+(d.error?` <span title="${esc(d.error)}">台账读不了</span>`:"");
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
