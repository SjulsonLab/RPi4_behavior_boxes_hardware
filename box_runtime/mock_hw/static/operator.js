const operatorStatusLine = document.getElementById('operator-status-line');
const runStatusPill = document.getElementById('run-status-pill');
const operatorRunState = document.getElementById('operator-run-state');
const performancePlotEl = document.getElementById('performance-plot');
const sessionStatusEl = document.getElementById('session-status');
const taskStatusEl = document.getElementById('task-status');
const audioStatusEl = document.getElementById('audio-status');
const visualStatusEl = document.getElementById('visual-status');
const eventLogEl = document.getElementById('event-log');
const cameraGridEl = document.getElementById('camera-grid');
const armForm = document.getElementById('arm-form');
const startTaskButton = document.getElementById('start-task');
const stopButton = document.getElementById('stop-session');
let performanceChart = null;

const previewVisibility = {
  camera0: true,
  camera1: false,
};

function escapeHtml(value) {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

function formatTime(ts) {
  if (!ts) return '-';
  return new Date(ts * 1000).toLocaleTimeString();
}

function statusClass(status) {
  if (['running', 'starting', 'completed'].includes(status)) return 'on';
  if (['stopping', 'error'].includes(status)) return 'warn';
  return 'off';
}

function setRunState(state) {
  runStatusPill.className = `status-pill ${statusClass(state.status)}`;
  runStatusPill.textContent = state.status;
  operatorRunState.textContent = JSON.stringify(state, null, 2);
  const armDisabled = !['idle', 'completed', 'error'].includes(state.status);
  const startDisabled = state.status !== 'armed';
  const stopDisabled = !['armed', 'starting', 'running', 'stopping'].includes(state.status);
  document.getElementById('arm-session').disabled = armDisabled;
  startTaskButton.disabled = startDisabled;
  stopButton.disabled = stopDisabled;
}

function renderCard(el, title, active, lines) {
  const pillText = active ? 'active' : 'idle';
  el.innerHTML = `
    <div class="card-title">
      <h3>${escapeHtml(title)}</h3>
      <span class="status-pill ${active ? 'on' : 'off'}">${pillText}</span>
    </div>
    ${lines.map((line) => `<div class="meta-line">${line}</div>`).join('')}
  `;
}

function renderRuntime(state) {
  const runtime = state.runtime || {};
  const session = runtime.session || {};
  const task = runtime.task || {};
  const audio = runtime.audio || {};
  const visual = state.visual || {};

  operatorStatusLine.textContent = session.active
    ? `Running ${task.protocol_name || session.protocol_name || 'task'}`
    : 'Ready for operator launch';

  renderCard(sessionStatusEl, 'Session', !!session.active, [
    `Protocol: ${escapeHtml(task.protocol_name || session.protocol_name || 'idle')}`,
    `Lifecycle: ${escapeHtml(session.lifecycle_state || 'idle')}`,
    `Box: ${escapeHtml(session.box_name || '-')}`,
  ]);

  renderCard(taskStatusEl, 'Task', !!task.phase, [
    `Phase: ${escapeHtml(task.phase || 'idle')}`,
    `Trial: ${task.trial_index == null ? '-' : task.trial_index + 1}`,
    `Type: ${escapeHtml(task.trial_type || '-')}`,
    `Completed: ${escapeHtml(task.completed_trials ?? 0)} / ${escapeHtml(task.max_trials ?? '-')}`,
  ]);

  renderCard(audioStatusEl, 'Audio', !!audio.active, [
    `Current cue: ${escapeHtml(audio.current_cue_name || '-')}`,
    `Last cue: ${escapeHtml(audio.last_cue_name || '-')}`,
  ]);

  renderCard(visualStatusEl, 'Visual', !!visual.visual_stim_active, [
    `Enabled: ${visual.visual_stim_enabled ? 'yes' : 'no'}`,
    `Current grating: ${escapeHtml(visual.current_grating || '-')}`,
    `Last on: ${formatTime(visual.last_visual_stim_on_ts)}`,
  ]);

  renderCameras(runtime.camera || {});
  renderPlot(runtime.plot || { trial_outcomes: [], rates: {}, counts: {} });
}

function renderPlot(plotState) {
  const outcomes = Array.isArray(plotState.trial_outcomes) ? plotState.trial_outcomes : [];
  const labels = outcomes.map((entry) => `T${(entry.trial_index ?? 0) + 1}`);
  const outcomeValues = outcomes.map((entry) => {
    if (entry.outcome === 'hit') return 1;
    if (entry.outcome === 'correct_reject') return 0.75;
    if (entry.outcome === 'miss') return 0.25;
    if (entry.outcome === 'false_alarm') return 0;
    return null;
  });
  const hitRate = plotState.rates?.hit_rate;
  const falseAlarmRate = plotState.rates?.false_alarm_rate;
  const rateLabels = labels.length > 0 ? labels : ['No trials yet'];
  const hitLine = rateLabels.map(() => hitRate);
  const faLine = rateLabels.map(() => falseAlarmRate);

  if (typeof Chart === 'undefined') {
    const ctx = performancePlotEl.getContext('2d');
    ctx.clearRect(0, 0, performancePlotEl.width, performancePlotEl.height);
    ctx.font = '16px IBM Plex Sans';
    ctx.fillText('Chart library unavailable', 24, 40);
    return;
  }

  if (performanceChart === null) {
    performanceChart = new Chart(performancePlotEl, {
      type: 'line',
      data: {
        labels: rateLabels,
        datasets: [
          {
            label: 'Outcome timeline',
            data: outcomeValues.length > 0 ? outcomeValues : [null],
            borderColor: '#8a4b14',
            backgroundColor: 'rgba(138, 75, 20, 0.12)',
            tension: 0.25,
          },
          {
            label: 'Hit rate',
            data: hitLine,
            borderColor: '#0f7a43',
            tension: 0.2,
          },
          {
            label: 'False-alarm rate',
            data: faLine,
            borderColor: '#9a3412',
            tension: 0.2,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        scales: {
          y: {
            min: 0,
            max: 1,
          },
        },
      },
    });
    return;
  }

  performanceChart.data.labels = rateLabels;
  performanceChart.data.datasets[0].data = outcomeValues.length > 0 ? outcomeValues : [null];
  performanceChart.data.datasets[1].data = hitLine;
  performanceChart.data.datasets[2].data = faLine;
  performanceChart.update('none');
}

function previewMarkup(cameraId, camera) {
  const visible = previewVisibility[cameraId] !== false;
  if (!visible) {
    return `
      <div class="camera-preview">
        <div class="preview-copy">Preview hidden</div>
      </div>
    `;
  }
  if (camera.preview_url) {
    return `
      <div class="camera-preview">
        <img src="${escapeHtml(camera.preview_url)}" alt="${escapeHtml(cameraId)} preview">
      </div>
    `;
  }
  if (camera.preview_available) {
    return `
      <div class="camera-preview">
        <div class="preview-copy">Preview available, but no browser stream URL is published yet.</div>
      </div>
    `;
  }
  return `
    <div class="camera-preview">
      <div class="preview-copy">No browser preview available</div>
    </div>
  `;
}

function renderCameras(cameraState) {
  const cameraIds = ['camera0', 'camera1'];
  cameraGridEl.innerHTML = '';
  for (const cameraId of cameraIds) {
    const camera = cameraState[cameraId] || {
      camera_id: cameraId,
      recording: false,
      prepared: false,
      preview_active: false,
      preview_available: false,
      preview_url: null,
    };
    const slot = document.createElement('article');
    slot.className = 'camera-slot';
    slot.dataset.cameraId = cameraId;
    slot.innerHTML = `
      <div class="camera-title">
        <div>
          <h3>${escapeHtml(cameraId)}</h3>
          <div class="meta-line">Recording: ${camera.recording ? 'yes' : 'no'}</div>
        </div>
        <button type="button" class="secondary toggle" data-camera-toggle="${escapeHtml(cameraId)}">
          ${previewVisibility[cameraId] !== false ? 'Hide preview' : 'Show preview'}
        </button>
      </div>
      <div class="meta-line">Prepared: ${camera.prepared ? 'yes' : 'no'}</div>
      <div class="meta-line">Preview mode: ${escapeHtml(camera.preview_mode || 'off')}</div>
      <div class="meta-line">Preview active: ${camera.preview_active ? 'yes' : 'no'}</div>
      ${previewMarkup(cameraId, camera)}
    `;
    cameraGridEl.appendChild(slot);
  }
}

function describeEvent(event) {
  if (event.kind === 'pin') {
    return {
      label: event.label || '-',
      value: event.value ?? '-',
      source: event.source || '-',
    };
  }
  if ((event.kind || '').startsWith('runtime_')) {
    return {
      label: event.section || event.kind,
      value: event.phase || event.lifecycle_state || event.current_cue_name || '-',
      source: event.source || '-',
    };
  }
  return {
    label: event.label || event.current_grating || '-',
    value: event.value ?? '-',
    source: event.source || '-',
  };
}

function renderEvents(events) {
  eventLogEl.innerHTML = '';
  for (const event of events.slice(0, 12)) {
    const summary = describeEvent(event);
    const row = document.createElement('tr');
    row.innerHTML = `
      <td>${formatTime(event.ts)}</td>
      <td>${escapeHtml(event.kind || '-')}</td>
      <td>${escapeHtml(summary.label)}</td>
      <td>${escapeHtml(summary.value)}</td>
      <td>${escapeHtml(summary.source)}</td>
    `;
    eventLogEl.appendChild(row);
  }
}

async function postJson(url, body = null) {
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: body === null ? null : JSON.stringify(body),
  });
  const payload = await res.json();
  if (!res.ok) {
    throw new Error(payload.error || `HTTP ${res.status}`);
  }
  return payload;
}

async function refresh() {
  try {
    const [stateRes, eventsRes, operatorRes] = await Promise.all([
      fetch('/api/state'),
      fetch('/api/events?limit=25'),
      fetch('/api/operator/state'),
    ]);
    const [state, events, operatorState] = await Promise.all([
      stateRes.json(),
      eventsRes.json(),
      operatorRes.json(),
    ]);
    renderRuntime(state);
    renderEvents(events.events || []);
    setRunState(operatorState);
  } catch (err) {
    operatorStatusLine.textContent = `Error: ${err.message}`;
  }
}

armForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  const payload = {
    session_tag: document.getElementById('session-tag').value.trim(),
    max_trials: Number.parseInt(document.getElementById('max-trials').value, 10),
    max_duration_s: Number.parseFloat(document.getElementById('max-duration-s').value),
    fake_mouse_enabled: document.getElementById('fake-mouse-enabled').checked,
    fake_mouse_seed: Number.parseInt(document.getElementById('fake-mouse-seed').value, 10),
  };
  try {
    const state = await postJson('/api/operator/arm', payload);
    setRunState(state);
    await refresh();
  } catch (err) {
    operatorStatusLine.textContent = `Arm failed: ${err.message}`;
  }
});

startTaskButton.addEventListener('click', async () => {
  try {
    const state = await postJson('/api/operator/start', {});
    setRunState(state);
    await refresh();
  } catch (err) {
    operatorStatusLine.textContent = `Start failed: ${err.message}`;
  }
});

stopButton.addEventListener('click', async () => {
  try {
    const state = await postJson('/api/operator/stop', {});
    setRunState(state);
    await refresh();
  } catch (err) {
    operatorStatusLine.textContent = `Stop failed: ${err.message}`;
  }
});

cameraGridEl.addEventListener('click', async (event) => {
  const button = event.target.closest('button[data-camera-toggle]');
  if (!button) return;
  const cameraId = button.getAttribute('data-camera-toggle');
  previewVisibility[cameraId] = !(previewVisibility[cameraId] !== false);
  await refresh();
});

refresh();
setInterval(refresh, 500);
