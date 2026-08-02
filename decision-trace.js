(() => {
  const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));
  const start = () => fetch('strategy_data/recommendations.json', { cache: 'no-store' })
    .then(response => response.ok ? response.json() : null)
    .then(data => {
      if (!data) return;
      document.querySelector('#decisionTraceability')?.remove();
      const records = [...(data.recommendations || [])].sort((a, b) => String(b.date || '').localeCompare(String(a.date || '')));
      const section = document.createElement('section');
      section.id = 'decisionTraceability';
      section.className = 'card panel';
      section.innerHTML = `<h2>決策可追溯紀錄</h2><p class="label">每筆保留資料快照、來源、風險旗標與 5／20／60 日檢核；研究候選不是買進指令。</p><div class="form" style="grid-template-columns:1fr 1fr 1fr"><input id="decisionCodeFilter" class="input" inputmode="numeric" placeholder="股票代碼或名稱"><select id="decisionStatusFilter" class="input"><option value="">全部結論</option><option>正式研究候選</option><option>僅追蹤</option><option>暫不決策</option></select><select id="decisionCoverageFilter" class="input"><option value="0">全部完整度</option><option value="80">80% 以上</option><option value="70">70% 以上</option><option value="1">不足 70%</option></select></div><div id="decisionStats" class="strategy" style="margin-top:10px"></div><div id="decisionRows"></div>`;
      const app = document.querySelector('main.app');
      if (!app) return;
      app.insertBefore(section, app.querySelector('.footer'));
      const codeFilter = section.querySelector('#decisionCodeFilter');
      const statusFilter = section.querySelector('#decisionStatusFilter');
      const coverageFilter = section.querySelector('#decisionCoverageFilter');
      const rows = section.querySelector('#decisionRows');
      const render = () => {
        const query = codeFilter.value.trim().toLowerCase();
        const status = statusFilter.value;
        const threshold = Number(coverageFilter.value);
        const filtered = records.filter(item => {
          const decision = item.decisionRecord?.decision || '舊紀錄待補建';
          const coverage = Number(item.decisionRecord?.dataCompleteness?.analysisWeightPct ?? item.coverage ?? 0);
          const matchText = !query || String(item.code || '').includes(query) || String(item.name || '').toLowerCase().includes(query);
          const matchStatus = !status || decision === status;
          const matchCoverage = threshold === 0 || (threshold === 1 ? coverage < 70 : coverage >= threshold);
          return matchText && matchStatus && matchCoverage;
        });
        section.querySelector('#decisionStats').innerHTML = `<b>顯示 ${filtered.length}／${records.length} 筆紀錄</b><br><span class="hint">正式研究候選 ${records.filter(item => item.decisionRecord?.decision === '正式研究候選').length} 筆；缺少關鍵資料的紀錄不會產生投資結論。</span>`;
        rows.innerHTML = filtered.length ? filtered.slice(0, 30).map(item => {
          const decision = item.decisionRecord || {};
          const source = decision.sources || {};
          const snapshot = decision.snapshot || {};
          const completeness = decision.dataCompleteness || {};
          const flags = (decision.riskFlags || []).join('、') || '無新增旗標';
          const outcomes = item.outcomes || {};
          const review = [5, 20, 60].map(day => {
            const value = outcomes[String(day)];
            return value?.status === 'complete' ? `${day}日 ${Number(value.returnPct) >= 0 ? '+' : ''}${value.returnPct}%` : `${day}日待驗證`;
          }).join('・');
          return `<details class="strategy"><summary><b>${esc(item.name || item.code)}（${esc(item.code)}）</b>・${esc(decision.decision || '舊紀錄待補建')}・資料 ${Number(completeness.analysisWeightPct ?? item.coverage ?? 0)}%</summary><span class="hint">日期 ${esc(item.date)}・策略 ${esc(decision.strategyVersion || item.strategyVersion || '1.0')}・${decision.reconstructed ? '補建紀錄' : '原始快照'}<br>快照：價格 ${esc(snapshot.price ?? item.entryPrice ?? '資料不足')}／EPS ${esc(snapshot.epsTTM ?? '資料不足')}／ROE ${esc(snapshot.roeTTM ?? '資料不足')}／負債比 ${esc(snapshot.debtRatio ?? '資料不足')}<br>資料來源：${esc(source.market || '資料不足')}；${esc(source.fundamentals || '資料不足')}<br>風險旗標：${esc(flags)}<br>後續檢核：${esc(review)}</span></details>`;
        }).join('') : '<div class="strategy">沒有符合篩選條件的決策紀錄。</div>';
      };
      [codeFilter, statusFilter, coverageFilter].forEach(element => element.addEventListener('input', render));
      render();
    })
    .catch(() => {});
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start, { once: true });
  else start();
})();
