// ===== 知识页签 (2026-10-26, 知识库改造第 6 步): 基座台账的只读视图 =====
// 子视图: 结论 / 复现漏斗 / 因子知识 / 教训 / 待处理, 外加全局搜索. 数据层 knowledge.py, 接口 /api/knowledge/<视图>.
// 写入 (accept 结论 / 改教训状态) 不在这里做: 详情里给出基座工具的命令.
let knView="conclusions", knTable=null, knSort={key:null,desc:false}, knFunnelSel=null;

function knVerdict(v){ return v?`<span class="v-${esc(String(v))}">${esc(String(v))}</span>`:""; }
function knNum(x,d){ return (x===null||x===undefined||x==="")?"":(typeof x==="number"?x.toFixed(d===undefined?4:d):esc(String(x))); }
function knCell(v){ return Array.isArray(v)?esc(v.join(", ")):(v===null||v===undefined?"":esc(String(v))); }

// 通用表: cols = [{key,label,fmt?}], rows, onRow(row) -> 详情
function knRenderTable(cols, rows, onRow, rowClass){
  knTable={cols,rows,onRow,rowClass};
  knDrawTable();
}
function knDrawTable(){
  if(!knTable) return;
  const {cols,onRow,rowClass}=knTable;
  const f=(document.getElementById("kn-filter").value||"").toLowerCase();
  let rows=knTable.rows.filter(r=>!f||cols.some(c=>String(r[c.key]??"").toLowerCase().includes(f)));
  if(knSort.key){
    const k=knSort.key, s=knSort.desc?-1:1;
    rows=rows.slice().sort((a,b)=>{const x=a[k],y=b[k];
      if(x===y) return 0; if(x===null||x===undefined) return 1; if(y===null||y===undefined) return -1;
      return (typeof x==="number"&&typeof y==="number"?x-y:String(x).localeCompare(String(y)))*s;});
  }
  const box=document.getElementById("kn-table");
  if(!box) return;
  box.innerHTML=`<div class="kn-note">${rows.length} / ${knTable.rows.length} 行 · 点表头排序, 点行看详情</div>
    <table><tr>${cols.map(c=>`<th data-k="${c.key}">${esc(c.label)}${knSort.key===c.key?(knSort.desc?" v":" ^"):""}</th>`).join("")}</tr>
    ${rows.map((r,i)=>`<tr class="kn-row ${rowClass?rowClass(r):""}" data-i="${i}">${cols.map(c=>`<td>${c.fmt?c.fmt(r[c.key],r):knCell(r[c.key])}</td>`).join("")}</tr>`).join("")}</table>`;
  box.querySelectorAll("th").forEach(th=>th.onclick=()=>{const k=th.dataset.k;
    knSort={key:k,desc:knSort.key===k?!knSort.desc:false}; knDrawTable();});
  if(onRow) box.querySelectorAll("tr.kn-row").forEach(tr=>tr.onclick=()=>onRow(rows[+tr.dataset.i]));
}

function knDetail(html){ document.getElementById("kn-detail").innerHTML=html; }
function knMain(html){ document.getElementById("kn-main").innerHTML=html; }

async function loadKnowledge(view){
  if(view) knView=view;
  document.querySelectorAll(".kn-sub").forEach(b=>b.classList.toggle("active",b.dataset.kn===knView));
  knSort={key:null,desc:false}; knTable=null; knDetail(""); knMain("加载中...");
  try{
    if(knView==="conclusions") await knConclusions();
    else if(knView==="funnel") await knFunnel();
    else if(knView==="factors") await knFactors();
    else if(knView==="lessons") await knLessons();
    else if(knView==="pending") await knPending();
    document.getElementById("meta").textContent="知识 · "+new Date().toLocaleTimeString();
  }catch(e){ knMain('<div class="artempty">加载失败: '+esc(String(e))+'</div>'); }
}

// ---------------- 结论
async function knConclusions(){
  const hist=document.getElementById("kn-hist")?.checked;
  const d=await jget("/api/knowledge/conclusions"+(hist?"?history=1":""));
  if(d.configured===false){ knMain('<div class="artempty">未配置知识仓库 (config: KNOWLEDGE_REPO / knowledgeRepo)</div>'); return; }
  knMain(`<div class="kn-note">结论台账 core/research/finding_ledger.jsonl: 现行 ${d.current} / 全部 ${d.total}.
    否决 (rejected) 与成立同样标色; provisional = 未复核. <label><input type="checkbox" id="kn-hist" ${hist?"checked":""}
    onchange="loadKnowledge()"> 含已取代 / 撤回</label></div><div id="kn-table"></div>`);
  knRenderTable([
    {key:"id",label:"ID"},{key:"stream",label:"研究线"},{key:"verdict",label:"结论",fmt:knVerdict},
    {key:"status",label:"状态",fmt:knVerdict},{key:"evidence_level",label:"证据等级"},
    {key:"title",label:"标题"},{key:"created",label:"日期",fmt:v=>esc(String(v||"").slice(0,10))},
    {key:"reopen_if",label:"重开条件",fmt:v=>v?"有":""},
  ], d.rows, r=>knConclusionDetail(r.id));
}

