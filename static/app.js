// ===== 通用 =====
function toast(t){ const e=document.getElementById("toast"); e.textContent=t; e.style.display="block"; }
async function jget(u){ return (await fetch(u)).json(); }
async function jpost(u,b){ return (await fetch(u,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(b)})).json(); }
function esc(s){ return (s||"").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;"); }

// ===== 标签页 =====
document.querySelectorAll(".tab").forEach(t=>t.onclick=()=>{
  document.querySelectorAll(".tab").forEach(x=>x.classList.remove("active"));
  document.querySelectorAll(".panel").forEach(x=>x.classList.remove("active"));
  t.classList.add("active");
  document.getElementById("tab-"+t.dataset.tab).classList.add("active");
  if(t.dataset.tab==="registry") loadRegistry();
  if(t.dataset.tab==="procs") loadProcs();
  if(t.dataset.tab==="data") loadData();
  if(t.dataset.tab==="artifacts") loadArtifacts();
  if(t.dataset.tab==="agents") loadAgents();
  if(t.dataset.tab==="research" && typeof loadResearch==="function") loadResearch();   // static/research.js
  if(t.dataset.tab==="knowledge" && typeof loadKnowledge==="function") loadKnowledge();   // static/knowledge.js
  if(t.dataset.tab==="streams"){ fitAndResize(TERMS.get(activeTid),false); refreshTermStatus(); }
});

// ===== 多终端 (持久 PTY, 每个标签一个独立 xterm + ws) =====
// TERMS: id -> {id,cwd,label,term,fit,ws,el,alive,lastCols,lastRows,userInputUntil}
const TERMS=new Map();
let activeTid=null, activePath=null;
const LS_KEY="sd_terms_v1";
let termTabsHtml="";
let resizeRaf=0;

function lsSave(){ try{ localStorage.setItem(LS_KEY,
  JSON.stringify([...TERMS.values()].map(t=>({id:t.id,cwd:t.cwd,label:t.label})))); }catch(e){} }
function uuid(){ try{ return crypto.randomUUID(); }catch(e){}
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g,c=>{const r=Math.random()*16|0;return (c==="x"?r:(r&3|8)).toString(16);}); }
function shortLabel(cwd,label){ if(label) return label;
  const b=(cwd||"").replace(/[\\/]+$/,"").split(/[\\/]/).pop(); return b||"term"; }

function termStateInfo(t){
  if(!t.alive) return {cls:"dead", txt:"已退出"};
  const ag=(t.agent&&t.agent!=="none")?t.agent:"";
  if(t.state==="working") return {cls:"working", txt:(ag||"shell")+" 跑"};
  if(t.state==="waiting") return {cls:"waiting", txt:(ag||"?")+" 等你"};
  return {cls:"idle", txt:ag||"shell"};
}
function isCodexTerm(t){
  return (t.agent==="codex") || String(t.label||"").toLowerCase().includes("codex");
}
function renderTabs(){
  const el=document.getElementById("termtabs");
  const html=!TERMS.size ? '<span class="notabs">无终端 — 点某 stream 的「终端」, 或右侧「+新」起一个</span>' : [...TERMS.values()].map(t=>{
    const s=termStateInfo(t);
    return `<span class="termtab ts-${s.cls} ${t.id===activeTid?"active":""}" onclick="activateTerm('${t.id}')" title="${esc(t.cwd)}&#10;状态: ${esc(s.txt)}">
       <span class="tt-dot"></span>${esc(t.label)}<span class="tt-st">${esc(s.txt)}</span>
       <span class="tt-x" onclick="event.stopPropagation();closeTerm('${t.id}')" title="关闭终端">×</span>
     </span>`;}).join("");
  if(html!==termTabsHtml){ el.innerHTML=html; termTabsHtml=html; }
  updateTermSum();
}
function updateTermSum(){
  const el=document.getElementById("termsum"); if(!el) return;
  let w=0,wa=0,id=0,de=0;
  for(const t of TERMS.values()){ if(!t.alive){de++;continue;}
    if(t.state==="working")w++; else if(t.state==="waiting")wa++; else id++; }
  const parts=[];
  if(w) parts.push("▶ "+w+" 跑着");
  if(wa) parts.push("⏳ "+wa+" 等你");
  if(id) parts.push("○ "+id+" 空闲");
  if(de) parts.push("✕ "+de+" 退出");
  el.textContent=parts.join("  ·  ");
  el.className="termsum"+(wa?" has-wait":"");
}
// 轮询各终端忙/等状态 (不重建 xterm, 只更新标签状态灯)
async function refreshTermStatus(){
  if(!TERMS.size){ updateTermSum(); return; }
  let d; try{ d=await jget("/api/terminals?status=1"); }catch(e){ return; }
  const live=new Map((d.terminals||[]).map(x=>[x.id,x]));
  const now=Date.now();
  for(const t of TERMS.values()){
    const lv=live.get(t.id);
    if(lv){
      if(!(isCodexTerm(t) && t.id===activeTid && t.userInputUntil && now<t.userInputUntil)){
        t.state=lv.state; t.agent=lv.agent; t.cpu=lv.cpu;
      }
    }
    else if(!t.ws || t.ws.readyState>1){ t.alive=false; }  // 服务端已无此终端且我方未连 => 判退出
  }
  renderTabs();
}

function fitAndResize(t,focus){
  if(!t) return;
  try{ t.fit.fit(); }catch(e){}
  if(focus){ try{ t.term.focus(); }catch(e){} }
  sendResize(t);
}

function activateTerm(id){
  const t=TERMS.get(id); if(!t) return;
  activeTid=id; activePath=t.cwd;
  for(const x of TERMS.values()) x.el.classList.toggle("active",x===t);
  document.querySelectorAll(".card").forEach(c=>c.classList.toggle("active",c.dataset.path===t.cwd));
  renderTabs();
  setTimeout(()=>fitAndResize(t,true),0);
}

function makeTerm(id,cwd,label){
  const el=document.createElement("div"); el.className="termpane";  // 可见性由 .active 类控制, 不用 display:none (否则 xterm 量不到尺寸, 输入框/光标错位)
  document.getElementById("termpanes").appendChild(el);
  const fitA=new FitAddon.FitAddon();
  const term=new Terminal({fontFamily:"Consolas, monospace",fontSize:13,theme:{background:"#000"},cursorBlink:true,scrollback:20000,
    windowsPty:{backend:"conpty",buildNumber:26100}});  // 告诉 xterm 对面是新版 ConPTY, 正确维护 reflow/scrollback
  term.loadAddon(fitA); term.open(el);
  const t={id,cwd,label:shortLabel(cwd,label),term,fit:fitA,ws:null,el,alive:true,lastCols:0,lastRows:0,userInputUntil:0};
  term.onData(d=>{ if(isCodexTerm(t)) t.userInputUntil=Date.now()+5000; if(t.ws&&t.ws.readyState===1) t.ws.send(JSON.stringify({i:d})); });
  TERMS.set(id,t);
  return t;
}

function openWs(t,isNew,cmd){
  const proto=location.protocol==="https:"?"wss":"ws";
  let url=`${proto}://${location.host}/pty?term=${t.id}&cwd=${encodeURIComponent(t.cwd)}&label=${encodeURIComponent(t.label)}`;
  if(isNew) url+="&new=1";
  const ws=new WebSocket(url); t.ws=ws;
  ws.onopen=()=>{ t.alive=true; renderTabs(); if(t.id===activeTid) fitAndResize(t,false); else sendResize(t);
    if(cmd) setTimeout(()=>{ if(ws.readyState===1) ws.send(JSON.stringify({i:cmd+"\r"})); },700); };
  ws.onmessage=e=>t.term.write(e.data);
  ws.onclose=()=>{ t.ws=null; t.alive=false; renderTabs();
    t.term.write("\r\n\x1b[31m[终端已断开 — 进程已退出或服务重启; 点标签 × 关闭]\x1b[0m\r\n"); };
  ws.onerror=()=>{};
}

