(() => {
  const el = (tag, attrs = {}, html = '') => { const node = document.createElement(tag); Object.assign(node, attrs); node.innerHTML = html; return node; };
  const number = value => Number.isFinite(Number(value)) ? Number(value) : 0;
  const dateValue = value => { const date = new Date(value); return Number.isNaN(date) ? new Date() : date; };
  const fmt = value => new Intl.NumberFormat('zh-TW', { maximumFractionDigits: 0 }).format(value || 0);
  const percent = value => Number.isFinite(value) ? `${value >= 0 ? '+' : ''}${value.toFixed(2)}%` : '資料不足';
  const taxRate = code => /^00\d{2,4}$/.test(String(code)) ? 0.001 : 0.003;
  state.dividends ||= [];

  const panel = el('section', { className: 'card panel watch', id: 'portfolioTools' }, `
    <h2>投資績效與資料保全</h2>
    <p class="label">績效以實際買賣現金流、目前市值與已登錄股利計算；基準採 0050 可用歷史收盤價。</p>
    <div class="grid" id="performanceCards"></div>
    <div class="form" style="margin-top:12px"><button id="exportCsv">匯出 CSV</button><button id="importCsv">匯入 CSV</button><button id="exportBackup">完整備份</button><button id="importBackup">還原備份</button><input id="portfolioFile" type="file" accept=".csv,.json" hidden></div>
    <h2 style="margin-top:18px">股利／除權息紀錄</h2>
    <p class="label">登錄實際配發資訊；現金股利會計入績效。股票股利以「每股配發股數」記錄，方便追蹤除權息與填權息。</p>
    <div class="form"><input id="divCode" class="input" inputmode="numeric" maxlength="6" placeholder="股票代碼"><input id="divDate" class="input" type="date"><input id="cashDividend" class="input" type="number" min="0" step="0.001" placeholder="現金股利／股"><input id="stockDividend" class="input" type="number" min="0" step="0.001" placeholder="股票股利／股"><button id="addDividend">新增股利</button></div>
    <div class="table-wrap"><table class="table"><thead><tr><th>日期</th><th>股票</th><th>現金股利</th><th>股票股利</th><th>填權息</th><th></th></tr></thead><tbody id="dividendRows"></tbody></table></div>
    <p class="footer">0050 比較僅涵蓋目前可取得的歷史區間；若持有期間早於基準資料，會明確標示資料不足。所有數據僅供研究參考。</p>`);
  document.querySelector('.footer')?.before(panel);

  function sharesOn(code, date) {
    return state.transactions.filter(t => t.code === code && dateValue(t.date) <= date).reduce((sum, t) => sum + (t.side === 'sell' ? -number(t.shares) : number(t.shares)), 0);
  }
  function cashFlow() {
    const flows = state.transactions.map(t => {
      const gross = number(t.price) * number(t.shares), fee = t.fee == null ? Math.max(20, gross * 0.001425) : number(t.fee);
      const value = t.side === 'sell' ? gross - fee - gross * taxRate(t.code) : -gross - fee;
      return { date: dateValue(t.date), value };
    });
    state.dividends.forEach(d => { const shares = sharesOn(d.code, dateValue(d.date)); if (shares > 0) flows.push({ date: dateValue(d.date), value: shares * number(d.cashPerShare) }); });
    const market = positions().filter(p => p.shares > 0).reduce((sum, p) => sum + number(p.market), 0);
    if (market) flows.push({ date: new Date(), value: market });
    return flows.sort((a, b) => a.date - b.date);
  }
  function xirr(flows) {
    if (flows.length < 2 || !flows.some(f => f.value < 0) || !flows.some(f => f.value > 0)) return null;
    const start = flows[0].date, years = f => (f.date - start) / 86400000 / 365;
    let rate = 0.1;
    for (let i = 0; i < 80; i++) {
      const base = 1 + rate;
      if (base <= 0) return null;
      const f = flows.reduce((sum, item) => sum + item.value / base ** years(item), 0);
      const d = flows.reduce((sum, item) => sum - years(item) * item.value / base ** (years(item) + 1), 0);
      if (!Number.isFinite(d) || Math.abs(d) < 1e-9) return null;
      const next = rate - f / d;
      if (!Number.isFinite(next) || next < -0.9999 || next > 1000) return null;
      if (Math.abs(next - rate) < 1e-7) return next;
      rate = next;
    }
    return null;
  }
  function benchmark(flows) {
    const history = (window.__quoteHistory || {})['0050'] || [];
    if (!flows.length || history.length < 2) return null;
    const start = flows[0].date;
    const first = history.find(p => dateValue(p.date) >= start) || history[0], last = history.at(-1);
    if (!first?.close || !last?.close || first.close === last.close) return null;
    const invested = -flows.filter(f => f.value < 0).reduce((sum, f) => sum + f.value, 0);
    return { returnRate: (last.close / first.close - 1) * 100, value: invested * last.close / first.close, start: first.date };
  }
  function render() {
    const flows = cashFlow(), paid = -flows.filter(f => f.value < 0).reduce((sum, f) => sum + f.value, 0);
    const market = positions().filter(p => p.shares > 0).reduce((sum, p) => sum + number(p.market), 0);
    const dividends = flows.filter(f => f.value > 0 && f.date.toDateString() !== new Date().toDateString()).reduce((sum, f) => sum + f.value, 0);
    const x = xirr(flows), simple = paid ? ((market + dividends) / paid - 1) * 100 : null, base = benchmark(flows);
    document.querySelector('#performanceCards').innerHTML = [
      ['投入本金', `NT$ ${fmt(paid)}`], ['目前市值', `NT$ ${fmt(market)}`], ['累積報酬率', percent(simple)], ['年化 XIRR', percent(x == null ? null : x * 100)], ['已登錄現金股利', `NT$ ${fmt(dividends)}`], ['0050 基準', base ? `${percent(base.returnRate)}（${base.start} 起）` : '歷史資料不足']
    ].map(([label, value]) => `<article class="card metric"><div class="label">${label}</div><div class="value" style="font-size:20px">${value}</div></article>`).join('');
    document.querySelector('#dividendRows').innerHTML = state.dividends.length ? state.dividends.slice().sort((a,b)=>String(b.date).localeCompare(String(a.date))).map(d => `<tr><td>${d.date}</td><td>${q(d.code)?.name || d.code}（${d.code}）</td><td>${number(d.cashPerShare).toFixed(3)}</td><td>${number(d.stockPerShare).toFixed(3)}</td><td>待資料串接</td><td><button class="removeDividend" data-id="${d.id}">刪除</button></td></tr>`).join('') : '<tr><td colspan="6" class="hint">尚未登錄股利資料</td></tr>';
    document.querySelectorAll('.removeDividend').forEach(button => button.onclick = () => { state.dividends = state.dividends.filter(d => d.id !== button.dataset.id); save(); render(); });
  }
  function download(filename, content, mime) { const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob([content], { type: mime })); a.download = filename; a.click(); URL.revokeObjectURL(a.href); }
  document.querySelector('#addDividend').onclick = () => {
    const code = document.querySelector('#divCode').value.trim(), date = document.querySelector('#divDate').value;
    if (!q(code) || !date) return alert('請輸入可辨識股票代碼與除權息日期。');
    state.dividends.push({ id: crypto.randomUUID(), code, date, cashPerShare: number(document.querySelector('#cashDividend').value), stockPerShare: number(document.querySelector('#stockDividend').value) });
    save(); ['#divCode','#cashDividend','#stockDividend'].forEach(s => document.querySelector(s).value = ''); render();
  };
  document.querySelector('#exportCsv').onclick = () => { const rows = [['類型','日期','股票代碼','買賣','股數','成交價','手續費','現金股利每股','股票股利每股'], ...state.transactions.map(t => ['交易',t.date,t.code,t.side,t.shares,t.price,t.fee ?? '', '', '']), ...state.dividends.map(d => ['股利',d.date,d.code,'','', '', '',d.cashPerShare,d.stockPerShare])]; download('台股投資紀錄.csv', '\ufeff' + rows.map(r=>r.map(v=>`"${String(v).replaceAll('"','""')}"`).join(',')).join('\n'), 'text/csv;charset=utf-8'); };
  document.querySelector('#exportBackup').onclick = () => download('台股投資完整備份.json', JSON.stringify(state, null, 2), 'application/json');
  document.querySelector('#importCsv').onclick = () => { document.querySelector('#portfolioFile').accept = '.csv'; document.querySelector('#portfolioFile').dataset.mode = 'csv'; document.querySelector('#portfolioFile').click(); };
  document.querySelector('#importBackup').onclick = () => { document.querySelector('#portfolioFile').accept = '.json'; document.querySelector('#portfolioFile').dataset.mode = 'json'; document.querySelector('#portfolioFile').click(); };
  document.querySelector('#portfolioFile').onchange = async event => { const file = event.target.files[0]; if (!file) return; try { const text = await file.text(); if (event.target.dataset.mode === 'json') { const incoming = JSON.parse(text); if (!Array.isArray(incoming.transactions)) throw Error(); Object.assign(state, incoming); } else { const lines = text.replace(/^\ufeff/, '').trim().split(/\r?\n/).slice(1); lines.forEach(line => { const c = [...line.matchAll(/(?:^|,)(?:"((?:[^"]|"")*)"|([^,]*))/g)].map(m => (m[1] ?? m[2]).replaceAll('""','"')); if (c[0] === '交易' && c[2] && c[4]) state.transactions.push({id:crypto.randomUUID(),date:c[1],code:c[2],side:c[3]||'buy',shares:number(c[4]),price:number(c[5]),fee:c[6]===''?null:number(c[6])}); if (c[0] === '股利' && c[2]) state.dividends.push({id:crypto.randomUUID(),date:c[1],code:c[2],cashPerShare:number(c[7]),stockPerShare:number(c[8])}); }); } save(); render(); location.reload(); } catch { alert('檔案格式無法辨識，未進行匯入。'); } event.target.value = ''; };
  const originalLoad = window.load;
  fetch('quotes.json',{cache:'no-store'}).then(r=>r.json()).then(data => { window.__quoteHistory = data.history || {}; render(); }).catch(render);
  render();
})();