async function knConclusionDetail(id){
  const d=await jget("/api/knowledge/conclusion?id="+encodeURIComponent(id));
  if(d.error){ knDetail(esc(d.error)); return; }
  const r=d.record;
  const ev=(r.evidence||[]).map(e=>"- "+[e.kind,e.ref||e.name,e.value!==undefined?"= "+e.value:""].filter(Boolean).join(" ")).join("\n");
  const chain=d.chain.length>1?`<div class="kn-sec">取代链 (新 -> 旧)</div><pre>${d.chain.map(c=>esc(`${c.id} [${c.verdict}/${c.status}] ${c.title}`)).join("\n")}</pre>`:"";
  knDetail(`<h3>${esc(r.id)} ${knVerdict(r.verdict)} ${knVerdict(r.status)}</h3>
    <div><b>${esc(r.title||"")}</b></div>
    <div class="kn-sec">问题</div><div>${esc(r.question||"")}</div>
    ${r.hypothesis?`<div class="kn-sec">假设</div><div>${esc(String(r.hypothesis))}</div>`:""}
    <div class="kn-sec">条件</div><pre>${esc(JSON.stringify(r.conditions||{},null,1))}</pre>
    <div class="kn-sec">证据</div><pre>${esc(ev)||"(无)"}</pre>
    ${r.significance?`<div class="kn-sec">显著性</div><pre>${esc(JSON.stringify(r.significance,null,1))}</pre>`:""}
    ${r.reopen_if?`<div class="kn-sec">重开条件</div><pre>${esc(JSON.stringify(r.reopen_if,null,1))}</pre>`:""}
    ${chain}
    ${r.status==="provisional"?`<div class="rs-hint">复核后改状态 (基座 main 上): <code>python tools/research_ledger.py set-status ${esc(r.id)} confirmed --reason "..."</code></div>`:""}
    <div class="kn-sec">原始记录</div><pre>${esc(JSON.stringify(r,null,1))}</pre>`);
}

// ---------------- 复现漏斗
async function knFunnel(){
  const d=await jget("/api/knowledge/funnel");
  const st=d.by_status, order=["candidate","registered","infeasible","parked","rejected"];
  const stage=(key,label,n)=>`<div class="kn-stage${knFunnelSel===key?" active":""}" onclick="knFunnelPick('${key}')"><b>${n}</b>${esc(label)}</div>`;
  const stages=[stage("","研报库"+(d.library_found?"":" (目录未找到)"),d.library),
    stage("*","已尝试 (条)",d.attempted)]
    .concat(order.filter(k=>st[k]).map(k=>stage(k,k,st[k])))
    .concat(Object.keys(st).filter(k=>!order.includes(k)).map(k=>stage(k,k,st[k])));
  knMain(`<div class="kn-note">研报库 ${d.library} 份 -> 尝试 ${d.attempted} 条 (涉及 ${d.reports_attempted} 份研报), 已关联 RPT- 编号 ${d.linked_rows} 条.
    点阶段下钻.</div><div class="kn-funnel">${stages.join("")}</div><div id="kn-table"></div>`);
  window._knFunnel=d;
  knFunnelPick(knFunnelSel===null?"*":knFunnelSel, true);
}
function knFunnelPick(key, keep){
  const d=window._knFunnel; if(!d) return;
  knFunnelSel=key;
  document.querySelectorAll(".kn-stage").forEach(e=>e.classList.toggle("active",e.getAttribute("onclick").includes("'"+key+"'")));
  if(key===""){ document.getElementById("kn-table").innerHTML='<div class="kn-note">研报库全文目录用全局搜索查 (按标题 / RPT- 编号).</div>'; knTable=null; return; }
  const rows=key==="*"?d.rows:d.rows.filter(r=>r.status===key);
  knRenderTable([
    {key:"id",label:"ID"},{key:"batch",label:"批"},{key:"factor",label:"因子"},{key:"status",label:"状态"},
    {key:"rating",label:"评级"},{key:"report",label:"研报"},{key:"report_id",label:"RPT"},
    {key:"rejected_reason",label:"原因",fmt:v=>esc(String(v||"").slice(0,60))},
  ], rows, r=>knDetail(`<h3>${esc(r.id)} ${esc(r.factor||"")}</h3>
    <div class="kn-sec">研报</div><div>${esc(r.report||"")} ${esc(r.report_date||"")}</div>
    <div>${r.report_id?`<code>${esc(r.report_id)}</code> ${esc(r.report_title||"")}`:'<span class="kn-note">未关联 RPT- 编号 (tools/replication/link_reports.py)</span>'}</div>
    <div class="kn-note">${esc(r.report_path||"")}</div>
    <pre>${esc(JSON.stringify(r,null,1))}</pre>`));
}

