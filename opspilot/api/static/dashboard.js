const usd = n => '$' + Number(n || 0).toFixed(4);
function table(id, head, rows) {
  const t = document.getElementById(id);
  t.replaceChildren(el('tr', {}, ...head.map(h => el('th', {}, h))), ...rows.map(r => el('tr', {}, ...r.map(c => el('td', {}, c instanceof Node ? c : String(c))))));
}
function bar(ratio) { const r = Math.min(ratio || 0, 1); return el('div', { className: 'bar', title: Math.round((ratio || 0) * 100) + '%' }, el('i', { className: ratio >= 1 ? 'bad' : ratio >= 0.8 ? 'warn' : '', style: `width:${r * 100}%` })); }
async function load() {
  const denied = document.getElementById('denied'), content = document.getElementById('content');
  let m;
  try { m = await api('/api/admin/metrics'); } catch (e) { denied.style.display = 'block'; content.style.display = 'none'; return; }
  denied.style.display = 'none'; content.style.display = 'block';
  const s = m.summary;
  const cards = [['Requests', s.total_requests], ['Resolved by agent', s.resolved], ['Pending approval', s.pending_approval], ['p95 latency', s.latency_p95_ms + ' ms'],
    ['Error rate', (s.error_rate * 100).toFixed(1) + '%'], ['Blocked by guardrails', s.guardrail_block_pct + '%'], ['Cost / resolved request', usd(s.cost_per_resolved_request_usd)], ['Total AI cost', usd(s.total_cost_usd)]];
  document.getElementById('cards').replaceChildren(...cards.map(([k, v]) => el('div', { className: 'card' }, el('b', {}, String(v)), el('span', {}, k))));
  table('teams', ['Team', 'Calls', 'Cost', 'Budget', ''], m.spend_by_team.map(t => [t.team, t.calls, usd(t.cost_usd), '$' + t.budget_usd, bar(t.ratio)]));
  table('shadow', ['Client', 'Users', 'Calls', 'Cost', 'Flag'], m.shadow_ai.map(c => [c.client_id, c.users, c.calls, usd(c.cost_usd), c.flag || '✓ registered']));
  table('daily', ['Day', 'Requests', 'Errors', 'Blocked', 'Cost'], m.daily.map(d => [d.day, d.requests, d.errors, d.blocked, usd(d.cost_usd)]));
  table('spans', ['Span', 'Count', 'Avg ms', 'Max ms', 'Errors'], m.spans.map(x => [x.name, x.n, x.avg_ms, x.max_ms, x.errors]));
  table('models', ['Provider', 'Model', 'Calls', 'Tokens', 'Cost'], m.spend_by_model.map(x => [x.provider, x.model, x.calls, x.tokens, usd(x.cost_usd)]));
  const v = await api('/api/admin/audit/verify'); const rows = await api('/api/admin/audit?limit=8');
  document.getElementById('audit').textContent = v.intact ? '✓ Hash chain intact' : '✗ Chain broken at #' + v.first_broken_seq;
  table('auditrows', ['#', 'Actor', 'Client', 'Action', 'Outcome', 'Approved by'], rows.map(r => [r.seq, r.actor, r.client_id || '', r.action, r.outcome, r.approved_by || '']));
}
document.getElementById('reload').addEventListener('click', load);
setupLogin(document.getElementById('who'), load);
