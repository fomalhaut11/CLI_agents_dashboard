// ===== 实盘跟踪页签 (2026-10-31): v3ra 模拟账户 / 目标持仓 / 输入闸 / 各链路日志, 只读 =====
// 数据 = /api/live?machine=<key> (远端机器读 remote_fetch.py --live 拉回的包; 本机现算), 构造见 live_snapshot.py.
// 页面不写任何东西: "拉取最新" 只是以受管进程重拉数据包.
let liveMachine=(()=>{ try{ return localStorage.getItem("live-machine")||""; }catch(e){ return ""; } })();
let liveData=null, liveLogSel=null, livePosFilter="";

function lvPct(x,d){ return (x===null||x===undefined||x==="")?"":((x*100).toFixed(d===undefined?2:d)+"%"); }
function lvNum(x,d){ return (x===null||x===undefined||x==="")?"":(typeof x==="number"?x.toLocaleString("en-US",{maximumFractionDigits:d===undefined?0:d}):esc(String(x))); }
function lvSign(x){ return x>0?"fresh":(x<0?"stale":""); }
function lvDay(d){ d=String(d||"").replace(/-/g,""); return d.length===8?d.slice(0,4)+"-"+d.slice(4,6)+"-"+d.slice(6):d; }

function setLiveMachine(k){ liveMachine=k; try{ localStorage.setItem("live-machine",k); }catch(e){} loadLive(); }

async function fetchLive(){
  const r=await jpost("/api/live/refresh",{machine:liveData&&liveData.machine||liveMachine});
  if(!r||r.rc!==0){ toast((r&&r.out)||"启动失败"); return; }
  toast("已启动拉取 #"+r.id+" (几秒), 完成后自动刷新");
  const t0=Date.now(), before=liveData&&liveData.meta&&liveData.meta.generated_at;
  const poll=async()=>{
    const d=await jget("/api/live?machine="+encodeURIComponent(liveData.machine));
    if(d&&d.meta&&d.meta.generated_at!==before){ loadLive(); return; }
    if(Date.now()-t0<120000) setTimeout(poll,3000);
  };
  setTimeout(poll,3000);
}

// 需要人看的事: 最近交易日没有目标持仓 / 链路返回码非零 / 输入闸不过 / 锁文件在
function liveAlerts(d){
  const out=[], cal=(d.calendar||{}).days||[], lanes=(d.lanes||{}).lanes||[];
  const last=cal.length?cal[cal.length-1]:null;
  const latest=(d.paper||{}).latest_day;
  const morning=(lanes.find(l=>l.key==="morning")||{}).logs||[];
  if(last){
    const m=morning.find(x=>x.name.indexOf(last.day)>=0);
    if(!last.holdings && m) out.push(["stale",`最近交易日 ${lvDay(last.day)} 没有目标持仓 (最新持仓 ${lvDay(latest)}); 清晨链路 ${m.name} 返回码 ${JSON.stringify(m.rcs)}`]);
    else if(!last.holdings) out.push(["dim",`最近交易日 ${lvDay(last.day)} 还没有目标持仓 (清晨链路 07:30 未见日志; 最新持仓 ${lvDay(latest)})`]);
  }
  // 账户开始记账之前的缺口不算 (模拟盘还没开)
  const acc0=String((((d.account||{}).rows||[])[0]||{}).day||"").replace(/-/g,"");
  cal.slice(0,-1).forEach(c=>{ if(!c.holdings && acc0 && c.day>=acc0) out.push(["stale",`${lvDay(c.day)} 缺目标持仓 (该日账户沿用前一日仓位)`]); });
  const said=out.length&&last&&!last.holdings;
  lanes.forEach(l=>{
    const x=l.logs[0]; if(!x || (l.key==="morning"&&said)) return;
    const bad=Object.keys(x.rcs).filter(k=>x.rcs[k]!==0);
    if(bad.length) out.push([l.key==="morning"?"stale":"warn",`${l.label}: ${x.name} 非零返回码 ${bad.map(k=>k+"="+x.rcs[k]).join(" ")}`]);
  });
  const g=d.gate_latest;
  if(g && g.ok===false) out.push(["warn",`20:00 输入闸 (${esc(g.day||"")}) 不通过: ${g.n_blocking} 个特征阻塞`+
    `${g.optimizer_ok===false?", 优化器输入不齐":""} -- 分钟类特征当晚拿不到属正常, 以 07:30 清晨链路为准`]);
  if((d.lanes||{}).lock) out.push(["warn",`锁文件在: ${d.lanes.lock.who} (${d.lanes.lock.mtime} 起), 可能正在跑`]);
  return out;
}