// ---------------- 因子知识
async function knFactors(){
  const d=await jget("/api/knowledge/factors");
  if(!d.found){ knMain('<div class="artempty">没找到因子注册表 core/factors/meta/factor_registry.json</div>'); return; }
  const tested=d.rows.filter(r=>r.tested).length;
  knMain(`<div class="kn-note">因子注册表 ${d.rows.length} 个, 有单因子测试指标 ${tested} 个. 评级取复现台账 (无则 linear_rating).</div><div id="kn-table"></div>`);
  const n4=v=>knNum(v,4), n2=v=>knNum(v,2);
  knRenderTable([
    {key:"name",label:"因子"},{key:"category",label:"类别"},{key:"rating",label:"评级"},
    {key:"rank_ic_mean",label:"RankIC",fmt:n4},{key:"rank_icir",label:"RankICIR",fmt:n2},
    {key:"ic_mean",label:"IC",fmt:n4},{key:"icir",label:"ICIR",fmt:n2},
    {key:"long_short_annual",label:"多空年化",fmt:n2},{key:"long_short_sharpe",label:"多空夏普",fmt:n2},
    {key:"is_active",label:"启用",fmt:v=>v===false?"否":""},
  ], d.rows, r=>knFactorDetail(r));
}
async function knFactorDetail(r){
  knDetail(`<h3>${esc(r.name)}</h3><div class="kn-note">${esc(r.source_paper||"")}</div>加载归档...`);
  const a=await jget("/api/knowledge/factor_archive?name="+encodeURIComponent(r.name));
  const files=a.files||[];
  const imgs=files.filter(f=>/\.(png|jpe?g|svg)$/i.test(f)).slice(0,6);
  const link=f=>`<a href="/api/knowledge/archive_file?rel=${encodeURIComponent(f)}" target="_blank">${esc(f)}</a>`;
  knDetail(`<h3>${esc(r.name)} <span class="rs-sub">${esc(r.category||"")}</span></h3>
    <div class="kn-note">${esc(r.source_paper||"")}</div>
    <pre>${esc(JSON.stringify(r,null,1))}</pre>
    <div class="kn-sec">单因子测试归档 (${files.length})</div>
    ${files.length?files.map(f=>"<div>"+link(f)+"</div>").join(""):'<div class="kn-note">归档里没有这个因子的文件</div>'}
    ${imgs.map(f=>`<img src="/api/knowledge/archive_file?rel=${encodeURIComponent(f)}" alt="${esc(f)}">`).join("")}`);
}

// ---------------- 教训
async function knLessons(){
  const d=await jget("/api/knowledge/lessons");
  if(!d.found){ knMain(`<div class="artempty">教训库 core/research/lessons.jsonl 还没有 (落地时在 main 上
    <code>python tools/lessons.py import-incidents</code> 导入; 现在事故仍看 docs/INCIDENTS.md)</div>`); return; }
  const alarms=d.rows.filter(r=>r.alarm).length;
  knMain(`<div class="kn-note">教训 ${d.rows.length} 条; 红色 = severity high 且只是 recorded (没有守护) ${alarms} 条.
    状态: recorded -> codified (写进 skill / 契约) -> enforced (有测试或门) -> retired.</div><div id="kn-table"></div>`);
  knRenderTable([
    {key:"id",label:"ID"},{key:"date",label:"日期"},{key:"kind",label:"类"},{key:"class",label:"分级"},
    {key:"severity",label:"严重度"},{key:"state",label:"状态"},{key:"enforcement",label:"守护"},
    {key:"title",label:"标题"},{key:"scope",label:"scope",fmt:v=>knCell(v)},
  ], d.rows, r=>knLessonDetail(r.id), r=>r.alarm?"kn-alarm":"");
}
async function knLessonDetail(id){
  const r=await jget("/api/knowledge/lesson?id="+encodeURIComponent(id));
  if(r.error){ knDetail(esc(r.error)); return; }
  const body=r.body_md?(typeof marked!=="undefined"?marked.parse(r.body_md):`<pre>${esc(r.body_md)}</pre>`):"";
  knDetail(`<h3>${esc(r.id)} <span class="rs-sub">${esc(r.kind)} · ${esc(r.state)} · ${esc(String(r.severity||"-"))}</span></h3>
    <div><b>${esc(r.title||"")}</b></div>
    ${r.rule?`<div class="kn-sec">规则</div><div>${esc(r.rule)}</div>`:""}
    <div class="kn-sec">scope</div><div>${knCell(r.scope)} <span class="kn-note">(${esc(r.scope_source||"")})</span></div>
    <div class="kn-sec">守护</div><pre>${esc(JSON.stringify(r.enforcement||{},null,1))}</pre>
    <div class="rs-hint">改状态 (基座 main 上): <code>python tools/lessons.py set-state ${esc(r.id)} codified|enforced|retired --ref &lt;守护位置&gt; --reason "..."</code></div>
    <div class="md">${body}</div>`);
}

