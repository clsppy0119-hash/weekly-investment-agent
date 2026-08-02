(() => {
  const dividendForm=document.querySelector('#divCode')?.closest('.form'); if(dividendForm)dividendForm.hidden=true;
  const heading=[...document.querySelectorAll('h2')].find(node=>node.textContent.includes('股利／除權息'));
  if(heading){heading.textContent='Agent 股利／除權息追蹤';const note=heading.nextElementSibling;if(note)note.textContent='Agent 使用網站已下載的整批公開市場資料估算，不會把你的持股代碼傳送給外部服務。實際入帳、稅額與二代健保費仍以券商及稅務資料為準。'}
  function renderDividendAgent(){
    if(state.dividends.length)return;
    const items=positions().filter(item=>item.shares>0),body=document.querySelector('#dividendRows');if(!body)return;
    body.innerHTML=items.length?items.map(item=>{const data=(typeof fundamentals==='object'&&fundamentals[item.code])||{},yieldText=Number.isFinite(data.dividendYield)?`${data.dividendYield.toFixed(2)}%`:'資料不足';return`<tr><td>等待官方批次資料</td><td>${item.name}（${item.code}）</td><td colspan="2">目前參考殖利率 ${yieldText}</td><td>Agent 追蹤中</td><td></td></tr>`}).join(''):'<tr><td colspan="6" class="hint">新增持股後，Agent 會自動追蹤股利與除權息。</td></tr>';
  }
  const baseRender=render;render=function(){baseRender();renderDividendAgent()};renderDividendAgent();
  fetch('data/fundamentals-coverage.json',{cache:'no-store'}).then(response=>response.ok?response.json():null).then(data=>{
    if(!data||document.querySelector('#fundamentalCoverage'))return;
    const total=data.universeCodes||data.semiconductorCodes?.length||0,metric=key=>`${data.metrics?.[key]||0}/${total}`;
    const section=document.createElement('section');section.id='fundamentalCoverage';section.className='card panel';
    const active=data.activeStage||'半導體';
    section.innerHTML=`<h2>產業財務資料覆蓋率</h2><p class="label">自動隊列：半導體 → 電子其他 → 金融 → 傳產與其他 → 興櫃；候選股優先。</p><div class="strategy"><b>目前補齊：${active}</b><br><span class="hint">本階段 ${data.stageCoverage?.[active]?.reviewed??data.successfulCodes??0}/${data.currentStageCodes??total} 檔已審核・本批成功 ${data.successfulCodes||0} 檔<br>全資料池核心指標已備 ${data.enrichedCodes??0}/${total} 檔・尚待補齊 ${data.remainingCodes??'暫無'} 檔<br>TTM EPS ${metric('eps')}・TTM ROE ${metric('roe')}・負債比 ${metric('debtRatio')}・近五年期間 ${data.fiveYearHistory||0}/${total}<br>更新：${data.updatedAt||'暫無'}。缺失資料會維持「資料不足」，不會以 0 補值。</span></div>`;
    const app=document.querySelector('main.app');if(app)app.insertBefore(section,app.querySelector('.footer'));
  }).catch(()=>{});
  fetch('strategy_data/recommendations.json',{cache:'no-store'}).then(response=>response.ok?response.json():null).then(data=>{
    if(!data||document.querySelector('#decisionTraceability'))return;
    const records=(data.recommendations||[]).slice(-6).reverse();
    const ready=records.filter(item=>item.decisionRecord?.decision==='正式研究候選').length;
    const text=records.length?records.map(item=>{
      const d=item.decisionRecord||{};
      const flags=(d.riskFlags||[]).slice(0,2).join('、')||'無新增旗標';
      const outcomes=item.outcomes||{};
      const progress=[5,20,60].map(days=>outcomes[String(days)]?.status==='complete'?`${days}日 ${outcomes[String(days)].returnPct>=0?'+':''}${outcomes[String(days)].returnPct}%`:`${days}日待驗證`).join('・');
      return `<div class="strategy"><b>${item.name||item.code}（${item.code}）</b>・${d.decision||'舊紀錄待補建'}<br><span class="hint">資料權重 ${d.dataCompleteness?.analysisWeightPct??item.coverage??0}%・策略 ${d.strategyVersion||item.strategyVersion||'1.0'}・${d.reconstructed?'補建紀錄':'原始快照'}<br>風險：${flags}<br>後續檢核：${progress}</span></div>`;
    }).join(''):'<div class="strategy">尚無可追溯決策紀錄。</div>';
    const section=document.createElement('section');section.id='decisionTraceability';section.className='card panel';
    section.innerHTML=`<h2>決策可追溯紀錄</h2><p class="label">保存每次結論的資料快照、來源、缺失與風險旗標；「正式研究候選」不是買進指令。</p><div class="strategy"><b>近期正式研究候選：${ready} 筆</b><br><span class="hint">5／20／60 個交易日後自動檢核；舊紀錄會標示「補建」。</span></div>${text}`;
    const app=document.querySelector('main.app');if(app)app.insertBefore(section,app.querySelector('.footer'));
  }).catch(()=>{});
})();

