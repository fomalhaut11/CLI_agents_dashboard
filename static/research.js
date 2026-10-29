// ===== 研究页签 (2026-10-26): 读研究库登记册, 卡片 + 详情 =====
// 研究库是独立 git 库, 钉基座某提交 (base.pin). 卡片的脏 / 提交只算本库; "pin 落后 main N" 是提示不是故障;
// 影响提示 o = pin 之后基座改动没碰到本库 import 的模块, ! = 碰到了 (升级 pin 后需复核结果).
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
  const pinHint='<h4>升级 pin</h4><div id="rs-pin">加载中...</div>';
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
  loadPinBox(name);
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
    '<div class="rs-hint">登记册 config/research_repos.yaml 的 base_pin 在基座 main 上, 页面不改; 升级 / 回退后卡片会显示 "登记册不一致", 去 main 的 worktree 改。</div>';
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
    <td>${p.stale?"过旧":""}${p.registered_pin&&p.pin&&p.registered_pin!==p.pin?" 登记册不一致":""}${p.status!=="active"?" "+esc(p.status):""}</td></tr>`).join("");
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
  const rows=d.repos.map(r=>`<tr class="${r.hits.length?"rq-overdue":""}"><td>${esc(r.name)}</td>
    <td>${r.hits.length?"! "+r.hits.length:"o"}</td><td>${r.hits.length?`<pre class="rs-pre">${r.hits.map(esc).join("\n")}</pre>`:"没碰到本库 import 的模块"}</td></tr>`).join("");
  box.innerHTML=`<div class="rs-hint">${esc(d.label)}: 改了 ${d.changed.length} 个文件 (.py ${d.n_py}); 受影响研究库 ${d.affected.length?d.affected.map(esc).join(", "):"无"}。
    判据: 被改的 .py 是研究库 import 的基座模块 (或其包下文件); 只是近似, 不看改的是不是接口。</div>
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
