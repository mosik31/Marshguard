// MarshGuard WhatsApp simulator — sends messages through the same server pipeline as the real webhook.
const $ = s => document.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

let phone;
try { phone = localStorage.getItem('mg.simPhone'); } catch (e) { }
if (!phone) { phone = 'sim-' + Math.random().toString(36).slice(2, 8); try { localStorage.setItem('mg.simPhone', phone); } catch (e) { } }

const msgs = [];
const now = () => new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
function push(dir, text, att) { msgs.push({ dir, text, att, t: now() }); renderMsgs(); }
function renderMsgs() {
  $('#msgs').innerHTML = msgs.map(m => `<div class="msg ${m.dir}">${m.att ? `<span class="att">${esc(m.att)}</span>` : ''}${esc(m.text)}<span class="time">${m.t}</span></div>`).join('');
  $('#msgs').scrollTop = 1e6;
}

async function send(body, outText, att) {
  push('out', outText, att);
  setBusy(true);
  try {
    const r = await fetch('/api/sim/message', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ phone, ...body }) });
    const d = await r.json();
    if (!r.ok) throw new Error(d.detail || r.statusText);
    for (const r of d.replies) {
      await new Promise(res => setTimeout(res, 450));
      const text = typeof r === 'string' ? r : r.text;
      push('in', r.type === 'location_request' ? text + '\n\n[ 📍 Send location ]' : text);
    }
    if (d.event) showEvent(d.event);
  } catch (e) {
    push('in', 'Could not reach the MarshGuard server. Is it running? (' + e.message + ')');
  } finally { setBusy(false); }
}
function setBusy(b) { document.querySelectorAll('.compose button').forEach(x => x.disabled = b); }

function showEvent(ev) {
  if (ev.kind === 'review') {
    $('#twin').innerHTML = `<h2>Twin update · R-${ev.report}</h2><p class="muted" style="margin:0">Waiting for an engineer to verify the photo on the dashboard. The channel is unchanged until then.</p>`;
    return;
  }
  const lost = Math.round((1 - ev.cap_after / ev.cap_before) * 100);
  $('#twin').innerHTML = `<h2>Twin update · ${ev.ticket}</h2><dl class="kv">
    <dt>Channel</dt><dd>${esc(ev.name)}</dd><dt>Routed to</dt><dd>${ev.agency}</dd><dt>Blockage</dt><dd>${esc(ev.label)}</dd>
    <dt>Capacity before</dt><dd>${ev.cap_before.toFixed(2)} m³/s</dd><dt>Capacity after</dt><dd>${ev.cap_after.toFixed(2)} m³/s</dd>
    <dt>Change</dt><dd>${lost > 0 ? lost + '% lost' : 'no further loss'}</dd><dt>Prescribed</dt><dd>${esc(ev.machine)}</dd></dl>`;
}

$('#c-sendphoto').addEventListener('click', () => {
  const sel = $('#c-photo'); send({ type: 'image', sample: sel.value }, sel.selectedOptions[0].textContent, '[photo]');
});
$('#c-sendloc').addEventListener('click', () => {
  const sel = $('#c-loc'); send({ type: 'location', channel: sel.value }, sel.selectedOptions[0].textContent.replace(/^📍\s*/, 'Near '), '[location pin]');
});
$('#c-form').addEventListener('submit', ev => {
  ev.preventDefault(); const t = $('#c-text').value.trim(); if (!t) return;
  $('#c-text').value = ''; send({ type: 'text', text: t }, t);
});

// messages the server sends later (ticket cleared, review outcome)
function connect() {
  const es = new EventSource('/api/stream');
  es.onmessage = ev => {
    const d = JSON.parse(ev.data);
    if (d.type === 'bot' && d.phone === phone) push('in', d.text);
  };
}

fetch('/api/channels').then(r => r.json()).then(list => {
  $('#c-loc').innerHTML = list.map(c => `<option value="${c.id}">📍 ${esc(c.name)}</option>`).join('');
}).catch(() => push('in', 'Could not reach the MarshGuard server.'));
send({ type: 'text', text: 'hi' }, 'Hi');
connect();