function sendResize(t){ t=t||TERMS.get(activeTid);
  if(!t||!t.ws||t.ws.readyState!==1) return;
  const cols=t.term.cols, rows=t.term.rows;
  if(t.lastCols===cols && t.lastRows===rows) return;
  t.lastCols=cols; t.lastRows=rows;
  t.ws.send(JSON.stringify({r:[cols,rows]}));
}

// 新建一个终端标签 (fresh shell, 可选自动跑 cmd)
function newTerm(cwd,label,cmd){
  if(!cwd){ toast("先点某 stream 的「终端」选个 worktree"); return null; }
  const id=uuid(); const t=makeTerm(id,cwd,label);
  openWs(t,true,cmd||null); lsSave(); activateTerm(id);
  return t;
}
// worktree「终端」按钮: 已有该目录活终端就切过去, 否则开新的
function openTerminal(cwd){
  for(const t of TERMS.values()) if(t.alive&&t.cwd===cwd){ activateTerm(t.id); return; }
  newTerm(cwd,null,null);
}
// Claude/Codex「+新/继续/选择」: 一律开新标签跑命令 (绝不打进正在运行的 REPL)
function agentTerm(label,cmd){
  if(!activePath){ toast("先点某 stream 的「终端」选个 worktree"); return; }
  newTerm(activePath,label,cmd);
}
async function closeTerm(id){
  const t=TERMS.get(id); if(!t) return;
  try{ await jpost("/api/terminal/close",{id}); }catch(e){}
  try{ t.ws&&t.ws.close(); }catch(e){}
  try{ t.term.dispose(); }catch(e){}
  t.el.remove(); TERMS.delete(id); lsSave();
  if(activeTid===id){ activeTid=null; const nxt=[...TERMS.keys()][0]; if(nxt) activateTerm(nxt); else renderTabs(); }
  else renderTabs();
}
// 快捷命令(git status 等): 写进当前活动终端
function sendCmd(cmd){ const t=TERMS.get(activeTid);
  if(!t||!t.ws||t.ws.readyState!==1){ toast("先打开/选中一个终端"); return; }
  t.ws.send(JSON.stringify({i:cmd+"\r"})); t.term.focus(); }

function jumpTermToInput(){
  const t=TERMS.get(activeTid);
  if(!t){ toast("先打开/选中一个终端"); return; }
  if(isCodexTerm(t)) t.userInputUntil=Date.now()+5000;
  try{ t.term.scrollToBottom(); }catch(e){}
  try{
    const vp=t.el&&t.el.querySelector(".xterm-viewport");
    if(vp) vp.scrollTop=vp.scrollHeight;
  }catch(e){}
  try{ t.term.focus(); }catch(e){}
}

function installTermControls(){
  const quick=document.querySelector(".termbar .quick");
  if(!quick||document.getElementById("termjump")) return;
  const transcript=document.createElement("button");
  transcript.id="termtranscript";
  transcript.className="termjump";
  transcript.type="button";
  transcript.textContent="当前记录";
  transcript.title="打开当前 Claude/Codex 会话的稳定聊天记录";
  transcript.onclick=openCurrentTranscript;
  const btn=document.createElement("button");
  btn.id="termjump";
  btn.className="termjump";
  btn.type="button";
  btn.textContent="到底/输入";
  btn.title="把当前终端滚动到底部并聚焦输入; Codex 回复很长时可快速回到输入区";
  btn.onclick=jumpTermToInput;
  quick.prepend(transcript);
  quick.prepend(btn);
  document.addEventListener("keydown",e=>{
    if(!document.getElementById("tab-streams").classList.contains("active")) return;
    if(e.ctrlKey&&e.key==="End"){ e.preventDefault(); jumpTermToInput(); }
  });
}

// 刷新后重连: 读 localStorage 标签 + 服务端存活终端, 把还活着的接回来
async function restoreTerms(){
  let saved=[]; try{ saved=JSON.parse(localStorage.getItem(LS_KEY)||"[]"); }catch(e){}
  let live={terminals:[]}; try{ live=await jget("/api/terminals"); }catch(e){}
  const liveMap=new Map((live.terminals||[]).map(x=>[x.id,x]));
  for(const s of saved){ const lv=liveMap.get(s.id);
    if(lv){ const t=makeTerm(lv.id,lv.cwd||s.cwd,lv.label||s.label); openWs(t,false,null); liveMap.delete(lv.id); } }
  for(const lv of liveMap.values()){ const t=makeTerm(lv.id,lv.cwd,lv.label); openWs(t,false,null); } // 别处建的也收编
  lsSave();
  const first=[...TERMS.keys()][0]; if(first) activateTerm(first); else renderTabs();
}

window.addEventListener("resize",()=>{
  if(resizeRaf) cancelAnimationFrame(resizeRaf);
  resizeRaf=requestAnimationFrame(()=>{ resizeRaf=0; fitAndResize(TERMS.get(activeTid),false); });
});