function liveKpis(d){
  const rows=(d.account||{}).rows||[];
  if(!rows.length) return `<div class="dim">没有账户记录 (${esc((d.account||{}).dir||"")})</div>`;
  const r=rows[rows.length-1], cum=(r.nav_rel||1)-1;
  const tile=(lab,val,cls,sub)=>`<div class="lv-tile"><div class="lv-tl">${lab}</div><div class="lv-tv ${cls||""}">${val}</div>${sub?`<div class="lv-ts">${sub}</div>`:""}</div>`;
  return `<div class="lv-tiles">
    ${tile("账户最新日",esc(r.day),"",`${rows.length} 个记账日, 起于 ${esc(rows[0].day)}`)}
    ${tile("净值",lvNum(r.nav),"",`初始 ${lvNum(rows[0].equity_open)}`)}
    ${tile("累计超额 (对中证1000)",lvPct(cum),lvSign(cum),`账户 ${lvPct(r.nav/rows[0].equity_open-1)}`)}
    ${tile("当日超额",lvPct(r.excess),lvSign(r.excess),`账户 ${lvPct(r.ret)} / 基准 ${lvPct(r.bench_ret)}`)}
    ${tile("持仓 / 成交 / 受阻",`${lvNum(r.n_hold)} / ${lvNum(r.n_filled)} / ${lvNum(r.n_blocked)}`,"",`换手 ${lvPct(r.turnover)} · 费用 ${lvNum(r.fees)}`)}
    ${tile("现金",lvNum(r.cash),"",`目标偏离 L1 ${lvPct(r.target_gap_l1)}`)}
  </div>`;
}

// 链路日历: 行 = 最近交易日, 列 = 各链路当日日志 (返回码) + 目标持仓 + 记账
function liveCalendar(d){
  const cal=d.calendar||{}, lanes=(d.lanes||{}).lanes||[];
  if(cal.error) return `<div class="dim">交易日历读不了: ${esc(cal.error)}</div>`;
  const byDay={};
  lanes.forEach(l=>l.logs.forEach(x=>{ const k=(x.name.match(/(\d{8})/)||[])[1]; if(k) (byDay[l.key]=byDay[l.key]||{})[k]=x; }));
  const cell=(l,day)=>{
    const x=(byDay[l.key]||{})[day];
    if(!x) return `<td class="dim">-</td>`;
    const bad=Object.keys(x.rcs).filter(k=>x.rcs[k]!==0);
    const t=Object.keys(x.rcs).map(k=>k+"="+x.rcs[k]).join(" ")||"无返回码行";
    const cls=bad.length?(l.key==="morning"&&bad.indexOf("paper")>=0?"stale":"lv-warn"):"fresh";
    return `<td class="${cls} lv-click" title="${esc(t)}" onclick="liveShowLog('${l.key}','${esc(x.name)}')">${bad.length?esc(bad.join(",")):"OK"}</td>`;
  };
  const rows=(cal.days||[]).slice().reverse().map(c=>`<tr><td>${lvDay(c.day)}</td>
    ${lanes.map(l=>cell(l,c.day)).join("")}
    <td class="${c.holdings?"fresh":"stale"}">${c.holdings?"有":"缺"}</td>
    <td class="${c.account?"fresh":"dim"}">${c.account?"有":"-"}</td></tr>`).join("");
  return `<table class="lv-cal"><tr><th>交易日</th>${lanes.map(l=>`<th title="${esc(l.label)}">${esc(l.at)} ${esc(l.label.split(" ").slice(1).join(" ").replace(/\(.*\)/,""))}</th>`).join("")}<th>目标持仓</th><th>账户记账</th></tr>${rows}</table>
    <div class="lv-note">格子 = 当天那份日志里非零的返回码 (OK = 全为 0), 点开看日志末尾。20:00 日更的 wave0/1 与输入闸非零常见 (源头当晚无新数据 / 分钟特征次晨才到), 以 07:30 清晨链路的 paper 为准。</div>`;
}