/* Replaces the compact decision summary with a filterable audit panel. */
(() => {
  const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
  fetch('strategy_data/recommendations.json',{cache:'no-store'}).then(response=>response.ok?response.json():null).then(data=>{
    if(!data)return;
    document.querySelector('#decisionTraceability')?.remove();
    const records=[...(data.recommendations||[])].sort((a,b)=>String(b.date||'').localeCompare(String(a.date||'')));
    const section=document.createElement('section');
    section.id='decisionTraceability'; section.className='card panel';
    section.innerHTML=`<h2>決策可追溯紀錄</h2><p class="label">每筆保留資料快照、來源、風險旗標與 5／20／60 日檢核；研究候選不是買進指令。</p><div class="form" style="grid-template-columns:1fr 1fr 1fr"><input id="decisionCodeFilter" class="input" inputmode="numeric" placeholder="股票代碼或名稱"><select id="decisionStatusFilter" class="input"><option value="">全部結論</option><option>正式研究候選</option><option>僅追蹤</option><option>暫不決策</option></select><select id="decisionCoverageFilter" class="input"><option value="0">全部完整度</option><option value="80">80% 以上</option><option value="70">70% 以上</option><option value="1">不足 70%</option></select></div><div id="decisionStats" class="strategy" style="margin-top:10px"></div><div id="decisionRows"></div>`;
    const app=document.querySelector('main.app'); if(!app)return; app.insertBefore(section,app.querySelector('.footer'));
    const codeFilter=section.querySelector('#decisionCodeFilter'), statusFilter=section.querySelector('#decisionStatusFilter'), coverageFilter=section.querySelector('#decisionCoverageFilter'), rows=section.querySelector('#decisionRows');
    const render=()=>{
      const query=codeFilter.value.trim().toLowerCase(), status=statusFilter.value, threshold=Number(coverageFilter.value);
      const filtered=records.filter(item=>{
        const decision=item.decisionRecord?.decision||'舊紀錄待補建', coverage=Number(item.decisionRecord?.dataCompleteness?.analysisWeightPct??item.coverage??0);
        const matchText=!query||String(item.code||'').includes(query)||String(item.name||'').toLowerCase().includes(query);
        const matchStatus=!status||decision===status;
        const matchCoverage=threshold===0||threshold===1?threshold!==1||coverage<70:coverage>=threshold;
        return matchText&&matchStatus&&matchCoverage;
      });
      section.querySelector('#decisionStats').innerHTML=`<b>顯示 ${filtered.length}／${records.length} 筆紀錄</b><br><span class="hint">正式研究候選 ${records.filter(x=>x.decisionRecord?.decision==='正式研究候選').length} 筆；缺少關鍵資料的紀錄不會產生投資結論。</span>`;
      rows.innerHTML=filtered.length?filtered.slice(0,30).map(item=>{
        const d=item.decisionRecord||{}, source=d.sources||{}, snapshot=d.snapshot||{}, completeness=d.dataCompleteness||{}, flags=(d.riskFlags||[]).join('、')||'無新增旗標', outcomes=item.outcomes||{};
        const review=[5,20,60].map(day=>{const value=outcomes[String(day)]; return value?.status==='complete'?`${day}日 ${Number(value.returnPct)>=0?'+':''}${value.returnPct}%`:`${day}日待驗證`;}).join('・');
        return `<details class="strategy"><summary><b>${esc(item.name||item.code)}（${esc(item.code)}）</b>・${esc(d.decision||'舊紀錄待補建')}・資料 ${Number(completeness.analysisWeightPct??item.coverage??0)}%</summary><span class="hint">日期 ${esc(item.date)}・策略 ${esc(d.strategyVersion||item.strategyVersion||'1.0')}・${d.reconstructed?'補建紀錄':'原始快照'}<br>快照：價格 ${esc(snapshot.price??item.entryPrice??'資料不足')}／EPS ${esc(snapshot.epsTTM??'資料不足')}／ROE ${esc(snapshot.roeTTM??'資料不足')}／負債比 ${esc(snapshot.debtRatio??'資料不足')}<br>資料來源：${esc(source.market||'資料不足')}；${esc(source.fundamentals||'資料不足')}<br>風險旗標：${esc(flags)}<br>後續檢核：${esc(review)}</span></details>`;
      }).join(''):'<div class="strategy">沒有符合篩選條件的決策紀錄。</div>';
    };
    [codeFilter,statusFilter,coverageFilter].forEach(element=>element.addEventListener('input',render)); render();
  }).catch(()=>{});
})();
