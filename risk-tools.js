(() => {
  const n=value=>Number.isFinite(Number(value))?Number(value):0, pct=value=>Number.isFinite(value)?`${value>=0?'+':''}${value.toFixed(1)}%`:'資料不足';
  const panel=document.createElement('section'); panel.className='card panel watch';
  panel.innerHTML=`<h2>Agent 自動風險管理</h2><p class="label">依持股、投資方式與歷史行情自動整理，不需要手動填寫停損、產業或日誌。</p><div class="grid" id="riskCards"></div><h2 style="margin-top:18px">Agent 停損與產業判讀</h2><div class="table-wrap"><table class="table"><thead><tr><th>股票</th><th>目前價</th><th>建議停損</th><th>風險距離</th><th>產業</th></tr></thead><tbody id="stopRows"></tbody></table></div><h2 style="margin-top:18px">Agent 買賣決策日誌</h2><div id="journalRows"></div><p class="hint">日誌是依交易與市場資料產生的系統草稿，不會假裝是使用者本人的買賣理由。</p>`;
  document.querySelector('#portfolioTools')?.after(panel);
  function industry(item){const name=item.name||'';if(/^00/.test(item.code)||/ETF|指數|高股息|基金/.test(name))return'ETF／基金';if(/金|銀|證|保險/.test(name))return'金融';if(/半導體|矽|晶|電/.test(name))return'電子／半導體（推測）';return'待產業資料更新'}
  function stop(item){const style=state.investStyle||'value',rate=style==='swing'?.93:style==='dividend'?.90:.85;return n(q(item.code)?.price)*rate}
  function renderAgent(){
    const items=positions().filter(x=>x.shares>0),market=items.reduce((s,x)=>s+n(x.market),0),largest=items.reduce((a,x)=>!a||x.market>a.market?x:a,null),groups={};items.forEach(x=>{const k=industry(x);groups[k]=(groups[k]||0)+n(x.market)});const top=Object.entries(groups).sort((a,b)=>b[1]-a[1])[0];
    document.querySelector('#riskCards').innerHTML=[['可用現金','未連接券商'],['個股最大集中',largest&&market?`${largest.name} ${pct(largest.market/market*100)}`:'尚無持股'],['產業最大集中',top&&market?`${top[0]} ${pct(top[1]/market*100)}`:'尚無持股'],['風險資料','隨每日收盤更新']].map(x=>`<article class="card metric"><div class="label">${x[0]}</div><div class="value" style="font-size:18px">${x[1]}</div></article>`).join('');
    document.querySelector('#stopRows').innerHTML=items.length?items.map(x=>{const price=n(q(x.code)?.price),s=stop(x),gap=s?(price/s-1)*100:null;return`<tr><td>${x.name}（${x.code}）</td><td>${price||'—'}</td><td>${s?s.toFixed(2):'—'}</td><td>${gap==null?'—':pct(gap)}</td><td>${industry(x)}</td></tr>`}).join(''):'<tr><td colspan="5" class="hint">尚無持股</td></tr>';
    document.querySelector('#journalRows').innerHTML=state.transactions.length?state.transactions.slice().reverse().map(t=>{const z=q(t.code),change=z?.price&&t.price?(z.price/t.price-1)*100:null;return`<div class="strategy"><b>${t.side==='sell'?'賣出回顧草稿':'買進紀錄草稿'}｜${z?.name||t.name||t.code}（${t.code}）</b><br><span class="hint">${t.date}・${t.side==='sell'?'賣出':'買進'} ${Number(t.shares).toLocaleString()} 股，成交價 NT$ ${Number(t.price).toLocaleString()}。${change==null?'等待最新報價比較。':`目前相對成交價 ${pct(change)}。`}</span></div>`}).join(''):'<div class="hint">新增交易後，Agent 會自動建立決策紀錄草稿。</div>';
  }
  const baseRender=render; render=function(){baseRender();renderAgent()};
  fetch('quotes.json',{cache:'no-store'}).then(r=>r.json()).then(d=>{window.__quoteHistory=d.history||window.__quoteHistory;renderAgent()}).catch(renderAgent);renderAgent();
})();