// 折线: 累计超额 (nav_rel - 1), 单序列; 悬停显示当日数值
function liveChart(d){
  const rows=((d.account||{}).rows||[]).filter(r=>r.nav_rel!=null);
  if(rows.length<2) return "";
  const W=760,H=200,L=56,R=26,T=14,B=26;
  const ys=rows.map(r=>r.nav_rel-1), lo=Math.min(0,...ys), hi=Math.max(0,...ys), pad=(hi-lo)*0.12||0.001;
  const y0=lo-pad, y1=hi+pad;
  const X=i=>L+(W-L-R)*i/(rows.length-1), Y=v=>T+(H-T-B)*(1-(v-y0)/(y1-y0));
  const ticks=[]; const step=Math.pow(10,Math.floor(Math.log10((y1-y0)/3)))||0.001;
  const s=(y1-y0)/step>8?step*2:step;
  for(let v=Math.ceil(y0/s)*s; v<=y1; v+=s) ticks.push(v);
  const path=ys.map((v,i)=>(i?"L":"M")+X(i).toFixed(1)+" "+Y(v).toFixed(1)).join(" ");
  const xl=rows.map((r,i)=>(i===0||i===rows.length-1||i%Math.ceil(rows.length/6)===0)?`<text x="${X(i)}" y="${H-8}" text-anchor="middle">${esc(String(r.day).slice(5))}</text>`:"").join("");
  return `<div class="lv-chart"><div class="lv-ct">累计超额 (账户 / 中证1000 - 1, 按竞价价记账)</div>
    <svg viewBox="0 0 ${W} ${H}" width="100%" style="max-width:${W}px" id="lv-svg">
      ${ticks.map(v=>`<line x1="${L}" x2="${W-R}" y1="${Y(v)}" y2="${Y(v)}" class="${Math.abs(v)<1e-12?"lv-zero":"lv-grid"}"/><text x="${L-6}" y="${Y(v)+4}" text-anchor="end">${(v*100).toFixed(2)}%</text>`).join("")}
      <path d="${path}" class="lv-line"/>
      ${ys.map((v,i)=>`<circle cx="${X(i)}" cy="${Y(v)}" r="4" class="lv-dot"/>`).join("")}
      <g class="lv-x">${xl}</g>
      <line id="lv-cross" y1="${T}" y2="${H-B}" class="lv-cross" style="display:none"/>
      ${rows.map((r,i)=>`<rect x="${X(i)-(W-L-R)/(rows.length-1)/2}" y="${T}" width="${(W-L-R)/(rows.length-1)}" height="${H-T-B}" fill="transparent"
        onmousemove="liveHover(event,${i},${X(i)})" onmouseleave="liveHover(null)"/>`).join("")}
    </svg><div class="lv-tip" id="lv-tip"></div></div>`;
}
function liveHover(ev,i,x){
  const tip=document.getElementById("lv-tip"), cr=document.getElementById("lv-cross");
  if(!ev){ tip.style.display="none"; cr.style.display="none"; return; }
  const r=((liveData.account||{}).rows||[]).filter(r=>r.nav_rel!=null)[i];
  cr.setAttribute("x1",x); cr.setAttribute("x2",x); cr.style.display="";
  tip.innerHTML=`<b>${esc(r.day)}</b><br>累计超额 ${lvPct(r.nav_rel-1)}<br>当日 账户 ${lvPct(r.ret)} / 基准 ${lvPct(r.bench_ret)} / 超额 ${lvPct(r.excess)}<br>净值 ${lvNum(r.nav)}`;
  const box=ev.currentTarget.ownerSVGElement.getBoundingClientRect();
  tip.style.left=(ev.clientX-box.left+14)+"px"; tip.style.top=(ev.clientY-box.top+8)+"px"; tip.style.display="block";
}

