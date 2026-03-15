const statusLine = document.getElementById('status-line');
const sessionStatusEl = document.getElementById('session-status');
const taskStatusEl = document.getElementById('task-status');
const audioStatusEl = document.getElementById('audio-status');
const inputsEl = document.getElementById('inputs');
const outputsEl = document.getElementById('outputs');
const eventsEl = document.getElementById('event-log');
const visualEl = document.getElementById('visual-stim');

// Head-fixed, function-first ordering for UI display.
const INPUT_FUNCTION_ORDER = [
  'ir_lick_left',
  'ir_lick_right',
  'ir_lick_center',
  'lick_left',
  'lick_right',
  'lick_center',
  'poke_left',
  'poke_right',
  'poke_center',
  'treadmill_1',
  'treadmill_2',
  'poke_extra1',
  'poke_extra2',
  'trigger_in',
  'user_input',
];

const OUTPUT_FUNCTION_ORDER = [
  'reward_left',
  'reward_right',
  'reward_center',
  'reward_4',
  'reward_5',
  'airpuff',
  'vacuum',
  'cue_led_1',
  'cue_led_2',
  'cue_led_3',
  'cue_led_4',
  'cue_led_5',
  'cue_led_6',
  'trigger_out',
  'ttl_output',
];

function buildOrderIndex(labels) {
  return new Map(labels.map((label, idx) => [label, idx]));
}

const inputOrderIndex = buildOrderIndex(INPUT_FUNCTION_ORDER);
const outputOrderIndex = buildOrderIndex(OUTPUT_FUNCTION_ORDER);

function sortByFunctionOrder(pins, orderIndex) {
  return [...pins].sort((a, b) => {
    const labelA = a.label || '';
    const labelB = b.label || '';
    const rankA = orderIndex.has(labelA) ? orderIndex.get(labelA) : Number.MAX_SAFE_INTEGER;
    const rankB = orderIndex.has(labelB) ? orderIndex.get(labelB) : Number.MAX_SAFE_INTEGER;
    if (rankA !== rankB) return rankA - rankB;
    if (labelA !== labelB) return labelA.localeCompare(labelB);
    return (a.pin ?? Number.MAX_SAFE_INTEGER) - (b.pin ?? Number.MAX_SAFE_INTEGER);
  });
}

function fmtTs(ts) {
  if (!ts) return '-';
  const d = new Date(ts * 1000);
  return d.toLocaleTimeString();
}

function statePill(active) {
  const klass = active ? 'state-pill state-on' : 'state-pill state-off';
  return `<span class="${klass}">${active ? 'ACTIVE' : 'INACTIVE'}</span>`;
}