// ---------------- 待处理
async function knPending(){
  const d=await jget("/api/knowledge/pending");
  const sec=(title,rows,fmt,empty)=>`<div class="kn-sec">${esc(title)} (${rows.length})</div>`+
    (rows.length?`<table>${rows.map(r=>"<tr>"+fmt(r)+"</tr>").join("")}</table>`:`<div class="kn-note">${esc(empty||"无")}</div>`);
  knMain(
    sec("收件箱待 accept 的结论提案",d.inbox,r=>`<td>${esc(r.id)}</td><td>${esc(r.user)}</td><td>${esc(r.stream||"")}</td><td>${knVerdict(r.verdict)}</td><td>${esc(r.title||"")}</td>`)+
      (d.inbox.length?'<div class="rs-hint">入账 (基座 main 上): <code>python tools/research_ledger.py accept</code></div>':"")+
    sec("跨线请求 (未结)",d.requests,r=>`<td>${esc(r.id)}</td><td>${esc(r.from||"")}</td><td>${esc(r.kind||"")}</td><td>${esc(r.status||"")}${r.overdue?' <span class="v-rejected">超期</span>':""}</td><td>${esc(String(r.age_days??""))}d</td><td>${esc(r.title||"")}</td>`,
      d.requests_error||"无")+
    sec("未复核 (provisional) 的现行结论",d.provisional,r=>`<td>${esc(r.id)}</td><td>${esc(r.stream||"")}</td><td>${knVerdict(r.verdict)}</td><td>${esc(r.title||"")}</td>`)+
    sec("带重开条件的结论 (人工判断是否已触发)",d.reopen_if,r=>`<td>${esc(r.id)}</td><td>${esc(r.title||"")}</td><td>${esc(JSON.stringify(r.reopen_if))}</td>`)+
    sec("没有守护的高危教训",d.unguarded_high,r=>`<td>${esc(r.id)}</td><td>${esc(r.state)}</td><td>${esc(r.title||"")}</td>`,"无 (或教训库尚未导入)"));
}

// ---------------- 全局搜索
async function knSearch(q){
  q=(q||"").trim(); if(!q) return;
  document.querySelectorAll(".kn-sub").forEach(b=>b.classList.remove("active"));
  knTable=null; knDetail(""); knMain("搜索中...");
  const d=await jget("/api/knowledge/search?q="+encodeURIComponent(q));
  const blocks=[["conclusions","结论",r=>`${r.id} [${r.verdict}/${r.status}] ${r.title}`],
    ["replications","复现",r=>`${r.id} ${r.factor} [${r.status}/${r.rating||"-"}] ${r.report||""}`],
    ["factors","因子",r=>`${r.name} (${r.category||"-"}) RankICIR ${r.rank_icir??"-"} 评级 ${r.rating||"-"}`],
    ["lessons","教训",r=>`${r.id} [${r.state}/${r.severity||"-"}] ${r.title}`],
    ["reports","研报库",r=>`${r.id} ${r.date||""} ${r.broker||""} ${r.title||""}  (${r.path})`]];
  const n=blocks.reduce((s,b)=>s+(d[b[0]]||[]).length,0);
  knMain(`<div class="kn-note">"${esc(q)}" 共 ${n} 条 (每类最多 30)</div>`+blocks.map(([k,label,f])=>{
    const rows=d[k]||[];
    return `<div class="kn-sec">${label} (${rows.length})</div>`+(rows.length?`<pre>${rows.map(r=>esc(f(r))).join("\n")}</pre>`:"");
  }).join(""));
}

document.querySelectorAll(".kn-sub").forEach(b=>b.onclick=()=>loadKnowledge(b.dataset.kn));
document.getElementById("kn-filter").oninput=()=>knDrawTable();
document.getElementById("kn-search").onkeydown=e=>{ if(e.key==="Enter") knSearch(e.target.value); };