function liveAccountTable(d){
  const rows=((d.account||{}).rows||[]).slice().reverse();
  return `<table><tr><th>日</th><th>账户</th><th>基准</th><th>超额</th><th>累计超额</th><th>净值</th><th>持仓</th><th>成交</th><th>受阻</th><th>换手</th><th>费用</th><th>分红</th><th>目标偏离</th><th>价格来源</th></tr>
    ${rows.map(r=>`<tr><td>${esc(r.day)}</td><td class="${lvSign(r.ret)}">${lvPct(r.ret)}</td><td>${lvPct(r.bench_ret)}</td>
      <td class="${lvSign(r.excess)}">${lvPct(r.excess)}</td><td class="${lvSign(r.nav_rel-1)}">${lvPct(r.nav_rel-1)}</td><td>${lvNum(r.nav)}</td>
      <td>${lvNum(r.n_hold)}</td><td>${lvNum(r.n_filled)}</td><td>${lvNum(r.n_blocked)}</td><td>${lvPct(r.turnover)}</td><td>${lvNum(r.fees)}</td>
      <td>${lvNum(r.cash_dividend)}</td><td>${lvPct(r.target_gap_l1)}</td><td class="dim" title="${esc(r.holdings||"")}">${esc(String(r.price_source||"").slice(0,48))}</td></tr>`).join("")}</table>`;
}

