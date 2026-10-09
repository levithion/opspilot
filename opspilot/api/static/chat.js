const log = document.getElementById('log'), q = document.getElementById('q'), send = document.getElementById('send');
const SUGGESTIONS = ['How do I install the VPN client?', 'Please reset my VPN, it keeps failing', 'What access and licences do I have?', 'Open a ticket: my monitor flickers', 'How much has my team spent on AI this month?'];
let me = null;

function addMsg(kind, text, meta) {
  const m = el('div', { className: 'msg ' + kind }, text);
  if (meta) m.append(meta);
  log.append(m); log.scrollTop = log.scrollHeight; return m;
}
function metaFor(r) {
  const cls = { resolved: 'ok', pending_approval: 'warn', blocked: 'bad', error: 'bad' }[r.status] || '';
  const box = el('div', { className: 'meta' }, el('span', { className: 'chip ' + cls }, r.status));
  if (r.category) box.append(el('span', { className: 'chip' }, r.category + ' · ' + r.priority));
  (r.tool_calls || []).forEach(t => box.append(el('span', { className: 'chip ' + (t.ok ? '' : 'bad') }, '🔧 ' + t.tool)));
  if (r.approvals?.length) box.append(el('span', { className: 'chip warn' }, r.approvals.join(', ')));
  box.append(el('span', { className: 'chip' }, `${Math.round(r.latency_ms)} ms · $${r.cost_usd.toFixed(5)}`));
  return box;
}
async function ask(text) {
  addMsg('user', text); send.disabled = true;
  const pending = addMsg('bot', '…');
  try { const r = await api('/api/chat', { method: 'POST', body: { message: text } }); pending.remove(); addMsg('bot', r.answer, metaFor(r)); }
  catch (e) { pending.remove(); addMsg('bot', 'Error: ' + e.message); }
  send.disabled = false; refresh();
}
document.getElementById('f').addEventListener('submit', e => { e.preventDefault(); const t = q.value.trim(); if (t) { q.value = ''; ask(t); } });
document.getElementById('suggest').append(...SUGGESTIONS.map(s => el('button', { className: 'secondary', type: 'button', onclick: () => ask(s) }, s)));

async function decide(id, approve) {
  try { await api(`/api/approvals/${id}/decision`, { method: 'POST', body: { approve, note: approve ? 'approved via console' : 'rejected via console' } }); } catch (e) { alert(e.message); }
  refresh();
}
async function refresh() {
  me = await api('/api/me');
  const [aps, tks, tools] = await Promise.all([api('/api/approvals'), api('/api/tickets'), api('/api/tools')]);
  const canDecide = me.scopes.includes('approvals:decide');
  document.getElementById('tickets-title').textContent = me.scopes.includes('tickets:read:any') ? 'All tickets' : 'My tickets';
  document.getElementById('approvals').replaceChildren(...(aps.length ? aps.slice(0, 8).map(a => {
    const li = el('li', {}, el('b', {}, a.id), ` ${a.params.system} reset · `, el('span', { className: 'chip ' + (a.status === 'pending' ? 'warn' : a.status === 'executed' ? 'ok' : 'bad') }, a.status));
    if (canDecide && a.status === 'pending' && a.requested_by !== me.user_id) { li.append(el('div', {}, el('button', { onclick: () => decide(a.id, true) }, 'Approve'), ' ', el('button', { className: 'secondary', onclick: () => decide(a.id, false) }, 'Reject'))); }
    return li; }) : [el('li', {}, 'None')]));
  document.getElementById('tickets').replaceChildren(...(tks.length ? tks.slice(0, 8).map(t => el('li', {}, el('b', {}, t.key), ' ', t.title, ' ', el('span', { className: 'chip' }, t.status))) : [el('li', {}, 'None yet')]));
  document.getElementById('tools').replaceChildren(...tools.map(t => el('span', { className: 'chip', title: t.description }, t.name)));
}
setupLogin(document.getElementById('who'), async () => { log.replaceChildren(); addMsg('bot', 'Hi! I can answer IT questions from the knowledge base, open tickets and request approved resets.'); await refresh(); });
