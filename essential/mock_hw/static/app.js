const statusLine = document.getElementById('status-line');
const inputsEl = document.getElementById('inputs');
const outputsEl = document.getElementById('outputs');
const eventsEl = document.getElementById('event-log');
const visualEl = document.getElementById('visual-stim');

function fmtTs(ts) {
  if (!ts) return '-';
  const d = new Date(ts * 1000);
  return d.toLocaleTimeString();
}

function statePill(active) {
  const klass = active ? 'state-pill state-on' : 'state-pill state-off';
  return `<span class="${klass}">${active ? 'ACTIVE' : 'INACTIVE'}</span>`;
}

async function postJson(url, body = null) {
  const opts = { method: 'POST', headers: { 'Content-Type': 'application/json' } };
  if (body !== null) opts.body = JSON.stringify(body);
  const res = await fetch(url, opts);
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text);
  }
  return await res.json();
}

function renderVisual(visual) {
  const enabled = !!visual.visual_stim_enabled;
  const active = !!visual.visual_stim_active;
  const grating = visual.current_grating || 'None';
  visualEl.innerHTML = `
    <div class="pin-title">
      <span class="pin-label">Visual Stimulus</span>
      ${statePill(active)}
    </div>
    <div class="pin-meta">Enabled: ${enabled}</div>
    <div class="pin-meta">Current grating: ${grating}</div>
    <div class="pin-meta">Last ON: ${fmtTs(visual.last_visual_stim_on_ts)}</div>
    <div class="pin-meta">Last OFF: ${fmtTs(visual.last_visual_stim_off_ts)}</div>
  `;
}

function renderPins(state) {
  const pins = state.pins || [];
  const inputs = pins.filter(p => p.direction === 'input');
  const outputs = pins.filter(p => p.direction === 'output');

  inputsEl.innerHTML = '';
  outputsEl.innerHTML = '';

  for (const pin of inputs) {
    const label = pin.label || `pin_${pin.pin}`;
    const card = document.createElement('div');
    card.className = 'card';
    card.innerHTML = `
      <div class="pin-title">
        <span class="pin-label">${label}</span>
        ${statePill(pin.active)}
      </div>
      <div class="pin-meta">GPIO ${pin.pin}</div>
      <div class="controls">
        <button data-label="${label}" data-action="press">Press</button>
        <button data-label="${label}" data-action="release">Release</button>
      </div>
      <div class="controls">
        <input type="number" min="0" step="10" value="100" data-duration="${label}" aria-label="Pulse duration ms">
        <button data-label="${label}" data-action="pulse">Pulse (ms)</button>
      </div>
    `;
    inputsEl.appendChild(card);
  }

  for (const pin of outputs) {
    const label = pin.label || `pin_${pin.pin}`;
    const card = document.createElement('div');
    card.className = 'card';
    card.innerHTML = `
      <div class="pin-title">
        <span class="pin-label">${label}</span>
        ${statePill(pin.active)}
      </div>
      <div class="pin-meta">GPIO ${pin.pin}</div>
      <div class="pin-meta">Type: ${pin.device_type || '-'}</div>
      <div class="pin-meta">Value: ${pin.value}</div>
    `;
    outputsEl.appendChild(card);
  }

  renderVisual(state.visual || {});
}

function renderEvents(events) {
  eventsEl.innerHTML = '';
  for (const event of events) {
    const row = document.createElement('tr');
    row.innerHTML = `
      <td>${fmtTs(event.ts)}</td>
      <td>${event.kind || '-'}</td>
      <td>${event.label || event.current_grating || '-'}</td>
      <td>${event.pin ?? '-'}</td>
      <td>${event.value ?? event.visual_stim_active ?? '-'}</td>
      <td>${event.source || '-'}</td>
    `;
    eventsEl.appendChild(row);
  }
}

async function refresh() {
  try {
    const [stateRes, eventsRes] = await Promise.all([
      fetch('/api/state'),
      fetch('/api/events?limit=150'),
    ]);
    const state = await stateRes.json();
    const events = await eventsRes.json();

    renderPins(state);
    renderEvents(events.events || []);
    statusLine.textContent = 'Connected';
  } catch (err) {
    statusLine.textContent = `Error: ${err.message}`;
  }
}

document.body.addEventListener('click', async (evt) => {
  const btn = evt.target.closest('button[data-action]');
  if (!btn) return;

  const label = btn.getAttribute('data-label');
  const action = btn.getAttribute('data-action');

  try {
    if (action === 'pulse') {
      const durationInput = document.querySelector(`input[data-duration="${label}"]`);
      const duration = parseInt(durationInput?.value || '100', 10);
      await postJson(`/api/input/${encodeURIComponent(label)}/pulse`, { duration_ms: duration });
    } else {
      await postJson(`/api/input/${encodeURIComponent(label)}/${action}`);
    }
    await refresh();
  } catch (err) {
    statusLine.textContent = `Action failed: ${err.message}`;
  }
});

refresh();
setInterval(refresh, 300);
