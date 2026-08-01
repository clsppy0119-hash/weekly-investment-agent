(() => {
  const panel = document.createElement('section');
  panel.className = 'card panel watch';
  panel.hidden = true;
  panel.innerHTML = '<h2>每日投資摘要</h2><p class="label">依最新收盤資料、持股與自選股自動更新。</p><div id="dailySummary"></div><h2 style="margin-top:18px">個股研究</h2><p class="label">整合基本面、估值、趨勢與你的持股部位。</p><select id="researchCode" class="input"></select><div id="researchDetail"></div>';
  const watchPanel = document.querySelector('#watchList').closest('section.card');
  watchPanel.after(panel);
  const nav = document.querySelector('.tabs');
  const tab = document.createElement('button');
  tab.type = 'button'; tab.textContent = '個股研究'; nav.append(tab);
  const normalPanels = [document.querySelector('.grid'), document.querySelector('#rows').closest('article.card'), document.querySelector('#transactions').closest('section.card'), watchPanel, document.querySelector('#strategy').closest('aside.card')];
  nav.querySelectorAll('button').forEach(button => { if (button !== tab) button.addEventListener('click', () => { panel.hidden = true; }); });
  tab.addEventListener('click', () => {
    normalPanels.forEach(item => { item.hidden = true; }); panel.hidden = false;
    nav.querySelectorAll('button').forEach(button => button.classList.toggle('active', button === tab));
    renderResearch(); window.scrollTo({top:0, behavior:'smooth'});
  });
  function availableCodes() {
    return [...new Set([...positions().filter(item => item.shares > 0).map(item => item.code), ...state.watchlist])];
  }
  function renderResearch() {
    const select = document.querySelector('#researchCode');
    const current = select.value, codes = availableCodes();
    select.innerHTML = codes.length ? codes.map(code => `<option value="${code}">${q(code)?.name || code}（${code}）</option>`).join('') : '<option value="">尚無標的</option>';
    select.value = codes.includes(current) ? current : (codes[0] || '');
    const code = select.value, quote = q(code), f = fundamentals[code] || {}, holding = positions().find(item => item.code === code && item.shares > 0);
    if (!code) { document.querySelector('#researchDetail').innerHTML = '<div class="strategy">新增持股或自選股後即可研究。</div>'; return; }
    const result = stockScore(code, state.investStyle || 'value');
    const conclusion = result.availableWeight < 50 ? '資料可信度不足，目前只列入觀察，不提供買進或加碼判斷。' : result.score >= 75 ? '股票品質條件相對正向，仍需另看價格與個人部位。' : result.score < 55 ? '股票品質條件偏弱，優先觀察而非急於建立部位。' : '條件中性，等待營收、估值或趨勢出現明確訊號。';
    document.querySelector('#researchDetail').innerHTML = `<div class="strategy"><div class="strategy-head"><b>${quote?.name || code}（${code}）</b><span class="strategy-tag">品質 ${result.score} 分・可信度${result.confidence}</span></div><b>${conclusion}</b><br><span class="hint">有效分析權重 ${result.availableWeight}%・財報期間 ${f.financialPeriod || '尚未取得'}<br>${holding ? `持有 ${holding.shares.toLocaleString()} 股・平均成本 ${money(holding.avg)}・未實現損益 ${holding.unrealized >= 0 ? '+' : ''}${money(holding.unrealized)}` : '尚未持有'}<br>營收年增 ${metric(f.revenueYoY, '%')}・EPS ${metric(f.eps)}・ROE ${metric(f.roe, '%')}・負債比 ${metric(f.debtRatio, '%')}<br>本益比 ${metric(f.pe)}・股價淨值比 ${metric(f.pb)}・殖利率 ${metric(f.dividendYield, '%')}<br>缺少資料不會被當成零分；完整 5／20 日預測請查看「策略提示」。</span></div>`;
  }
  function renderDailySummary() {
    const holdings = positions().filter(item => item.shares > 0);
    const total = holdings.reduce((sum, item) => sum + item.market, 0);
    const largest = holdings.reduce((current, item) => !current || item.market > current.market ? item : current, null);
    const weight = largest && total ? largest.market / total * 100 : 0;
    const risk = !largest ? '目前沒有持股，可先用自選股建立觀察清單。' : weight >= 50 ? `${largest.name} 占投資組合 ${weight.toFixed(1)}%，集中風險偏高。` : `最大部位 ${largest.name} 占 ${weight.toFixed(1)}%，持續留意配置比例。`;
    const watchScores = state.watchlist.map(code => ({code, name:q(code)?.name || code, score:stockScore(code, state.investStyle || 'value').score})).sort((a,b) => b.score - a.score).slice(0,3);
    const focus = watchScores.length ? watchScores.map(item => `${item.name} ${item.score} 分`).join('・') : '尚未加入自選股';
    const unrealized = holdings.reduce((sum, item) => sum + item.unrealized, 0);
    document.querySelector('#dailySummary').innerHTML = `<div class="strategy"><div class="strategy-head"><b>今日投資組合</b><span class="strategy-tag">${updatedAt || '載入中'}</span></div><b>${risk}</b><br><span class="${unrealized >= 0 ? 'positive' : 'negative'}">未實現損益 ${unrealized >= 0 ? '+' : ''}${money(unrealized)}</span><br><span class="hint">自選股優先觀察：${focus}<br>每日留意最新收盤價、成交量、基本面資料與部位集中度。</span></div>`;
  }
  const baseWatchRenderer = renderWatch;
  renderWatch = function() {
    baseWatchRenderer();
    document.querySelectorAll('#watchList .watch-item').forEach((card, index) => {
      const code = state.watchlist[index], f = fundamentals[code] || {}, result = stockScore(code, state.investStyle || 'value');
      card.insertAdjacentHTML('beforeend', `<div class="watch-research hint">營收年增 ${metric(f.revenueYoY, '%')}・綜合 ${result.score} 分<br><button type="button" class="openResearch" data-code="${code}" style="margin-top:6px">查看研究</button></div>`);
    });
    document.querySelectorAll('.openResearch').forEach(button => button.addEventListener('click', () => { tab.click(); document.querySelector('#researchCode').value = button.dataset.code; renderResearch(); }));
    renderDailySummary();
  };
  panel.querySelector('#researchCode').addEventListener('change', renderResearch);
  renderDailySummary(); renderResearch(); renderWatch();
})();
