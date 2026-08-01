(() => {
  const config = window.CLOUD_CONFIG || {};
  const enabled = Boolean(config.supabaseUrl && config.supabasePublishableKey && window.supabase);
  const db = enabled ? window.supabase.createClient(config.supabaseUrl, config.supabasePublishableKey) : null;
  const siteUrl = 'https://clsppy0119-hash.github.io/weekly-investment-agent/';
  let user = null, syncTimer = null, skipSync = false, recoveryMode = false;

  document.head.insertAdjacentHTML('beforeend', `<style>
    .auth-box{position:fixed;z-index:50;inset:0;background:#102044aa;display:grid;place-items:center;padding:16px}
    .auth-dialog{width:min(420px,100%);background:#fff;border-radius:14px;padding:18px}
    .auth-actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:10px}
    .auth-button{background:#fff;color:var(--blue);padding:6px 9px;min-height:32px;font-size:12px}
    .auth-link{background:transparent;color:var(--blue);border:0;padding:6px 0;font-size:12px}
  </style>`);
  document.querySelector('.top>div').insertAdjacentHTML('beforeend', `<div class="hint" id="cloudStatus">${enabled ? '雲端同步：尚未登入' : '本機模式：尚未設定雲端'}</div>`);
  const accountButton = document.createElement('button');
  accountButton.className = 'auth-button';
  accountButton.textContent = enabled ? '登入' : '雲端設定';
  document.querySelector('.top').append(accountButton);

  const box = document.createElement('div');
  box.className = 'auth-box'; box.hidden = true;
  box.innerHTML = `<div class="auth-dialog">
    <h2 id="authTitle">帳號與雲端同步</h2>
    <p class="label" id="authDescription">登入同一帳號，即可跨裝置同步交易、自選股與投資方式。</p>
    <input id="authEmail" class="input" type="email" autocomplete="email" placeholder="Email">
    <input id="authPassword" class="input" type="password" minlength="8" autocomplete="current-password" placeholder="密碼（至少8碼）" style="margin-top:8px">
    <div id="authMessage" class="hint"></div>
    <div class="auth-actions">
      <button id="signIn">登入</button><button id="signUp">註冊</button>
      <button id="resetPassword" class="remove">忘記密碼</button>
      <button id="changePassword">更改密碼</button><button id="signOut" class="remove">登出</button>
      <button id="closeAuth" class="remove">關閉</button>
    </div>
  </div>`;
  document.body.append(box);

  const get = selector => document.querySelector(selector);
  const status = get('#cloudStatus'), message = get('#authMessage');
  const localPortfolio = () => JSON.parse(localStorage.getItem('tw-stock-dashboard-v3') || '{"transactions":[],"watchlist":[],"investStyle":"value"}');
  const originalSetItem = localStorage.setItem.bind(localStorage);

  function showMessage(text, isError = false) { message.textContent = text; message.style.color = isError ? '#b42318' : ''; }
  function openAccount() { box.hidden = false; renderAuth(); }
  accountButton.onclick = openAccount;
  get('#closeAuth').onclick = () => { box.hidden = true; recoveryMode = false; };

  function renderAuth() {
    const loggedIn = Boolean(user);
    get('#signIn').hidden = get('#signUp').hidden = loggedIn || !enabled || recoveryMode;
    get('#resetPassword').hidden = loggedIn || !enabled || recoveryMode;
    get('#signOut').hidden = !loggedIn || recoveryMode;
    get('#changePassword').hidden = (!loggedIn && !recoveryMode) || !enabled;
    get('#authEmail').hidden = recoveryMode;
    get('#authTitle').textContent = recoveryMode ? '設定新密碼' : '帳號與雲端同步';
    get('#authDescription').textContent = recoveryMode ? '請輸入至少 8 碼的新密碼。' : (loggedIn ? '已登入，資料變更會自動同步。' : '請登入或註冊；忘記密碼可寄送重設信。');
    get('#authPassword').placeholder = recoveryMode || loggedIn ? '新密碼（至少8碼）' : '密碼（至少8碼）';
    get('#authPassword').autocomplete = recoveryMode || loggedIn ? 'new-password' : 'current-password';
  }

  async function pushPortfolio() {
    if (!db || !user || skipSync) return;
    const { error } = await db.from('user_portfolios').upsert({ user_id: user.id, portfolio: localPortfolio(), updated_at: new Date().toISOString() });
    status.textContent = error ? '同步失敗，資料仍在本機' : '已同步 ' + new Date().toLocaleTimeString('zh-TW');
  }
  localStorage.setItem = (key, value) => {
    originalSetItem(key, value);
    if (key === 'tw-stock-dashboard-v3') { clearTimeout(syncTimer); syncTimer = setTimeout(pushPortfolio, 800); }
  };
  async function loadPortfolio() {
    const { data } = await db.from('user_portfolios').select('portfolio').eq('user_id', user.id).maybeSingle();
    if (data?.portfolio && (data.portfolio.transactions?.length || data.portfolio.watchlist?.length)) {
      if (confirm('載入此帳號的雲端資料並取代本機資料嗎？')) {
        skipSync = true; originalSetItem('tw-stock-dashboard-v3', JSON.stringify(data.portfolio)); location.reload(); return;
      }
    }
    await pushPortfolio();
  }
  async function setUser(nextUser) {
    const changed = user?.id !== nextUser?.id;
    user = nextUser;
    status.textContent = user ? `已登入：${user.email}` : '雲端同步：尚未登入';
    accountButton.textContent = user ? '帳號' : '登入';
    renderAuth();
    if (user && changed && !recoveryMode) await loadPortfolio();
  }

  get('#signIn').onclick = async () => {
    const { error } = await db.auth.signInWithPassword({ email: get('#authEmail').value.trim(), password: get('#authPassword').value });
    showMessage(error ? error.message : '登入成功', Boolean(error));
  };
  get('#signUp').onclick = async () => {
    const { error } = await db.auth.signUp({
      email: get('#authEmail').value.trim(), password: get('#authPassword').value,
      options: { emailRedirectTo: `${siteUrl}?auth=confirmed` }
    });
    showMessage(error ? error.message : '註冊信已寄出，請至 Email 完成驗證。', Boolean(error));
  };
  get('#resetPassword').onclick = async () => {
    const email = get('#authEmail').value.trim();
    if (!email) return showMessage('請先輸入 Email。', true);
    const { error } = await db.auth.resetPasswordForEmail(email, { redirectTo: `${siteUrl}?auth=recovery` });
    showMessage(error ? error.message : '密碼重設信已寄出，請查看 Email。', Boolean(error));
  };
  get('#changePassword').onclick = async () => {
    const password = get('#authPassword').value;
    if (password.length < 8) return showMessage('新密碼至少需要 8 碼。', true);
    const { error } = await db.auth.updateUser({ password });
    showMessage(error ? error.message : '密碼已更新，請妥善保管。', Boolean(error));
    if (!error) { recoveryMode = false; history.replaceState({}, '', siteUrl); renderAuth(); }
  };
  get('#signOut').onclick = () => db.auth.signOut();

  if (db) {
    db.auth.getUser().then(({ data }) => setUser(data.user));
    db.auth.onAuthStateChange((event, session) => {
      if (event === 'PASSWORD_RECOVERY') { recoveryMode = true; box.hidden = false; showMessage('驗證成功，請設定新密碼。'); }
      setUser(session?.user || null);
    });
  }
  renderAuth();
})();
