// MarshGuard command centre — all state comes from the API; live updates over Server-Sent Events.
const $ = s => document.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
let S = null;                 // latest server state
let selected = 'e1';
let lastReportId = null;   // newest report id already shown

async function api(path, body) {
  const r = await fetch(path, { method: body === undefined ? 'GET' : 'POST', headers: { 'Content-Type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body) });
  if (!r.ok) { const t = await r.json().catch(() => ({})); throw new Error(t.detail || r.statusText); }
  return r.json();
}
function toast(msg) { const t = $('#toast'); t.textContent = msg; t.hidden = false; clearTimeout(toast.h); toast.h = setTimeout(() => t.hidden = true, 3500); }
async function act(path, body, okMsg) {
  try { const s = await api(path, body); apply(s); if (okMsg) toast(okMsg); } catch (e) { toast(e.message); }
}

const statusColor = s => s === 'over' ? 'var(--crit)' : s === 'watch' ? 'var(--warn)' : 'var(--water)';
const statusPill = r => r.status === 'over' ? '<span class="pill p-over">OVERLOADED</span>' : r.status === 'watch' ? '<span class="pill p-watch">NEAR LIMIT</span>' : '<span class="pill p-ok">OK</span>';

function renderMap() {
  const N = S.nodes, healthy = S.scenario.marshHealthy;
  const bands = [[0, 112, 'Street drains · GCC'], [112, 224, 'Feeder canals · WRD'], [224, 318, 'Core marsh · Forest Dept'], [318, 440, 'Outfall · WRD']];
  let h = bands.map((b, i) => `<rect x="0" y="${b[0]}" width="640" height="${b[1] - b[0]}" fill="${i % 2 ? 'var(--surface)' : 'var(--band)'}"/>`).join('');
  const m = N.marsh;
  h += `<ellipse cx="${m.x}" cy="${m.y}" rx="${healthy ? 95 : 62}" ry="${healthy ? 30 : 22}" fill="var(--marsh-soft)" stroke="var(--marsh)" stroke-width="1.5"/>`;
  S.channels.forEach(e => {
    const a = N[e.from], b = N[e.to], w = Math.max(3, Math.min(12, 2 + e.cap_free / 2.2));
    if (e.root) h += `<line class="halo" x1="${a.x}" y1="${a.y}" x2="${b.x}" y2="${b.y}" stroke="var(--crit)" stroke-width="${w + 14}" stroke-linecap="round"/>`;
    if (selected === e.id) h += `<line x1="${a.x}" y1="${a.y}" x2="${b.x}" y2="${b.y}" stroke="var(--accent)" stroke-width="${w + 7}" stroke-linecap="round" stroke-opacity=".45"/>`;
    h += `<line x1="${a.x}" y1="${a.y}" x2="${b.x}" y2="${b.y}" stroke="${statusColor(e.status)}" stroke-width="${w}" stroke-linecap="round" ${e.symptom ? 'stroke-dasharray="7 5"' : ''}/>`;
  });
  h += bands.map((b, i) => { const t = b[2].toUpperCase(), w = t.length * 6.6 + 10; return `<rect x="4" y="${b[0] + 4}" width="${w}" height="16" rx="2" fill="${i % 2 ? 'var(--surface)' : 'var(--band)'}" fill-opacity=".92"/><text x="9" y="${b[0] + 15}" font-family="var(--body)" font-size="10" font-weight="700" letter-spacing=".08em" fill="var(--muted)">${t}</text>`; }).join('');
  for (const id in N) {
    const n = N[id];
    if (n.kind === 'marsh') { h += `<text x="${n.x - (healthy ? 102 : 69)}" y="${n.y + 4}" text-anchor="end" font-family="var(--body)" font-size="12" font-weight="700" fill="var(--marsh)">${n.label}</text>`; continue; }
    const r = n.kind === 'catch' ? 13 : n.kind === 'out' ? 11 : 8;
    h += `<circle cx="${n.x}" cy="${n.y}" r="${r}" fill="${n.kind === 'out' ? 'var(--water)' : 'var(--surface)'}" stroke="var(--fg)" stroke-width="1.5"/>`;
    let ty = n.kind === 'catch' ? n.y - 19 : n.y + 22, anchor = 'middle', tx = n.x;
    if (id === 'okk') { anchor = 'end'; tx = n.x - 14; ty = n.y + 4; }
    if (id === 'sea') ty = n.y + 26;
    h += `<text x="${tx}" y="${ty}" text-anchor="${anchor}" font-family="var(--body)" font-size="11.5" font-weight="600" fill="var(--fg)">${n.label}</text>`;
    if (n.kind === 'catch') h += `<text x="${n.x}" y="${n.y + 4}" text-anchor="middle" font-family="var(--mono)" font-size="9" fill="var(--muted)">${n.area}ha</text>`;
  }
  S.channels.forEach(e => {
    const a = N[e.from], b = N[e.to], mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2;
    const txt = Math.round(e.util * 100) + '%', bw = txt.length * 7 + 10;
    h += `<rect x="${mx - bw / 2}" y="${my - 9}" width="${bw}" height="18" rx="3" fill="var(--surface)" stroke="${statusColor(e.status)}"/><text x="${mx}" y="${my + 4}" text-anchor="middle" font-family="var(--mono)" font-size="11" font-weight="600" fill="${statusColor(e.status)}">${txt}</text>`;
    if (e.blockage !== 'none') h += `<g transform="translate(${mx + bw / 2 + 11},${my})"><circle r="9" fill="var(--warn)"/><text y="4" text-anchor="middle" font-family="var(--mono)" font-size="10" font-weight="700" fill="var(--surface)">${e.blockage[0].toUpperCase()}</text></g>`;
    h += `<line class="hit" data-e="${e.id}" x1="${a.x}" y1="${a.y}" x2="${b.x}" y2="${b.y}" stroke="transparent" stroke-width="26" style="cursor:pointer" tabindex="0" role="button" aria-label="${esc(e.name)}"/>`;
  });
  $('#map').innerHTML = h;
  $('#map').querySelectorAll('.hit').forEach(el => {
    const pick = () => { selected = el.dataset.e; render(); };
    el.addEventListener('click', pick);
    el.addEventListener('keydown', ev => { if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); pick(); } });
  });
}

function renderDetail() {
  const e = S.channels.find(x => x.id === selected);
  let verdict;
  if (e.root) verdict = `<div class="verdict root"><b>Root cause.</b> This channel would overflow even with free flow downstream. Fixing it here reduces flooding.</div>`;
  else if (e.symptom) verdict = `<div class="verdict back"><b>Symptom, not cause.</b> Water is backing up from the overloaded channel downstream. Clearing this channel alone will not help.</div>`;
  else if (e.status === 'watch') verdict = `<div class="verdict back"><b>Near limit.</b> Flow is above 80% of capacity. Watch if rainfall rises.</div>`;
  else verdict = `<div class="verdict fine"><b>Within capacity.</b> No action needed at this rainfall.</div>`;
  const opts = Object.entries(S.blockTypes).map(([k, v]) => `<option value="${k}" ${k === e.blockage ? 'selected' : ''}>${v}</option>`).join('');
  $('#detail').innerHTML = `
    <h2>Selected channel</h2>
    <div class="row" style="margin-bottom:8px"><div><b style="font-size:15px">${esc(e.name)}</b><div class="muted" style="font-size:12px">${e.tier} · ${e.agency}</div></div>${statusPill(e)}</div>
    <dl class="kv">
      <dt>Flow arriving</dt><dd>${e.demand.toFixed(2)} m³/s</dd>
      <dt>Effective capacity</dt><dd>${e.cap.toFixed(2)} m³/s</dd>
      <dt>Utilisation</dt><dd>${Math.round(e.util * 100)}%</dd>
      <dt>Section b × d</dt><dd>${e.b} × ${e.d} m</dd>
      <dt>Manning n</dt><dd>${e.n_eff.toFixed(3)}${e.blockage !== 'none' ? ' (raised)' : ''}</dd>
      <dt>Bed slope S</dt><dd>${e.S}</dd>
      ${e.backwater ? '<dt>Backwater</dt><dd>capacity reduced</dd>' : ''}
      ${e.citizen_tickets.length ? `<dt>Citizen tickets</dt><dd>${e.citizen_tickets.join(', ')}</dd>` : ''}
    </dl>
    <div style="margin:10px 0">${verdict}</div>
    <div class="row"><label for="blk">Reported blockage</label><select id="blk">${opts}</select></div>`;
  $('#blk').addEventListener('change', ev => act(`/api/channels/${e.id}/blockage`, { blockage: ev.target.value }));
}

function renderActions() {
  const t = S.actions;
  if (!t.length) { $('#tlist').innerHTML = '<div class="empty">No actions. Every channel is within capacity and no blockages are reported.</div>'; return; }
  let h = `<div class="trow head"><div>Ref</div><div>Pri.</div><div>Channel</div><div>Routed to</div><div>Cause and prescribed action</div><div></div></div>`;
  t.forEach(x => {
    const pc = x.priority === 'P1' ? 'p-over' : x.priority === 'P2' ? 'p-watch' : 'p-ok';
    const ref = x.citizen_tickets.length ? x.citizen_tickets.join('<br>') : '<span class="muted">model</span>';
    h += `<div class="trow"><div class="id">${ref}</div><div><span class="pill ${pc}">${x.priority}</span></div>
      <div>${esc(x.name)}<small>${Math.round(x.util * 100)}% of capacity</small></div>
      <div class="c4">${x.agency}</div>
      <div class="c5">${esc(x.cause)}<small>${esc(x.action)}</small></div>
      <div class="c6">${x.clearable ? `<button class="btn" data-clear="${x.channel}">Mark cleared</button>` : '<span class="muted" style="font-size:12px">Dispatch</span>'}</div></div>`;
  });
  $('#tlist').innerHTML = h;
  $('#tlist').querySelectorAll('[data-clear]').forEach(b => b.addEventListener('click', () => act(`/api/channels/${b.dataset.clear}/clear`, {}, 'Channel cleared. Reporters notified.')));
}

const RSTATUS = { verified: ['p-ok', 'VERIFIED'], review: ['p-watch', 'NEEDS REVIEW'], rejected: ['p-over', 'REJECTED'], low_confidence: ['p-over', 'LOW CONF.'] };
function renderReports() {
  const R = S.reports, ch = Object.fromEntries(S.channels.map(c => [c.id, c.name]));
  if (!R.length) { $('#rlist').innerHTML = '<div class="empty">No citizen reports yet. Open the WhatsApp simulator, or message the connected WhatsApp number.</div>'; return; }
  let h = `<div class="rrow head"><div>Ref</div><div>Status</div><div>Channel</div><div>Classifier</div><div>Source</div></div>`;
  R.forEach(r => {
    const [pc, lbl] = RSTATUS[r.status] || ['p-ok', r.status];
    const time = new Date(r.created * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    let cls = r.cls ? `${esc(S.blockTypes[r.cls] || r.cls)}<small>${r.confidence != null ? 'confidence ' + r.confidence.toFixed(2) : 'verified by engineer'}</small>` : '<span class="muted">no model, manual check</span>';
    if (r.status === 'review') {
      const opts = Object.entries(S.blockTypes).filter(([k]) => k !== 'none').map(([k, v]) => `<option value="${k}">${v}</option>`).join('');
      cls = `<div class="rev"><select id="rv-${r.id}" aria-label="Blockage type">${opts}</select><button class="btn primary" data-ok="${r.id}">Verify</button><button class="btn" data-no="${r.id}">Reject</button></div>`;
    }
    const photo = r.has_photo ? `<small><a href="/api/reports/${r.id}/photo" target="_blank" rel="noopener">View photo</a></small>` : '';
    h += `<div class="rrow"><div class="id">R-${r.id}</div><div><span class="pill ${pc}">${lbl}</span></div><div>${esc(ch[r.channel] || 'Location pending')}${photo}</div><div>${cls}</div><div>${esc(r.reporter)}<small>${r.source === 'whatsapp' ? 'WhatsApp · ' : ''}${time}</small></div></div>`;
  });
  $('#rlist').innerHTML = h;
  $('#rlist').querySelectorAll('[data-ok]').forEach(b => b.addEventListener('click', () => act(`/api/reports/${b.dataset.ok}/review`, { cls: $('#rv-' + b.dataset.ok).value }, 'Report verified and ticket raised.')));
  $('#rlist').querySelectorAll('[data-no]').forEach(b => b.addEventListener('click', () => act(`/api/reports/${b.dataset.no}/review`, { cls: null }, 'Report rejected.')));
}

function renderWhatsApp() {
  const w = S.integrations.whatsappStatus || {};
  const ago = t => { if (!t) return 'never'; const m = Math.round((Date.now() / 1000 - t) / 60); return m < 1 ? 'just now' : m < 60 ? m + ' min ago' : new Date(t * 1000).toLocaleString(); };
  const yes = (ok, good, bad) => `<span class="${ok ? 'good' : 'bad'}">${ok ? good : bad}</span>`;
  $('#wastatus').innerHTML = `
    <dt>Number</dt><dd>${w.configured ? yes(true, 'Connected · phone number ID ' + esc(w.phone_number_id), '') : yes(false, '', 'Not connected. Add WHATSAPP_TOKEN and WHATSAPP_PHONE_NUMBER_ID to .env')}</dd>
    <dt>Signature check</dt><dd>${yes(w.signature_check, 'On', 'Off. Set WHATSAPP_APP_SECRET')}</dd>
    <dt>Verify token</dt><dd>${yes(w.custom_verify_token, 'Custom', 'Still the default. Change WHATSAPP_VERIFY_TOKEN')}</dd>
    <dt>Updates after 24 h</dt><dd>${w.template ? 'Template “' + esc(w.template) + '”' : '<span class="muted">No template, so updates only reach people who messaged in the last 24 h</span>'}</dd>
    <dt>Last message in</dt><dd>${ago(w.last_webhook)}</dd>
    <dt>Last error</dt><dd>${w.last_error ? '<span class="bad">' + esc(w.last_error) + '</span>' : 'None'}</dd>`;
}

function renderAlert() {
  const a = S.alert;
  $('#alertbox').innerHTML = a ? `<div class="alert"><span class="pill p-over">CAP DRAFT</span><div><b>Flood warning drafted for ${a.places.join(', ')}</b><p>Waiting for TNSDMA approval before cell broadcast. Peak load ${Math.round(a.peak * 100)}% of channel capacity.</p></div></div>` : '';
}

function render() {
  if (!S) return;
  const sc = S.scenario;
  $('#rainv').innerHTML = `${Math.round(sc.rain * 10) / 10} <small>mm/h</small>`;
  if (document.activeElement !== $('#rain')) $('#rain').value = Math.min(100, sc.rain);
  $('#rainnote').hidden = !sc.rainNote; $('#rainnote').textContent = sc.rainNote;
  document.querySelectorAll('#presets .chip[data-r]').forEach(c => c.setAttribute('aria-pressed', sc.rainSource === 'manual' && +c.dataset.r === sc.rain));
  $('#live').setAttribute('aria-pressed', sc.rainSource === 'open-meteo');
  document.querySelectorAll('#tide button').forEach(b => b.setAttribute('aria-pressed', (b.dataset.v === 'high') === sc.tideHigh));
  document.querySelectorAll('#marshs button').forEach(b => b.setAttribute('aria-pressed', (b.dataset.v === '1') === sc.marshHealthy));
  $('#k-in').textContent = S.inflow.toFixed(1);
  const ov = S.channels.filter(c => c.status === 'over').length;
  $('#k-over').textContent = ov; $('#kk-over').classList.toggle('bad', ov > 0);
  $('#k-t').textContent = S.actions.length;
  $('#integ').textContent = `WhatsApp ${S.integrations.whatsapp ? 'live' : 'simulator only'} · YOLO ${S.integrations.yolo ? 'on' : 'off'}`;
  renderMap(); renderDetail(); renderActions(); renderReports(); renderAlert(); renderWhatsApp();
}
function apply(s) { if (s && s.type === 'state') { S = s; render(); } }

// controls
let rainTimer;
$('#rain').addEventListener('input', ev => {
  $('#rainv').innerHTML = `${ev.target.value} <small>mm/h</small>`;
  clearTimeout(rainTimer); rainTimer = setTimeout(() => act('/api/scenario', { rain: +ev.target.value }), 150);
});
document.querySelectorAll('#presets .chip[data-r]').forEach(c => c.addEventListener('click', () => act('/api/scenario', { rain: +c.dataset.r })));
$('#live').addEventListener('click', () => act('/api/rain/live', {}, 'Rainfall updated from Open-Meteo.'));
document.querySelectorAll('#tide button').forEach(b => b.addEventListener('click', () => act('/api/scenario', { tideHigh: b.dataset.v === 'high' })));
document.querySelectorAll('#marshs button').forEach(b => b.addEventListener('click', () => act('/api/scenario', { marshHealthy: b.dataset.v === '1' })));
let resetArmed = false;
$('#reset').addEventListener('click', () => {
  if (!resetArmed) { resetArmed = true; $('#reset').textContent = 'Click again to reset'; setTimeout(() => { resetArmed = false; $('#reset').textContent = 'Reset demo'; }, 3000); return; }
  resetArmed = false; $('#reset').textContent = 'Reset demo'; act('/api/reset', {}, 'Demo data reset.');
});

// live updates
function connect() {
  const es = new EventSource('/api/stream');
  es.onopen = () => { $('#conn').className = 'conn on'; $('#conn').lastChild.textContent = 'Live'; };
  es.onmessage = ev => {
    const d = JSON.parse(ev.data);
    if (d.type === 'notice') { toast(d.text); return; }
    if (d.type === 'state') {
      apply(d);
      const top = d.reports[0], topId = top ? top.id : 0;
      if (lastReportId !== null && topId > lastReportId) { selected = top.channel || selected; render(); toast(`New citizen report R-${top.id}`); }
      lastReportId = topId;
    }
  };
  es.onerror = () => { $('#conn').className = 'conn off'; $('#conn').lastChild.textContent = 'Reconnecting…'; };
}
api('/api/state').then(apply).catch(e => toast('Cannot reach the MarshGuard server: ' + e.message));
connect();
