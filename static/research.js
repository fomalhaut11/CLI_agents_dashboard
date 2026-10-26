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
    <h4>最近提交</h4><pre class="rs-pre">${r.commits.map(esc).join("\n")}</pre>
    ${pinLog}
    <h4>RESEARCH_SUMMARY</h4><div class="rs-summary md">${summary}</div>`;
}

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