function livePaperTable(d){
  const days=((d.paper||{}).days||[]).slice().reverse();
  const gate=g=>!g?`<td class="dim">-</td>`:`<td class="${g.ok?"fresh":"stale"}" title="${esc(JSON.stringify(g.blocking||{}).slice(0,400))}">${g.ok?"通过":"阻塞 "+g.n_blocking}${g.optimizer_ok===false?" / 优化器":""}</td>`;
  return `<table><tr><th>持仓日</th><th>输入闸</th><th>持仓数</th><th>事前 TE</th><th>换手</th><th>打分覆盖</th><th>放松</th><th>失败</th><th>特征缺失率</th><th>豁免</th><th>模型</th><th>写入</th></tr>
    ${days.map(x=>{ const s=x.summary||{}; const al=Object.keys(s.input_gate_allowed||{});
      return `<tr><td>${lvDay(x.dir)}</td>${gate(x.gate)}<td>${lvNum(s.n_hold)}</td><td>${lvPct(s.te_ex_ante)}</td><td>${lvPct(s.turnover)}</td>
      <td>${lvPct(s.score_cov,1)}</td><td class="${s.n_relaxed?"lv-warn":""}">${lvNum(s.n_relaxed)}</td><td class="${s.n_fail?"stale":""}">${lvNum(s.n_fail)}</td>
      <td>${lvPct(s.model_feature_nan_share)}</td><td class="${al.length?"lv-warn":""}" title="${esc(al.join(", "))}">${al.length||""}</td>
      <td class="dim">${esc(String(s.models||"").replace(/^results\/v3ra\//,"").replace(/\/models$/,""))} r${lvNum(s.round)}</td><td class="dim">${esc(x.mtime||"")}</td></tr>`; }).join("")}</table>`;
}

function livePositions(d){
  const p=d.positions||{}, f=livePosFilter.trim();
  const tc=p.trade_counts||{};
  const tcs=Object.keys(tc).sort().map(k=>`<span class="badge ${/filled/.test(k)?"clean":"dirty"}">${esc(k)} ${tc[k]}</span>`).join(" ");
  const hist=(p.trade_history||[]).slice().reverse().map(h=>{ const c=h.counts||{}; const n=k=>Object.keys(c).filter(x=>x.indexOf(k)>=0).reduce((a,x)=>a+c[x],0);
    return `<tr><td>${lvDay(h.day)}</td><td>${n(":filled")}</td><td>${Object.keys(c).filter(x=>x.indexOf(":filled")<0).map(x=>esc(x)+" "+c[x]).join(" · ")}</td></tr>`; }).join("");
  let pos=(p.positions||[]).slice().sort((a,b)=>(b.weight||0)-(a.weight||0));
  if(f) pos=pos.filter(r=>String(r.code).indexOf(f)>=0);
  const tr=(p.trades||[]).filter(r=>!f||String(r.code).indexOf(f)>=0);
  return `<div class="lv-cols"><div>
      <div class="lv-ct">持仓 ${lvDay(p.day)} (${(p.positions||[]).length} 只) <input placeholder="代码过滤" value="${esc(f)}" oninput="livePosFilter=this.value; liveRedrawPos()" style="width:110px"></div>
      <div class="lv-scroll"><table><tr><th>代码</th><th>股数</th><th>收盘</th><th>市值</th><th>权重</th><th>目标权重</th><th>偏离</th></tr>
      ${pos.map(r=>`<tr><td>${esc(r.code)}</td><td>${lvNum(r.shares)}</td><td>${lvNum(r.close,2)}</td><td>${lvNum(r.value)}</td><td>${lvPct(r.weight,3)}</td><td>${lvPct(r.target_weight,3)}</td><td class="dim">${lvPct((r.weight||0)-(r.target_weight||0),3)}</td></tr>`).join("")}</table></div></div>
    <div><div class="lv-ct">成交 ${lvDay(p.trade_day)} ${tcs}</div>
      <div class="lv-scroll"><table><tr><th>代码</th><th>方向</th><th>股数</th><th>状态</th><th>价格</th><th>金额</th><th>拆单</th><th>费用</th></tr>
      ${tr.map(r=>`<tr><td>${esc(r.code)}</td><td>${esc(r.side)}</td><td>${lvNum(r.qty)}</td><td class="${r.status==="filled"?"":"lv-warn"}">${esc(r.status)}</td><td>${lvNum(r.price,2)}</td><td>${lvNum(r.amount)}</td><td>${lvNum(r.n_orders)}</td><td>${lvNum(r.fee,2)}</td></tr>`).join("")}</table></div>
      <div class="lv-ct" style="margin-top:8px">近期成交状态</div><table><tr><th>日</th><th>成交</th><th>其余</th></tr>${hist}</table></div></div>`;
}
function liveRedrawPos(){ const el=document.getElementById("lv-pos"); if(el&&liveData){ el.innerHTML=livePositions(liveData); const i=el.querySelector("input"); if(i){ i.focus(); i.setSelectionRange(i.value.length,i.value.length); } } }

function liveLanes(d){
  const ln=d.lanes||{};
  return (ln.lanes||[]).map(l=>`<details${l.key==="morning"?" open":""}><summary>${esc(l.label)} <span class="dim">${esc(l.prefix)}*.log, 最近 ${l.logs.length} 份</span></summary>
    <table><tr><th>日志</th><th>最后写入</th><th>大小</th><th>END</th><th>返回码</th></tr>
    ${l.logs.map(x=>`<tr class="lv-click" onclick="liveShowLog('${l.key}','${esc(x.name)}')"><td>${esc(x.name)}</td><td>${esc(x.mtime)}</td><td>${x.size_kb} KB</td>
      <td class="${x.ended?"fresh":"dim"}">${x.ended?"有":"-"}</td><td>${Object.keys(x.rcs).map(k=>`<span class="${x.rcs[k]===0?"fresh":"stale"}">${esc(k)}=${x.rcs[k]}</span>`).join(" ")}</td></tr>`).join("")}</table></details>`).join("")
    +(ln.skipped&&ln.skipped.length?`<details><summary>daily_skipped.log (因锁跳过的运行, 末 10 行)</summary><pre class="lv-log">${esc(ln.skipped.join("\n"))}</pre></details>`:"");
}
function liveShowLog(key,name){
  liveLogSel=[key,name];
  const l=((liveData.lanes||{}).lanes||[]).find(x=>x.key===key), x=l&&l.logs.find(y=>y.name===name);
  const el=document.getElementById("lv-logview"); if(!el) return;
  el.innerHTML=x?`<div class="lv-ct">${esc(l.label)} · ${esc(name)} · 末 ${x.tail.length} 行 <button onclick="document.getElementById('lv-logview').innerHTML=''">关闭</button></div><pre class="lv-log">${esc(x.tail.join("\n"))}</pre>`:"";
  el.scrollIntoView({behavior:"smooth",block:"nearest"});
}

function liveLiveFiles(d){
  const v=d.live||{};
  return `<div class="lv-note">竞价快照: ${(v.quotes||[]).slice(-3).map(q=>esc(q.name)).join(", ")||"无"} · open_today.pkl ${esc(v.open_today||"无")} ·
    开盘 30 分钟: ${(v.intraday||[]).slice(-3).map(x=>`${esc(x.day)} (${x.n_files} 份${x.vwap30?", vwap30 "+esc(x.vwap30):""})`).join(", ")||"无"}</div>`;
}

async function loadLive(){
  const wrap=document.getElementById("livewrap");
  const d=await jget("/api/live"+(liveMachine?"?machine="+encodeURIComponent(liveMachine):""));
  liveData=d;
  const bar=`<div class="datahead machinebar"><span style="color:#8b949e;font-size:12px">机器:</span> `+(d.machines||[]).map(m=>
    `<button class="${m.key===d.machine?"primary":""}" onclick="setLiveMachine('${esc(m.key)}')">${esc(m.label)}</button>`).join(" ")
    +(d.machine!=="local"?` <button class="primary" onclick="fetchLive()">拉取最新</button>`:"")+`</div>`;
  if(d.missing){
    wrap.innerHTML=bar+`<div class="rs-hint">${d.error?esc(d.error):"还没有这台机器的实盘数据包"}。点 "拉取最新" (受管进程跑 remote_fetch.py --live, 几秒)。</div>`;
    return;
  }
  const m=d.meta||{}, al=liveAlerts(d);
  wrap.innerHTML=bar+`<div class="lv-meta">${esc(m.host||"")} · 检出 <code>${esc(m.root||"")}</code> · 生产模型 <code>${esc(m.models_dir||"?")}</code> ·
      数据包生成于 ${esc(m.generated_at||"?")}${d.machine!=="local"?` (拉回 ${fmtAge(d.fetched_age_s)}前)`:" (本机现算)"}</div>
    ${al.length?`<div class="lv-alerts">${al.map(a=>`<div class="${a[0]==="warn"?"lv-warn":a[0]}">${a[0]==="stale"?"[!] ":a[0]==="warn"?"[?] ":""}${a[1]}</div>`).join("")}</div>`:`<div class="lv-alerts fresh">最近交易日各链路无异常</div>`}
    ${liveKpis(d)}
    ${liveChart(d)}
    <h3>链路日历</h3>${liveCalendar(d)}
    <div id="lv-logview"></div>
    <h3>模拟账户逐日</h3><div class="lv-scroll">${liveAccountTable(d)}</div>
    <h3>目标持仓 (近 ${((d.paper||{}).days||[]).length} 个持仓日)</h3>${livePaperTable(d)}
    <h3>持仓与成交</h3><div id="lv-pos">${livePositions(d)}</div>
    <h3>链路日志</h3>${liveLanes(d)}${liveLiveFiles(d)}
    <div class="rs-hint">只读视图。持仓目录日 D = D 日 9:25 竞价成交 (07:30 清晨链路产出); 账户按竞价价记账, 偏乐观, 只作参考口径。
      v3ra 说明见 docs/architecture/modules/production_v3ra.md。</div>`;
  if(liveLogSel) liveShowLog(liveLogSel[0],liveLogSel[1]);
}
