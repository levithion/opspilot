// Shared helpers: dev login (token kept in sessionStorage) and a small fetch wrapper.
const Auth = {
  get token() { try { return sessionStorage.getItem('opspilot_token'); } catch { return null; } },
  set token(v) { try { v ? sessionStorage.setItem('opspilot_token', v) : sessionStorage.removeItem('opspilot_token'); } catch {} },
};
async function api(path, opts = {}) {
  const headers = { 'Content-Type': 'application/json', ...(opts.headers || {}) };
  if (Auth.token) headers.Authorization = 'Bearer ' + Auth.token;
  const res = await fetch(path, { ...opts, headers, body: opts.body ? JSON.stringify(opts.body) : undefined });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) { const e = new Error(typeof data.detail === 'string' ? data.detail : (data.detail?.message || res.statusText)); e.status = res.status; throw e; }
  return data;
}
async function setupLogin(select, onLogin) {
  const cfg = await api('/auth/config');
  if (!cfg.dev_login) { select.replaceWith(Object.assign(document.createElement('span'), { textContent: 'Sign in with Microsoft Entra ID (OIDC mode): send a bearer token' })); return; }
  const users = await api('/auth/dev-users');
  select.replaceChildren(...users.map(u => Object.assign(document.createElement('option'), { value: u.email, textContent: `${u.name} · ${u.team} · ${u.role}` })));
  const login = async () => { const t = await api('/auth/dev-token', { method: 'POST', body: { email: select.value } }); Auth.token = t.access_token; try { sessionStorage.setItem('opspilot_user', select.value); } catch {} await onLogin(); };
  select.addEventListener('change', login);
  try { const saved = sessionStorage.getItem('opspilot_user'); if (saved) select.value = saved; } catch {}
  await login();
}
const el = (tag, props = {}, ...kids) => { const n = Object.assign(document.createElement(tag), props); n.append(...kids); return n; };
