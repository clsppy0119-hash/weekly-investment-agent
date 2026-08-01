(() => {
  const n = v => Number.isFinite(Number(v)) ? Number(v) : 0;
  const fmt = v => new Intl.NumberFormat('zh-TW',{maximumFractionDigits:0}).format(v || 0);
  const pct = v => Number.isFinite(v) ? `${v>=0?'+':''}${v.toFixed(1)}%` : '資料不足';
  state.risk ||= { cash: 0, riskLimit: 2, stops: {}, industries: {}, journal: [] };
  const risk = state.risk;
  const panel = document.createElement('section'); panel.className='card panel watch';
  panel.innerHTML=`<h2>風險管理與決策日誌</h2><p class="label">風險數字為輔助判斷；請依自己的資金、期限與承受度設定。</p>
  <div class="form"><input id="cashBalance" class="input" type="number" min="0" placeholder="可用現金"><input id="riskLimit" class="input" type="number" min="0.1" max="100" step="0.1" placeholder="單筆風險上限 %"><button id="saveRisk">儲存設定</button></div><div class="grid" id="riskCards"></div>
  <h2 style="margin-top:18px">停損與產業分類</h2><div class="form"><input id="riskCode" class="input" inputmode="numeric" maxlength="6" placeholder="股票代碼"><input id="stopPrice" class="input" type="number" min="0" step="0.01" placeholder="停損價"><input id="industry" class="input" placeholder="產業，例如半導體"><button id="saveStop">儲存</button></div><div class="table-wrap"><table class="table"><thead><tr><th>股票</th><th>目前價</th><th>停損價</th><th>距停損</th><th>產業</th></tr></thead><tbody id="stopRows"></tbody></table></div>
  <h2 style="margin-top:18px">買賣決策日誌</h2><div class="form"><select id="journalType"><option value="買進理由">買進理由</option><option value="賣出回顧">賣出回顧</option></select><input id="journalCode" class="input" inputmode="numeric" maxlength="6" placeholder="股票代碼"><input id="journalText" class="input" placeholder="理由、條件或回顧"><button id="addJournal">新增日誌</button></div><div id="journalRows"></div>`;
  document.querySelector('#portfolioTools')?.after(panel);
  function holdingSeries(items) {
    const hist = window.__quoteHistory || {}; const dates = [...new Set(items.flatMap(x => (hist[x.code]||[]).map(p=>p.date)))].sort();
    return dates.map(date => items.reduce((sum,x) => { const p=(hist[x.code]||[]).find(v=>v.date===date); return sum + (p ? p.close*n(x.shares) : 0); }, n(risk.cash)));
  }
  function render() {
    const items=positions().filter(x=>x.shares>0), market=items.reduce((s,x)=>s+n(x.market),0), total=market+n(risk.cash);
    const series=holdingSeries(items); let peak=0,mdd=0; series.forEach(v=>{peak=Math.max(peak,v); if(peak) mdd=Math.min(mdd,(v/peak-1)*100)});
    const returns=series.slice(1).map((v,i)=>series[i]?v/series[i]-1:0); const mean=returns.reduce((s,v)=>s+v,0)/(returns.length||1); const vol=returns.length>1?Math.sqrt(returns.reduce((s,v)=>s+(v-mean)**2,0)/(returns.length-1))*Math.sqrt(252)*100:null;
    const largest=items.reduce((a,x)=>!a||x.market>a.market?x:a,null); const industries={}; items.forEach(x=>{const k=risk.industries[x.code]||'未分類';industries[k]=(industries[k]||0)+n(x.market)}); const topIndustry=Object.entries(industries).sort((a,b)=>b[1]-a[1])[0];
    document.querySelector('#riskCards').innerHTML=[['可用現金',`NT$ ${fmt(risk.cash)}`],['現金占比',pct(total?risk.cash/total*100:null)],['個股最大集中',largest?`${largest.name} ${pct(largest.market/total*100)}`:'尚無持股'],['產業最大集中',topIndustry?`${topIndustry[0]} ${pct(topIndustry[1]/total*100)}`:'尚無持股'],['估算 MDD',series.length>1?pct(mdd):'歷史資料不足'],['年化波動度',vol==null?'歷史資料不足':pct(vol)]].map(x=>`<article class="card metric"><div class="label">${x[0]}</div><div class="value" style="font-size:18px">${x[1]}</div></article>`).join('');
    document.querySelector('#stopRows').innerHTML=items.length?items.map(x=>{const stop=n(risk.stops[x.code]);const gap=stop&&q(x.code)?.price?(q(x.code).price/stop-1)*100:null;return `<tr><td>${x.name}（${x.code}）</td><td>${q(x.code)?.price||'—'}</td><td>${stop||'未設定'}</td><td>${gap==null?'—':pct(gap)}</td><td>${risk.industries[x.code]||'未分類'}</td></tr>`}).join(''):'<tr><td colspan="5" class="hint">尚無持股</td></tr>';
    document.querySelector('#journalRows').innerHTML=risk.journal.length?risk.journal.slice().reverse().map(j=>`<div class="strategy"><b>${j.type}｜${q(j.code)?.name||j.code}（${j.code}）</b><br><span class="hint">${j.date}・${j.text}</span></div>`).join(''):'<div class="hint">尚未新增決策日誌</div>';
  }
  document.querySelector('#cashBalance').value=risk.cash; document.querySelector('#riskLimit').value=risk.riskLimit;
  document.querySelector('#saveRisk').onclick=()=>{risk.cash=n(document.querySelector('#cashBalance').value);risk.riskLimit=n(document.querySelector('#riskLimit').value)||2;save();render()};
  document.querySelector('#saveStop').onclick=()=>{const code=document.querySelector('#riskCode').value.trim();if(!q(code))return alert('請輸入可辨識股票代碼。');risk.stops[code]=n(document.querySelector('#stopPrice').value);risk.industries[code]=document.querySelector('#industry').value.trim()||'未分類';save();render()};
  document.querySelector('#addJournal').onclick=()=>{const code=document.querySelector('#journalCode').value.trim(),text=document.querySelector('#journalText').value.trim();if(!q(code)||!text)return alert('請填入股票代碼與內容。');risk.journal.push({type:document.querySelector('#journalType').value,code,text,date:new Date().toLocaleDateString('zh-TW')});save();document.querySelector('#journalText').value='';render()};
  fetch('quotes.json',{cache:'no-store'}).then(r=>r.json()).then(d=>{window.__quoteHistory=d.history||window.__quoteHistory;render()}).catch(render);render();
})();
