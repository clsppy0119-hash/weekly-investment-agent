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
})();