// ===== Streams =====
async function addWorktree(){
  const branch=prompt("新分支名 (建议 stream/xxx):","stream/"); if(!branch)return;
  const name=prompt("worktree 目录名:","wt-"+branch.replace(/^stream\//,"").replace(/[\\/]/g,"-")); if(!name)return;
  const j=await jpost("/api/worktree",{name,branch});
  toast((j.rc===0?"[OK] 已创建 "+name:"[rc="+j.rc+"]")+"\n\n"+(j.out||"")); refreshStreams();
}
async function gitOp(path,action){ const j=await jpost("/api/git",{path,action});
  toast((j.rc===0?"[OK] ":"[rc="+j.rc+"] ")+action+"\n\n"+(j.out||"(无输出)")); refreshStreams(); }
async function syncMain(path,branch,behind,dirty){
  if(dirty){ toast("工作区有 "+dirty+" 个未提交改动, 先在终端 commit 或 stash, 再同步 main"); return; }
  if(!confirm("把 main 最新的 "+behind+" 个提交合并进 "+branch+" ?\n\n无冲突: 直接完成.\n有冲突: 自动回退, 不会留下半合并状态.")) return;
  toast("正在合并 main -> "+branch+" ...");
  const j=await jpost("/api/sync_main",{path});
  toast((j.rc===0?"[OK] ":"[rc="+j.rc+"] ")+"同步 main\n\n"+(j.out||"(无输出)")); refreshStreams(); }
async function setStatus(branch,status){ await jpost("/api/stream_meta",{branch,status}); refreshStreams(); }

function guardHtml(g){
  if(!g.length) return '<div class="guard"><span class="g-ok">约定: 无越界</span></div>';
  const lines=g.map(x=>`<div class="g-${x.level}">${x.level==="violation"?"✗":"!"} ${esc(x.file)} <span style="color:#6e7681">— ${esc(x.msg)}</span></div>`).join("");
  return `<div class="guard has">约定: ${g.length} 处需注意<br>${lines}</div>`;
}
function statusSel(s){
  const opts=["ACTIVE","PARKED","BLOCKED"].map(o=>`<option ${s.meta.status===o?"selected":""}>${o}</option>`).join("");
  return `<select onchange="setStatus('${s.branch}',this.value)" ${s.branch==="main"?"disabled":""}>${opts}</select>`;
}
function card(s){
  const dirty=s.staged+s.unstaged+s.untracked;
  const clean=dirty===0?'<span class="badge clean">clean</span>':`<span class="badge dirty">脏 ${dirty}</span>`;
  const ahead=s.ahead?`<span class="badge ahead">领先 ${s.ahead}</span>`:"";
  const behind=s.behind?`<span class="badge behind">落后 ${s.behind}</span>`:"";
  const stb=s.branch==="main"?"":`<span class="badge st-${s.meta.status}">${s.meta.status}</span>`;
  const p=s.path.replace(/\\/g,"\\\\");
  return `<div class="card" data-path="${s.path}">
    <div class="row1"><span class="branch">${s.branch}</span><span class="name">${s.name}</span>${statusSel(s)}</div>
    <div class="badges">${clean}${ahead}${behind}${stb}</div>
    ${s.branch==="main"?"":guardHtml(s.guard)}
    <div class="commits">${s.commits.map(c=>"· "+esc(c)).join("<br>")}</div>
    <div class="actions">
      <button class="primary" onclick="openTerminal('${p}')">终端</button>
      <button onclick="openSessions('${p}','${esc(s.branch)}')">会话</button>
      <button onclick="openDiff('${p}','${esc(s.branch)}')">改动${dirty?` (${dirty})`:""}</button>
      <button onclick="gitOp('${p}','fetch')">fetch</button>
      <button onclick="gitOp('${p}','pull')">pull</button>
      ${s.branch==="main"||!s.behind?"":`<button class="primary" onclick="syncMain('${p}','${esc(s.branch)}',${s.behind},${dirty})">同步 main (${s.behind})</button>`}
      <button onclick="gitOp('${p}','log')">log</button>
    </div></div>`;
}
async function refreshStreams(){
  try{
    const d=await jget("/api/streams");
    document.getElementById("cards").innerHTML=d.streams.map(card).join("");
    const wb=document.getElementById("wipbar");
    wb.textContent=`活跃 stream (WIP): ${d.wip_active} / 建议 ≤2`;
    wb.className="wipbar"+(d.wip_active>2?" warn":"");
    document.getElementById("meta").textContent=d.streams.length+" worktree · "+new Date().toLocaleTimeString();
    if(activePath) document.querySelectorAll(".card").forEach(c=>c.classList.toggle("active",c.dataset.path===activePath));
  }catch(e){ document.getElementById("meta").textContent="刷新失败: "+e; }
}

// ===== diff 模态 =====
let diffPath=null, diffFiles=[];
async function openDiff(path,branch){
  diffPath=path;
  document.getElementById("difftitle").textContent="改动 @ "+branch;
  document.getElementById("diffmodal").style.display="flex";
  document.getElementById("diffpre").innerHTML=""; document.getElementById("commitmsg").value="";
  diffFiles=await jget("/api/changed?path="+encodeURIComponent(path));
  document.getElementById("filelist").innerHTML=diffFiles.map((f,i)=>
    `<div class="f" data-i="${i}" onclick="showFileDiff(${i})">
       <input type="checkbox" data-file="${esc(f.file)}" onclick="event.stopPropagation()">
       <span class="st">${f.x}${f.y}</span><span>${esc(f.file)}</span></div>`).join("")||"<div style='color:#6e7681;padding:8px'>无改动</div>";
}
function colorDiff(t){ return esc(t).split("\n").map(l=>{
  if(l.startsWith("+")&&!l.startsWith("+++")) return `<span class="diff-add">${l}</span>`;
  if(l.startsWith("-")&&!l.startsWith("---")) return `<span class="diff-del">${l}</span>`;
  if(l.startsWith("@@")||l.startsWith("diff")||l.startsWith("index")) return `<span class="diff-hd">${l}</span>`;
  return l; }).join("\n"); }
async function showFileDiff(i){
  document.querySelectorAll(".filelist .f").forEach(e=>e.classList.toggle("sel",+e.dataset.i===i));
  const f=diffFiles[i];
  const j=await jget(`/api/filediff?path=${encodeURIComponent(diffPath)}&file=${encodeURIComponent(f.file)}&staged=${f.staged?1:0}`);
  document.getElementById("diffpre").innerHTML=colorDiff(j.diff||"(无 diff)");
}
function checkedFiles(){ return [...document.querySelectorAll(".filelist input:checked")].map(c=>c.dataset.file); }
async function stageSelected(unstage){
  const files=checkedFiles(); if(!files.length){toast("先勾选文件");return;}
  const j=await jpost("/api/stage",{path:diffPath,files,unstage});
  toast(j.rc===0?(unstage?"已取消暂存":"已暂存")+" "+files.length+" 个":j.out); openDiff(diffPath,document.getElementById("difftitle").textContent.replace("改动 @ ",""));
}
async function commitSelected(){
  const files=checkedFiles(), msg=document.getElementById("commitmsg").value.trim();
  if(!msg){toast("填提交信息");return;}
  const j=await jpost("/api/commit",{path:diffPath,files,message:msg});
  toast((j.rc===0?"[OK] 已提交":"[rc="+j.rc+"]")+"\n\n"+(j.out||""));
  if(j.rc===0){ closeDiff(); refreshStreams(); }
}
function closeDiff(){ document.getElementById("diffmodal").style.display="none"; }

// ===== 实验注册表 =====
let regData=[];
// 归属 = "基座" + 登记册里的研究库名 (经 /api/artifacts/roots 的 streams 取, 不再写死 stream 分支名;
// 2026-10-28 stream 分支全部退役) + 已有条目里出现过的旧值 (老条目仍可筛)
let regOwnerList=null;
async function regOwners(){
  if(!regOwnerList){
    try{
      const d=await jget("/api/artifacts/roots");
      regOwnerList=["基座"].concat((d.streams||[]).map(s=>s.key).filter(k=>k!=="all"&&k!=="shared"));
    }catch(e){ regOwnerList=["基座"]; }
  }
  return regOwnerList;
}
async function loadRegistry(){
  regData=await jget("/api/registry");
  const sel=document.getElementById("regfilter"), cur=sel.value;
  const owners=[...new Set((await regOwners()).concat(regData.map(e=>e.stream).filter(Boolean)))];
  sel.innerHTML='<option value="">全部归属</option>'+owners.map(o=>`<option value="${esc(o)}">${esc(o)}</option>`).join("");
  sel.value=owners.includes(cur)?cur:"";
  renderRegistry();
}
function renderRegistry(){
  const f=document.getElementById("regfilter").value;
  const rows=regData.filter(e=>!f||e.stream===f).map(e=>`<tr>
    <td>${e.id}</td><td>${esc(e.stream)}</td><td>${esc(e.date||"")}</td>
    <td>${esc(e.hypothesis||"")}</td><td>${esc(e.kill||"")}</td>
    <td><span class="st-pill st-${(e.status||"RUNNING").replace(" ","-")}">${esc(e.status||"RUNNING")}</span></td>
    <td>${esc(e.version||"")}</td>
    <td>${["RUNNING","PASS","HONEST-FAIL"].map(s=>`<button onclick="regSet(${e.id},'${s}')">${s[0]}</button>`).join("")}
        <button onclick="regDel(${e.id})">×</button></td></tr>`).join("");
  document.getElementById("regtable").innerHTML=`<table><tr><th>id</th><th>归属</th><th>日期</th>
    <th>假设</th><th>kill 准则</th><th>结论</th><th>FS版本</th><th>操作</th></tr>${rows}</table>`;
}
async function showRegForm(){
  const el=document.getElementById("regform");
  el.style.display=el.style.display==="none"?"grid":"none";
  const owners=await regOwners();
  el.innerHTML=`
    <label>归属<select id="rf_stream">${owners.map(o=>`<option>${esc(o)}</option>`).join("")}</select></label>
    <label>日期<input id="rf_date" value="${new Date().toISOString().slice(0,10)}"></label>
    <label class="full">假设 (hypothesis)<input id="rf_hyp"></label>
    <label class="full">kill 准则<input id="rf_kill"></label>
    <label>FactorStore 版本<input id="rf_ver"></label>
    <label>链接<input id="rf_link"></label>
    <div class="full"><button class="primary" onclick="regAdd()">保存</button></div>`;
}
async function regAdd(){
  const e={stream:rf_stream.value,date:rf_date.value,hypothesis:rf_hyp.value,kill:rf_kill.value,
           version:rf_ver.value,link:rf_link.value,status:"RUNNING"};
  if(!e.hypothesis){toast("填假设");return;}
  await jpost("/api/registry",{op:"add",entry:e}); document.getElementById("regform").style.display="none"; loadRegistry();
}
async function regSet(id,status){ await jpost("/api/registry",{op:"update",id,fields:{status}}); loadRegistry(); }
async function regDel(id){ if(confirm("删除实验 "+id+"?")){ await jpost("/api/registry",{op:"delete",id}); loadRegistry(); } }

// ===== 进程 =====
let procSel=null, procTimer=null;
async function startProcAt(path,cmd,label,force){
  const j=await jpost("/api/proc/start",{path,cmd,label,force:!!force});
  if(j.dup!==undefined && !force){
    if(confirm("同命令已在 #"+j.dup+" 运行, 仍要再跑一份?")) return startProcAt(path,cmd,label,true);
    procSel=j.dup; loadProcs(); return; }
  if(j.id){procSel=j.id;} loadProcs();
}
async function startJob(cmd,label){ await startProcAt(activePath,cmd,label); }
async function startCustom(){ const cmd=document.getElementById("customcmd").value.trim(); if(!cmd)return;
  document.getElementById("customcmd").value=""; await startProcAt(activePath,cmd,cmd); }
function procSub(p){
  let s="";
  if(p.status==="running") s=`<span class="rcok">running</span>${p.dur?" · "+p.dur:""}${p.cpu!==null?` · cpu ${p.cpu}%`:""}${p.mem_mb!==null?` · ${p.mem_mb}MB`:""}`;
  else if(p.status==="lost") s=`<span class="rcunk">lost</span> · 面板停机期间结束, rc 未知`;
  else if(p.rc===null) s=`<span class="rcunk">exited rc=?</span>${p.dur?" · "+p.dur:""}`;
  else if(p.rc===0) s=`<span class="rcok">rc=0</span>${p.dur?" · "+p.dur:""}`;
  else s=`<span class="rcbad">rc=${p.rc}</span>${p.dur?" · "+p.dur:""}`;
  return s+` · ${p.started}${p.ended?" → "+p.ended:""} · ${esc(p.path)}`;
}
async function loadProcs(){
  const list=await jget("/api/proc/list");
  document.getElementById("proclist").innerHTML=list.map(p=>`
    <div class="procitem ${p.id===procSel?"sel":""} ${p.status==="exited"&&p.rc!==null&&p.rc!==0?"fail":""}" onclick="selProc(${p.id})">
      <div class="lbl"><span class="dot ${p.status}"></span>${esc(p.label)} <span style="color:#6e7681">#${p.id}${p.adopted?" (接管)":""}</span></div>
      <div class="sub">${procSub(p)}</div>
      ${p.status==="running"
        ?`<button onclick="event.stopPropagation();stopProc(${p.id})">停止</button>`
        :`<button onclick="event.stopPropagation();rerunProc(${p.id})">重跑</button>
          <a href="/api/proc/log?id=${p.id}&dl=1" onclick="event.stopPropagation()" style="font-size:11px;margin-left:6px">日志</a>`}
    </div>`).join("")||"<div style='color:#6e7681'>无进程</div>";
  if(procSel) showProcLog();
  if(procTimer) clearTimeout(procTimer);
  if(document.getElementById("tab-procs").classList.contains("active")) procTimer=setTimeout(loadProcs,2000);
}
function selProc(id){ procSel=id; loadProcs(); }
async function rerunProc(id){
  const list=await jget("/api/proc/list"); const p=list.find(x=>x.id===id); if(!p)return;
  await startProcAt(p.path,p.cmd,p.label);
}
async function showProcLog(){ if(!procSel)return; const j=await jget("/api/proc/log?id="+procSel);
  const pre=document.getElementById("proclog"); const atBottom=pre.scrollHeight-pre.scrollTop-pre.clientHeight<40;
  pre.textContent=(j.truncated?"(…前文截断, 完整日志点条目上的\"日志\"下载)\n":"")+(j.text||"(无输出)");
  if(atBottom) pre.scrollTop=pre.scrollHeight; }
async function stopProc(id){ await jpost("/api/proc/stop",{id}); loadProcs(); }
async function clearProcs(delLogs){ await jpost("/api/proc/clear",{del_logs:!!delLogs}); if(delLogs||!procSel)procSel=null; loadProcs(); }

// ===== 会话恢复 (窗口关了, 对话还在盘上; claude + codex 双源) =====
let sessPath=null;
function ensureSessView(){
  let v=document.getElementById("sessview");
  if(v) return v;
  const list=document.getElementById("sesslist");
  if(!list||!list.parentElement) return null;
  list.classList.add("sesslist");
  list.style.width="";
  list.parentElement.classList.add("sessbody");
  v=document.createElement("div");
  v.id="sessview";
  v.className="sessview";
  list.parentElement.appendChild(v);
  return v;
}
function sessSection(title,list,kind){
  if(!list||!list.length)
    return `<div class="sesssec"><div class="sesshd">${title} (0)</div><div style="color:#6e7681;padding:6px">无</div></div>`;
  return `<div class="sesssec"><div class="sesshd">${title} (${list.length})</div>`+
    list.map(s=>`<div class="procitem sessitem" data-kind="${kind}" data-id="${esc(s.id)}">
      <div class="lbl">${esc(s.name||s.preview||"(无预览)")}</div>
      <div class="sub">${s.mtime} · ${s.size_kb} KB · ${esc(s.id)}</div>
      <div style="margin-top:6px;display:flex;gap:6px">
        <button onclick="viewSession('${kind}','${s.id}')">查看</button>
        <button class="primary" onclick="resumeSession('${kind}','${s.id}')">恢复</button>
        <button onclick="copyResume('${kind}','${s.id}')">复制命令</button>
      </div></div>`).join("")+`</div>`;
}
// 从该 worktree 的 claude/codex 会话里挑最近一条 (优先 prefKind, 否则按 ts 取最新)
function pickLatestSession(ccs,cxs,prefKind){
  ccs=ccs||[]; cxs=cxs||[];
  if(prefKind==="codex" && cxs.length) return {kind:"codex",id:cxs[0].id};
  if(prefKind==="claude" && ccs.length) return {kind:"claude",id:ccs[0].id};
  const top=[];
  if(ccs.length) top.push({kind:"claude",id:ccs[0].id,ts:+ccs[0].ts||0});
  if(cxs.length) top.push({kind:"codex",id:cxs[0].id,ts:+cxs[0].ts||0});
  top.sort((a,b)=>b.ts-a.ts);
  return top[0]||null;
}
async function openSessions(path,branch){
  sessPath=path;
  document.getElementById("sesstitle").textContent="会话 @ "+branch+" — 点「恢复」在该 worktree 接回 (claude / codex)";
  document.getElementById("sessmodal").style.display="flex";
  document.getElementById("sesslist").innerHTML="加载中...";
  const sv=ensureSessView();
  if(sv) sv.innerHTML="<div class='artempty'>选择左侧会话查看稳定聊天记录。PTY 终端仍用于交互。</div>";
  document.getElementById("sessdir").textContent="";
  const [cc,cx]=await Promise.all([
    jget("/api/sessions?path="+encodeURIComponent(path)),
    jget("/api/codex_sessions?path="+encodeURIComponent(path))
  ]);
  document.getElementById("sessdir").textContent="claude: "+(cc.dir||"-")+"   |   codex: "+(cx.dir||"-");
  document.getElementById("sesslist").innerHTML=
    sessSection("Claude 会话",cc.sessions,"claude")+sessSection("Codex 会话",cx.sessions,"codex");
  const latest=pickLatestSession(cc.sessions,cx.sessions,"");  // 一键直接打开最近一条, 省去再点「查看」
  if(latest) viewSession(latest.kind,latest.id);
}
function closeSess(){ document.getElementById("sessmodal").style.display="none"; }
function safeId(id){ return /^[0-9a-fA-F-]{8,64}$/.test(id||"") ? id : ""; }
function resumeCmd(kind,id){ id=safeId(id); if(!id){ toast("会话 id 非法, 拒绝"); return ""; }
  return kind==="codex" ? "codex resume "+id : "claude -r "+id; }
function resumeSession(kind,id){
  const cmd=resumeCmd(kind,id); if(!cmd) return;
  const path=sessPath; closeSess();
  document.querySelector('.tab[data-tab="streams"]').click();
  newTerm(path,kind,cmd);
  toast("正在 "+path+" 新标签恢复 "+kind+" 会话 "+id.slice(0,8)+" ...");
}
function copyResume(kind,id){ const c=resumeCmd(kind,id); if(!c) return;
  if(navigator.clipboard) navigator.clipboard.writeText(c); toast("已复制: "+c); }

function termAgentKind(t){
  if(!t) return "";
  const label=String(t.label||"").toLowerCase();
  if(isCodexTerm(t)) return "codex";
  if(t.agent==="claude"||label.includes("claude")) return "claude";
  return "";
}

function sessMsgHtml(m){
  const role=m.role||"";
  const phase=m.phase?` · ${esc(m.phase)}`:"";
  const ts=m.ts?` · ${esc(m.ts.replace("T"," ").replace("Z",""))}`:"";
  return `<div class="sessmsg ${esc(role)}">
    <div class="sessmeta">${esc(role||"msg")}${phase}${ts}</div>
    <pre>${esc(m.text||"")}</pre>
  </div>`;
}
async function viewSession(kind,id){
  id=safeId(id); if(!id){ toast("会话 id 非法, 拒绝"); return; }
  document.querySelectorAll(".sessitem").forEach(x=>x.classList.toggle("sel",x.dataset.kind===kind&&x.dataset.id===id));
  const v=ensureSessView();
  if(!v){ toast("会话视图容器不存在"); return; }
  v.innerHTML="<div class='artempty'>加载聊天记录...</div>";
  const url=`/api/session_transcript?kind=${encodeURIComponent(kind)}&id=${encodeURIComponent(id)}&path=${encodeURIComponent(sessPath||"")}`;
  let d; try{ d=await jget(url); }catch(e){ v.innerHTML="<div class='artempty'>读取失败。若刚更新代码, 需要等当前任务结束后重启 dashboard 后端。</div>"; return; }
  if(!d.ok){ v.innerHTML=`<div class='artempty'>读取失败: ${esc(d.error||"")}</div>`; return; }
  const meta=d.meta||{};
  const note=d.truncated?` · 仅显示最后 ${d.shown}/${d.total} 条`:` · ${d.total} 条`;
  const head=`<div class="sessviewhead">
    <div><b>${esc(kind)}</b> ${esc(id)}${note}</div>
    <div class="sub">${esc(meta.cwd||"")}${meta.model?` · ${esc(meta.model)}`:""}${meta.cli_version?` · ${esc(meta.cli_version)}`:""}</div>
  </div>`;
  const body=(d.messages||[]).map(sessMsgHtml).join("")||"<div class='artempty'>无可展示消息</div>";
  v.innerHTML=head+body;
  v.scrollTop=v.scrollHeight;
}
async function openCurrentTranscript(){
  const t=TERMS.get(activeTid);
  // 不再硬依赖存活 PTY: 有活动终端就用它的 worktree, 否则回退到当前选中的 worktree 卡片 (activePath).
  // 这样服务重启过 / CLI 在外部跑 / 终端是普通 shell 时, 仍能看本 stream 的对话.
  const cwd=(t&&t.cwd)||activePath;
  if(!cwd){ toast("先点某 stream 的「终端」或卡片选个 worktree, 再看记录"); return; }
  const prefKind=t?termAgentKind(t):"";
  const label=(t&&t.label)||cwd.replace(/[\\/]+$/,"").split(/[\\/]/).pop();
  sessPath=cwd;
  document.getElementById("sesstitle").textContent="当前记录 @ "+label+" — 稳定 transcript 视图";
  document.getElementById("sessmodal").style.display="flex";
  document.getElementById("sesslist").innerHTML="加载中...";
  const sv=ensureSessView();
  if(sv) sv.innerHTML="<div class='artempty'>加载当前会话记录...</div>";
  document.getElementById("sessdir").textContent="";
  const [cc,cx]=await Promise.all([
    jget("/api/sessions?path="+encodeURIComponent(cwd)),
    jget("/api/codex_sessions?path="+encodeURIComponent(cwd))
  ]);
  document.getElementById("sessdir").textContent="claude: "+(cc.dir||"-")+"   |   codex: "+(cx.dir||"-");
  document.getElementById("sesslist").innerHTML=
    sessSection("Claude 会话",cc.sessions,"claude")+sessSection("Codex 会话",cx.sessions,"codex");
  const latest=pickLatestSession(cc.sessions,cx.sessions,prefKind);
  if(latest) viewSession(latest.kind,latest.id);
  else if(sv) sv.innerHTML="<div class='artempty'>该 worktree 下没有找到 Claude/Codex 对话记录。</div>";
}

// ===== 后台 Agent (claude agents --json) =====
async function loadAgents(){
  const w=document.getElementById("agentwrap"); w.textContent="加载中...";
  let d; try{ d=await jget("/api/agents"); }catch(e){ w.innerHTML="<div style='color:#f85149'>请求失败</div>"; return; }
  if(!d.ok){ w.innerHTML="<div style='color:#f85149'>读取失败: "+esc(d.error||"")+"</div>"; return; }
  if(!d.agents.length){
    w.innerHTML="<div style='color:#6e7681'>当前无在跑 agent。"+(d.raw?"<pre class='proclog' style='margin-top:8px'>"+esc(d.raw)+"</pre>":"")+"</div>"; return; }
  w.innerHTML=d.agents.map(a=>{
    const id=a.sessionId||a.id||"", st=a.status||"?";
    const dot=(st==="busy"||st==="running")?"running":"exited";
    const cwd=(a.cwd||"").replace(/\\/g,"\\\\");
    return `<div class="procitem">
      <div class="lbl"><span class="dot ${dot}"></span>${esc(a.kind||"agent")} · ${esc(st)} <span style="color:#6e7681">pid ${a.pid||"?"}</span></div>
      <div class="sub">${esc(a.cwd||"")}<br>${esc(id)}</div>
      ${id&&cwd?`<div style="margin-top:6px"><button onclick="attachAgent('${cwd}','${id}')">在终端接入</button></div>`:""}
    </div>`; }).join("");
}
function attachAgent(cwd,id){
  id=safeId(id); if(!id){ toast("会话 id 非法, 拒绝"); return; }
  document.querySelector('.tab[data-tab="streams"]').click();
  newTerm(cwd,"claude","claude -r "+id); toast("新标签接入会话 "+id.slice(0,8)+" @ "+cwd);
}

// ===== 产出查看 (实验输出: docx/png/pdf/html/csv/parquet...) =====
let artKey=null, artRel="", artEntries=[], artRootsLoaded=false, artRootMeta={};
let artRoots=[], artStreams=[], artStream="all";

function humanSize(n){ if(n<1024) return n+" B"; if(n<1048576) return (n/1024).toFixed(1)+" KB";
  if(n<1073741824) return (n/1048576).toFixed(1)+" MB"; return (n/1073741824).toFixed(2)+" GB"; }
function artText(rel,key){ return jget(`/api/artifacts/text?key=${encodeURIComponent(key||artKey)}&rel=${encodeURIComponent(rel)}`); }
function rawUrl(rel,dl,key){ return `/api/artifacts/raw?key=${encodeURIComponent(key||artKey)}&rel=${encodeURIComponent(rel)}`+(dl?"&download=1":""); }

async function loadArtifacts(){
  if(!artRootsLoaded){
    const d=await jget("/api/artifacts/roots");
    artRoots=d.roots||[];
    artStreams=d.streams||[];
    artRootMeta=Object.fromEntries(artRoots.map(r=>[r.key,r]));
    const ss=document.getElementById("artstream");
    ss.innerHTML=artStreams.map(s=>`<option value="${esc(s.key)}">${esc(s.label||s.key)}</option>`).join("");
    artRootsLoaded=true;
    if(!artStreams.some(s=>s.key===artStream)) artStream=(artStreams[0]&&artStreams[0].key)||"all";
    ss.value=artStream;
    artSetStream(artStream);
    return;
  }
  if(artKey) artBrowse(artKey,artRel);
}

function artRootsForStream(stream){
  const own=artRoots.filter(r=>r.stream===stream);
  if(stream==="shared") return own;
  // 共享产出 (docs/reports / docs/experiments / 训练报告) 登记在 stream="shared" 下,
  // 但任何一条线都可能要看它。以前必须先把上面的 stream 下拉切回 shared 才找得到,
  // 每次找共享报告都要先想起来切一下。这里把它们追加在本线产出之后, 分组名加
  // "共享 · " 前缀以便区分; 本线自己的根仍排在前面, 所以默认选中的还是本线的。
  const shared=artRoots.filter(r=>r.stream==="shared")
                       .map(r=>Object.assign({}, r, {group:"共享 · "+(r.group||"产出")}));
  return own.concat(shared);
}
function renderArtRootSelect(roots, preferredKey){
  const sel=document.getElementById("artroot");
  if(!roots.length){
    sel.innerHTML="<option value=''>无受管产出地址</option>";
    return null;
  }
  const groups=[];
  for(const r of roots){
    let g=groups.find(x=>x.name===(r.group||"产出"));
    if(!g){ g={name:r.group||"产出", roots:[]}; groups.push(g); }
    g.roots.push(r);
  }
  sel.innerHTML=groups.map(g=>`<optgroup label="${esc(g.name)}">`+
    g.roots.map(r=>`<option value="${r.key}" ${r.exists?"":"disabled"}>${esc(r.label)}${r.exists?"":" (缺失)"}</option>`).join("")+
    `</optgroup>`).join("");
  const usable=roots.filter(r=>r.exists);
  const selected=usable.find(r=>r.key===preferredKey)||usable[0];
  if(selected) sel.value=selected.key;
  return selected||null;
}
function artSetStream(stream){
  artStream=stream;
  const roots=artRootsForStream(stream);
  const selected=renderArtRootSelect(roots, artKey);
  artRel="";
  document.getElementById("artviewhead").textContent="选择左侧文件查看";
  document.getElementById("artview").innerHTML="";
  if(selected) artBrowse(selected.key,"");
  else{
    artKey=null; artEntries=[];
    document.getElementById("artcrumb").innerHTML="";
    document.getElementById("artlist").innerHTML="<div class='artempty'>该 stream 尚未登记可浏览的产出地址</div>";
  }
}

async function artBrowse(key,rel){
  if(!key) return;
  artKey=key; artRel=rel||"";
  const d=await jget(`/api/artifacts/list?key=${encodeURIComponent(key)}&rel=${encodeURIComponent(artRel)}`);
  artEntries=d.entries||[];
  renderCrumb(); renderArtList();
}
function artReload(){ if(artKey) artBrowse(artKey,artRel); }
function artUp(){ const p=artRel.split("/"); p.pop(); artBrowse(artKey,p.join("/")); }
function artClick(i){ const e=artEntries[i]; if(!e) return; if(e.is_dir) artBrowse(artKey,e.rel); else artOpen(e); }

function renderCrumb(){
  const root=artRootMeta[artKey]||{};
  const parts=artRel?artRel.split("/"):[];
  const meta=[root.stream,root.task].filter(Boolean).join(" / ");
  let acc="", html=`<div class="artrootinfo"><b>${esc(root.label||artKey||"")}</b>`+
    `${meta?` <span>${esc(meta)}</span>`:""}`+
    `${root.path?` <span title="${esc(root.path)}">${esc(root.path)}</span>`:""}`+
    `${root.patterns&&root.patterns.length?` <span>过滤: ${esc(root.patterns.join(", "))}</span>`:""}</div>`+
    `<span class="crumb" onclick="artBrowse('${artKey}','')">根</span>`;
  parts.forEach(p=>{ acc=acc?acc+"/"+p:p;
    html+=` / <span class="crumb" onclick="artBrowse('${artKey}','${acc.replace(/'/g,"\\'")}')">${esc(p)}</span>`; });
  document.getElementById("artcrumb").innerHTML=html;
}
function kindIcon(k){ return {dir:"[D]",image:"[IMG]",pdf:"[PDF]",docx:"[DOC]",html:"[HTML]",md:"[MD]",table:"[TBL]",text:"[TXT]",binary:"[BIN]"}[k]||"[?]"; }
function renderArtList(){
  const up=artRel?`<div class="artitem up" onclick="artUp()">.. 上一级</div>`:"";
  const items=artEntries.map((e,i)=>`<div class="artitem" onclick="artClick(${i})">
      <span class="ai-k k-${e.kind}">${kindIcon(e.kind)}</span>
      <span class="ai-n">${esc(e.name)}</span>
      <span class="ai-m">${e.is_dir?"":humanSize(e.size)+" · "+e.mtime}</span>
    </div>`).join("");
  document.getElementById("artlist").innerHTML=up+(items||"<div class='artempty'>(空目录)</div>");
}

async function artOpen(e){
  const k=e.srcKey||artKey;  // 图表画廊条目带 srcKey, 指向其来源 root
  const head=document.getElementById("artviewhead"), view=document.getElementById("artview");
  head.innerHTML=`<span class="vh-name">${esc(e.name)}</span><span class="vh-meta">${humanSize(e.size)} · ${e.mtime}</span><a class="vh-dl" href="${rawUrl(e.rel,true,k)}">下载</a>`;
  view.innerHTML="<div class='artempty'>加载中...</div>";
  try{
    if(e.kind==="image"){ view.innerHTML=`<div class="imgwrap"><img src="${rawUrl(e.rel,false,k)}"></div>`; }
    else if(e.kind==="pdf"||e.kind==="html"){ view.innerHTML=`<iframe class="artframe" src="${rawUrl(e.rel,false,k)}"></iframe>`; }
    else if(e.kind==="md"){ const j=await artText(e.rel,k);
      view.innerHTML=`<div class="mdbody">${window.marked?marked.parse(j.text||""):esc(j.text||"")}</div>`; }
    else if(e.kind==="text"){ const j=await artText(e.rel,k);
      view.innerHTML=`<pre class="txtbody">${esc(j.text||"")}</pre>`+(j.truncated?"<div class='artempty'>(已截断, 文件过大)</div>":""); }
    else if(e.kind==="table"){ await artTable(e); }
    else if(e.kind==="docx"){ await artDocx(e); }
    else { view.innerHTML=`<div class='artempty'>不支持内联预览此格式 — <a href="${rawUrl(e.rel,true)}">下载</a></div>`; }
  }catch(err){ view.innerHTML=`<div class='artempty'>预览失败: ${esc(String(err))}</div>`; }
}
async function artTable(e){
  const view=document.getElementById("artview");
  const j=await jget(`/api/artifacts/table?key=${encodeURIComponent(e.srcKey||artKey)}&rel=${encodeURIComponent(e.rel)}`);
  if(j.error){ view.innerHTML=`<div class='artempty'>${esc(j.error)}</div>`; return; }
  const th="<tr>"+j.columns.map(c=>`<th>${esc(c)}</th>`).join("")+"</tr>";
  const tr=j.rows.map(r=>"<tr>"+r.map(v=>`<td>${esc(v)}</td>`).join("")+"</tr>").join("");
  const note=`显示 ${j.shown} 行`+(j.total!=null?` / 共 ${j.total} 行`:"")+(j.truncated?" (已截断)":"");
  view.innerHTML=`<div class="tblnote">${note}</div><div class="tblwrap"><table class="dtable">${th}${tr}</table></div>`;
}
async function artDocx(e){
  const view=document.getElementById("artview");
  const k=e.srcKey||artKey;
  if(!window.docx){ view.innerHTML=`<div class='artempty'>docx 预览库未加载 — <a href="${rawUrl(e.rel,true,k)}">下载</a></div>`; return; }
  const resp=await fetch(rawUrl(e.rel,false,k)); const blob=await resp.blob();
  view.innerHTML="<div class='docxbody' id='docxbody'></div>";
  await docx.renderAsync(blob, document.getElementById("docxbody"));
}

// ===== 数据 =====
async function startManagedProc(cmd,label){
  await startProcAt(activePath,cmd,label);
  if(procSel){ document.querySelector('.tab[data-tab="procs"]').click(); loadProcs(); }
}
function startAllDataHealthCheck(){
  startManagedProc("python tools/data_management/scheduled_data_updater.py --data-type all --health-check","data 全量健康检查");
}
function startLegacyWave0Pull(){
  // 2026-09-17: 旧入口降级. scheduled_data_updater 现在只是 wave0 的 SQL 拉取件 (被编排器调用), 不判跑不跑、不走门控、不动 wave1-5.
  const cmd="python tools/data_management/scheduled_data_updater.py --data-type all";
  const msg="这是旧入口: 只跑 wave0 的 SQL 拉取器 (行情/涨跌停/财务/行业/ST/指数), 不经门控, 不动 wave1-5 的因子/面板/财务/残差。\\n\\n日更请用上面的血缘管线面板。仍要跑旧入口?\\n\\n命令: "+cmd;
  if(!confirm(msg)) return;
  startManagedProc(cmd,"旧: wave0 拉取器");
}
// ===== 血缘管线 (定稿 2026-09-04, tools/data_management/lineage_pipeline.py) =====
// 面板状态放模块变量: loadData() 重绘表格时不丢用户的选择
const PIPE={scope:"all", noHeavy:false, heavyOnly:false, rebuild:false, only:"", skip:""};
const PIPE_SCOPES=[["all","全部 wave 0-5 (固定日更)"],["0","只跑 wave0 外部源 (SQL/日历/转债/ETF 原始表)"],["1","只跑 wave1 量价主体 (auxiliary/alpha191/HF/技术类)"],["2","只跑 wave2 派生面板 (财务统一/FF3/行业)"],["3","只跑 wave3 财务因子 (PIT 展开)"],["4","只跑 wave4 残差/状态 (Residual/Barra L2/MarketState)"],["5","只跑 wave5 终端 (FactorStore readiness)"],
  ["from1","从 wave1 起到 5"],["from2","从 wave2 起到 5"],["from3","从 wave3 起到 5"],["from4","从 wave4 起到 5"]];
function pipeRead(){
  const g=id=>document.getElementById(id);
  if(g("pipe-scope")){ PIPE.scope=g("pipe-scope").value; PIPE.noHeavy=g("pipe-noheavy").checked; PIPE.heavyOnly=g("pipe-heavyonly").checked; PIPE.rebuild=g("pipe-rebuild").checked; PIPE.only=g("pipe-only").value.trim(); PIPE.skip=g("pipe-skip").value.trim(); }
}
function pipeCmd(execute){
  pipeRead();
  let c="python tools/data_management/lineage_pipeline.py";
  if(PIPE.scope==="all") c+=" --run-all";
  else if(PIPE.scope.indexOf("from")===0) c+=" --run-all --from-wave "+PIPE.scope.slice(4);
  else c+=" --run-wave "+PIPE.scope;
  if(execute) c+=" --execute";
  if(PIPE.noHeavy) c+=" --no-heavy";
  if(PIPE.heavyOnly) c+=" --heavy-only --hf-workers 1";
  if(PIPE.rebuild) c+=" --rebuild";
  if(PIPE.only) c+=" --only "+PIPE.only;
  if(PIPE.skip) c+=" --skip "+PIPE.skip;
  return c;
}
function pipePreview(){ const el=document.getElementById("pipe-preview"); if(el) el.textContent=pipeCmd(false)+"   (加 --execute 才写盘)"; }
function startPipeline(execute){
  const cmd=pipeCmd(execute);
  const scope=PIPE_SCOPES.find(s=>s[0]===PIPE.scope); const lab=(execute?"日更执行":"日更计划")+" | "+(scope?scope[1].split(" ")[0]:PIPE.scope);
  if(execute){
    const msg="确认真跑血缘管线 (会写 DATA_ROOT)?\\n\\n命令: "+cmd+"\\n\\n顺序: 日历警告 -> 门控 (上游陈旧按受影响命令集拒跑) -> 短路 (全部节点 strict 新鲜才 SKIP) -> 执行 -> 内容终点验收。\\n退出码: 0 全 OK/SKIP (无事日) | 1 FAIL/NOOP/超时 | 2 门控拒跑 | 3 启动对账失败。\\n日志: logs/pipeline_runs/, 进程页可看。\\n读 1min 的重活默认在本次里排队 (信号量 1); 想留给独立终端勾 --no-heavy。"+(PIPE.rebuild?"\\n\\n注意: 勾了 --rebuild = 全量重算口径, 不短路, 耗时按小时计。":"");
    if(!confirm(msg)) return;
  }
  startManagedProc(cmd,lab);
}
function startAcceptance(which){
  if(which==="scan") startManagedProc("python manage.py data scan","验收: data scan (内容终点)");
  else startManagedProc("python tools/verify_ckpt_days.py --all --days 3","验收: checkpoint 抽日重算");
}
function pipePanelHtml(){
  const opts=PIPE_SCOPES.map(s=>`<option value="${s[0]}"${s[0]===PIPE.scope?" selected":""}>${esc(s[1])}</option>`).join("");
  const ck=(id,on,lab,title)=>`<label title="${esc(title)}" style="font-size:12px;white-space:nowrap"><input type="checkbox" id="${id}"${on?" checked":""} onchange="pipePreview()"> ${lab}</label>`;
  return `<div class="pipepanel">
    <div class="pipehead"><b>血缘管线日更</b> <span style="color:#8b949e;font-size:12px">lineage_pipeline.py: 顺序/命令/预算/归属全部从 config/derived_data.yaml 派生; 先看计划再执行; 跑完做两道验收</span></div>
    <div class="piperow">
      <select id="pipe-scope" onchange="pipePreview()">${opts}</select>
      ${ck("pipe-noheavy",PIPE.noHeavy,"--no-heavy","不跑读 1min 的重活 (留给独立终端)")}
      ${ck("pipe-heavyonly",PIPE.heavyOnly,"--heavy-only","只跑读 1min 的重活, 并发 1")}
      ${ck("pipe-rebuild",PIPE.rebuild,"--rebuild","全量重算口径: 不短路, 补历史中段的洞; 耗时按小时计")}
      <input id="pipe-only" placeholder="--only 子串 (如 etf cb)" value="${esc(PIPE.only)}" oninput="pipePreview()" style="width:150px">
      <input id="pipe-skip" placeholder="--skip 子串" value="${esc(PIPE.skip)}" oninput="pipePreview()" style="width:120px">
    </div>
    <div class="piperow">
      <button onclick="startPipeline(false)">看计划 (dry-run)</button>
      <button class="primary" onclick="startPipeline(true)">执行 (--execute)</button>
      <span style="color:#8b949e;font-size:12px">验收:</span>
      <button onclick="startAcceptance('scan')">data scan</button>
      <button onclick="startAcceptance('ckpt')">checkpoint 抽日重算</button>
      <span style="color:#8b949e;font-size:12px">旧入口:</span>
      <button onclick="startAllDataHealthCheck()">健康检查</button>
      <button onclick="startLegacyWave0Pull()">仅 wave0 拉取器</button>
    </div>
    <div id="pipe-preview" class="pipepreview"></div>
  </div>`;
}
// 数据页当前子分组: external(外部依赖) / intermediate(中间数据) / product(最终产物)
let dataGroup="external";
const DATA_GROUP_HINT={
  external:"直接连外部世界的数据 (upstream 挂 EXTERNAL:)。这些不更新, 下面两层全部无意义。",
  intermediate:"清洗层 / 因子层等中间计算物。上游刷新后需重算, 否则出现“文件新、内容旧”。",
  product:"可消费的最终产物 (FactorStore / ML 输出 / 回测 / 单因子测试结果)。"
};
function setDataGroup(g){ dataGroup=g; loadData(); }
async function startContentScan(){
  const msg="启动内容级复检 (分组: "+dataGroup+")?\\n\\n会逐个读文件求“内容终点日”, 首次较慢;\\n"
    +"结果按 (路径,mtime,大小) 缓存, 文件没变的下次直接命中。\\n以 managed process 运行, 可在“进程”页看日志。";
  if(!confirm(msg)) return;
  const r=await jpost("/api/data/content-scan",{group:dataGroup});
  if(r&&r.rc===0){ alert("已启动复检 #"+r.id+", 完成后回本页刷新"); }
  else { alert((r&&r.out)||"启动失败"); }
}
function dataRowsHtml(rows){
  if(!rows.length) return `<tr><td colspan="7" style="color:#8b949e">本组无数据集</td></tr>`;
  return rows.map(x=>{
    const name=`<td title="${esc(x.name)}">${esc(x.name)}</td>`;
    // 有血缘快照判定时以它为准 (内容终点, 认 FROZEN/REMOTE); 否则退回 mtime 快判
    const J=x.judge, JL={OK:"最新",STALE:"落后",MISSING:"缺失",FROZEN:"冻结",REMOTE:"远程",STATIC:"静态",
      UNKNOWN:"待查",UNREADABLE:"读不出",UNSCANNED:"未扫描",UNJUDGED:"不判"};
    const jCls=!J?"":(J==="OK"||J==="FROZEN"||J==="REMOTE"||J==="STATIC")?"fresh":(J==="MISSING"?"missing":"stale");
    const jTd=J?`<td class="${jCls}" title="${esc(x.judge_why||J)}">${esc(JL[J]||J)}</td>`:"";
    if(x.missing || (!J && !x.exists)) return `<tr>${name}${jTd||`<td class="missing">缺失</td>`}<td></td><td></td><td>${esc(x.frequency||"")}</td><td class="dim">${esc(x.rel)}</td><td class="dim">${esc(x.generator)}</td></tr>`;
    if(!x.exists) return `<tr>${name}${jTd}<td class="dim">-</td><td></td><td>${esc(x.frequency||"")}</td><td class="dim">${esc(x.rel)}</td><td class="dim">${esc(x.generator)}</td></tr>`;
    // 内容日期是真判据; mtime 只提示"有没有人动过", 不作结论
    let cd=x.content_date;
    let cdCls="dim", cdTxt="未扫描";
    if(cd&&String(cd).indexOf("ERR:")===0){ cdCls="missing"; cdTxt="读不出("+esc(cd.slice(4))+")"; }
    else if(cd){ cdTxt=esc(cd); cdCls=x.content_cached?"":"dim"; }
    // 扫过但判不出 != 没扫过, 别用同一个留白糊过去
    else if(x.content_cached){ cdCls="missing"; cdTxt="无日期列"; }
    const mtCls=x.stale?"stale":"fresh";
    const mtTxt=(x.max_age==null)?"不判":(x.stale?"静止":"活跃");
    return `<tr>${name}
      ${jTd||`<td class="${mtCls}" title="mtime 龄 ${x.age_days}d, 阈值 ${x.max_age==null?"无":x.max_age+"d"}">${mtTxt}</td>`}
      <td class="${cdCls}">${cdTxt}</td>
      <td class="dim">${x.mtime} (${x.age_days}d)</td>
      <td>${esc(x.frequency||"")}</td>
      <td class="dim">${esc(x.rel)}</td>
      <td class="dim">${esc(x.generator)}</td></tr>`;
  }).join("");
}
async function loadData(){
  const d=await jget("/api/data/overview");
  const counts=d.counts.map(c=>`<div class="datacard"><div class="k">${esc(c.label)}</div><div class="v">${c.count}</div></div>`).join("");
  const ver=`<div class="datacard"><div class="k">store_v2 激活版本</div><div class="v" style="font-size:14px">${esc(d.store_v2_version||"-")}</div></div>`;
  // 可移植版没有 config/derived_data.yaml, 退化成单组 + 只有 mtime 判, 页面照常能开
  const gsrc=(d.lineage===false)?(d.groups||[]).filter(g=>g.total>0):(d.groups||[]);
  const gcards=gsrc.map(g=>{
    const on=(g.key===dataGroup)?" on":"";
    const bad=g.stale+g.missing;
    return `<div class="datacard groupcard${on}" onclick="setDataGroup('${g.key}')">
      <div class="k">${esc(g.label)}</div>
      <div class="v">${g.total}<span style="font-size:12px;font-weight:400"> 项</span></div>
      <div style="font-size:11px;margin-top:2px">${bad?`<span class="stale">静止 ${g.stale} / 缺失 ${g.missing}</span>`:`<span class="fresh">全部活跃</span>`}</div>
    </div>`;
  }).join("");
  const L=await jget("/api/data/layer?group="+encodeURIComponent(dataGroup));
  document.getElementById("datawrap").innerHTML=`
    <div class="datahead">
      <div style="color:#8b949e;font-size:12px">data_root: ${esc(d.data_root)} &nbsp;|&nbsp; 清单派生自 config/derived_data.yaml (勿手写)</div>
    </div>
    ${pipePanelHtml()}
    <div id="lineagewrap"></div>
    <div class="datacards">${gcards}</div>
    <div class="datacards">${ver}${counts}</div>
    <div class="datahead">
      <div style="color:#8b949e;font-size:12px">${esc(DATA_GROUP_HINT[dataGroup]||"")}</div>
      <button onclick="startContentScan()">内容级复检</button>
      <span style="color:#8b949e;font-size:12px">${L.judged_at?`状态/终点取自血缘快照 (内容终点判, 同 data scan), 生成于: ${esc(L.judged_at)} -- 更新后点血缘图"刷新快照"`:`无血缘快照, 状态按 mtime 快判 (不可靠); 内容日期扫描于: ${esc(d.content_scanned_at||"从未")}`}</span>
    </div>
    <table><tr><th>数据集</th><th>${L.judged_at?"状态":"mtime 判"}</th><th>内容终点日</th><th>最后写入</th><th>频率</th><th>路径</th><th>生成器</th></tr>
      ${dataRowsHtml(L.rows||[])}</table>`;
  pipePreview();
  if(typeof loadLineage==="function") loadLineage();   // 血缘点亮图 (static/lineage.js)
}

// ===== 启动 =====
function refreshAll(){ refreshStreams();
  if(document.getElementById("tab-procs").classList.contains("active")) loadProcs();
  if(document.getElementById("tab-data").classList.contains("active")) loadData(); }
installTermControls();
refreshStreams();
restoreTerms().then(()=>refreshTermStatus());
setInterval(()=>{ if(document.getElementById("tab-streams").classList.contains("active")) refreshStreams(); },6000);
setInterval(()=>{ if(document.getElementById("tab-streams").classList.contains("active")) refreshTermStatus(); },3000);