function escapeHtml(value) {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

function pinDisplayLabel(pin) {
  const label = pin.label || `pin_${pin.pin}`;
  const aliases = Array.isArray(pin.aliases) ? pin.aliases.filter(Boolean) : [];
  return aliases.length > 0
    ? `${label} (${aliases.join(', ')})`
    : label;
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

function renderRuntime(runtime = {}) {
  const session = runtime.session || {};
  const task = runtime.task || {};
  const audio = runtime.audio || {};

  const protocolName = task.protocol_name || session.protocol_name || 'idle';
  const lifecycleState = session.lifecycle_state || 'unknown';
  const sessionActive = !!session.active;
  const phase = task.phase || 'idle';
  const trialNumber = Number.isInteger(task.trial_index) ? task.trial_index + 1 : null;
  const maxTrials = task.max_trials ?? '-';
  const taskActive = !!task.phase;
  const cueActive = !!audio.active;
  const cueName = audio.current_cue_name || audio.last_cue_name || 'none';

  statusLine.textContent = sessionActive
    ? `Running ${protocolName} | ${phase}`
    : `Connected | session ${lifecycleState}`;

  sessionStatusEl.innerHTML = `
    <div class="pin-title">
      <span class="pin-label">Session</span>
      ${statePill(sessionActive)}
    </div>
    <div class="runtime-value">${escapeHtml(protocolName)}</div>
    <div class="runtime-detail">Lifecycle: ${escapeHtml(lifecycleState)}</div>
    <div class="runtime-detail">Box: ${escapeHtml(session.box_name || '-')}</div>
  `;

  const taskHeadline = taskActive ? `${escapeHtml(phase)}` : 'No active task phase';
  const taskHighlight = trialNumber === null
    ? '<div class="highlight">Waiting for first trial.</div>'
    : `<div class="highlight">Trial ${trialNumber} of ${escapeHtml(maxTrials)} | ${escapeHtml(task.trial_type || 'unassigned')}</div>`;
  taskStatusEl.innerHTML = `
    <div class="pin-title">
      <span class="pin-label">Task</span>
      ${statePill(taskActive)}
    </div>
    <div class="runtime-value">${taskHeadline}</div>
    <div class="runtime-detail">Completed trials: ${escapeHtml(task.completed_trials ?? 0)}</div>
    <div class="runtime-detail">Stimulus active: ${task.stimulus_active ? 'yes' : 'no'}</div>
    ${taskHighlight}
  `;

  audioStatusEl.innerHTML = `
    <div class="pin-title">
      <span class="pin-label">Audio Cue</span>
      ${statePill(cueActive)}
    </div>
    <div class="runtime-value">${escapeHtml(cueName)}</div>
    <div class="runtime-detail">Cue playing: ${cueActive ? 'yes' : 'no'}</div>
    <div class="runtime-detail">Current cue: ${escapeHtml(audio.current_cue_name || '-')}</div>
    <div class="runtime-detail">Last cue: ${escapeHtml(audio.last_cue_name || '-')}</div>
  `;
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
  const inputs = sortByFunctionOrder(
    pins.filter(p => p.direction === 'input'),
    inputOrderIndex
  );
  const outputs = sortByFunctionOrder(
    pins.filter(p => p.direction === 'output'),
    outputOrderIndex
  );

  inputsEl.innerHTML = '';
  outputsEl.innerHTML = '';

  for (const pin of inputs) {
    const label = pin.label || `pin_${pin.pin}`;
    const card = document.createElement('div');
    card.className = 'card';
    card.innerHTML = `
      <div class="pin-title">
        <span class="pin-label">${escapeHtml(pinDisplayLabel(pin))}</span>
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
        <span class="pin-label">${escapeHtml(pinDisplayLabel(pin))}</span>
        ${statePill(pin.active)}
      </div>
      <div class="pin-meta">GPIO ${pin.pin}</div>
      <div class="pin-meta">Type: ${pin.device_type || '-'}</div>
      <div class="pin-meta">Value: ${pin.value}</div>
      <div class="controls">
        <button data-label="${label}" data-output-action="on">On</button>
        <button data-label="${label}" data-output-action="off">Off</button>
        <button data-label="${label}" data-output-action="toggle">Toggle</button>
      </div>
      <div class="controls">
        <input type="number" min="0" step="10" value="100" data-output-duration="${label}" aria-label="Output pulse duration ms">
        <button data-label="${label}" data-output-action="pulse">Pulse (ms)</button>
      </div>
    `;
    outputsEl.appendChild(card);
  }

  renderVisual(state.visual || {});
  renderRuntime(state.runtime || {});
}

function describeEvent(event) {
  if (event.kind === 'pin') {
    return {
      kind: 'pin',
      label: event.label || '-',
      pin: event.pin ?? '-',
      value: event.value ?? '-',
      source: event.source || '-',
    };
  }
  if (event.kind === 'visual_stim') {
    return {
      kind: 'visual_stim',
      label: event.current_grating || 'visual stimulus',
      pin: '-',
      value: event.visual_stim_active ?? '-',
      source: event.source || '-',
    };
  }
  if ((event.kind || '').startsWith('runtime_')) {
    const section = event.section || event.kind.replace('runtime_', '');
    const label = section === 'task'
      ? `${event.phase || 'idle'}${event.trial_type ? ` (${event.trial_type})` : ''}`
      : section === 'audio'
        ? (event.current_cue_name || event.last_cue_name || 'audio state')
        : (event.protocol_name || section);
    const value = section === 'task'
      ? `trial=${event.trial_index ?? '-'} completed=${event.completed_trials ?? '-'}`
      : section === 'audio'
        ? `active=${event.active ? 'yes' : 'no'}`
        : `state=${event.lifecycle_state || '-'}`;
    return {
      kind: event.kind,
      label,
      pin: '-',
      value,
      source: event.source || '-',
    };
  }
  return {
    kind: event.kind || '-',
    label: event.label || '-',
    pin: event.pin ?? '-',
    value: event.value ?? '-',
    source: event.source || '-',
  };
}

function renderEvents(events) {
  eventsEl.innerHTML = '';
  for (const event of events) {
    const rowState = describeEvent(event);
    const row = document.createElement('tr');
    row.innerHTML = `
      <td>${fmtTs(event.ts)}</td>
      <td>${escapeHtml(rowState.kind)}</td>
      <td>${escapeHtml(rowState.label)}</td>
      <td>${escapeHtml(rowState.pin)}</td>
      <td>${escapeHtml(rowState.value)}</td>
      <td>${escapeHtml(rowState.source)}</td>
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
    if (!stateRes.ok) {
      throw new Error(`state ${stateRes.status}`);
    }
    if (!eventsRes.ok) {
      throw new Error(`events ${eventsRes.status}`);
    }
    const state = await stateRes.json();
    const events = await eventsRes.json();

    renderPins(state);
    renderEvents(events.events || []);
  } catch (err) {
    statusLine.textContent = `Error: ${err.message}`;
  }
}

document.body.addEventListener('click', async (evt) => {
  const btn = evt.target.closest('button[data-action]');
  if (btn) {
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
    return;
  }

  const outputBtn = evt.target.closest('button[data-output-action]');
  if (!outputBtn) return;

  const outputLabel = outputBtn.getAttribute('data-label');
  const outputAction = outputBtn.getAttribute('data-output-action');

  try {
    if (outputAction === 'pulse') {
      const durationInput = document.querySelector(`input[data-output-duration="${outputLabel}"]`);
      const duration = parseInt(durationInput?.value || '100', 10);
      await postJson(`/api/output/${encodeURIComponent(outputLabel)}/pulse`, { duration_ms: duration });
    } else {
      await postJson(`/api/output/${encodeURIComponent(outputLabel)}/${outputAction}`);
    }
    await refresh();
  } catch (err) {
    statusLine.textContent = `Action failed: ${err.message}`;
  }
});

refresh();
setInterval(refresh, 300);
