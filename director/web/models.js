// Models tab: the recipes the modeling agent builds, their turntables, and a rebuild button.
const $ = id => document.getElementById(id);
const esc = s => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
import { watchJob } from './activity.js';

const ORDER = ['compare', 'front', 'three_quarter', 'side', 'back', 'head'];

export function mountModels(api, studio) {
  const state = { models: [], current: null };

  async function refresh(keep = true) {
    state.models = await api.get('/api/models');
    if (!keep || !state.models.some(m => m.id === state.current))
      state.current = (state.models[0] || {}).id || null;
    render();
  }

  function render() {
    const list = $('modelList'), body = $('modelViews');
    list.innerHTML = state.models.map(m => `
      <div class="model-card ${m.id === state.current ? 'sel' : ''}" data-model="${esc(m.id)}">
        <div class="name">${esc(m.id)}</div>
        <div class="meta">${esc(m.kind)} · ${m.parts} parts${m.height ? ' · ' + m.height.toFixed(2) + 'm' : ''}
          <span class="badge ${m.built ? 'badge-ok' : 'badge-muted'}">${m.built ? 'built' : 'unbuilt'}</span></div>
      </div>`).join('') || '<div class="empty">No model recipes yet</div>';
    list.querySelectorAll('[data-model]').forEach(el =>
      el.addEventListener('click', () => { state.current = el.dataset.model; render(); }));

    const m = state.models.find(x => x.id === state.current);
    $('modelTitle').textContent = m ? m.id : '—';
    if (!m) { body.innerHTML = ''; return; }
    const shots = [...m.previews].sort((a, b) => ORDER.indexOf(a.view) - ORDER.indexOf(b.view));
    body.innerHTML = shots.length
      ? shots.map(p => `<figure class="${p.view === 'compare' ? 'wide' : ''}">
           <img src="/files/${p.path}?t=${p.mtime}" data-full="/files/${p.path}">
           <figcaption>${esc(p.view)}</figcaption></figure>`).join('')
      : `<div class="empty">No turntable yet. Press <b>Build turntable</b>.${
           m.reference ? `<img src="/files/${m.reference}" style="height:150px;display:block;margin:12px auto 0;border-radius:6px">` : ''}</div>`;
    loadDetail(m.id);
  }

  async function loadDetail(id) {
    try {
      studio.modelDetail = await api.get('/api/model?id=' + encodeURIComponent(id));
      if (studio.activeTab === 'models') studio.renderInspector('model');
    } catch { /* the recipe may be mid-write */ }
  }

  async function build() {
    const m = state.models.find(x => x.id === state.current);
    if (!m) return;
    const status = $('modelStatus');
    status.textContent = `building ${m.id}…`;
    try {
      const r = await api.post('/api/action', { action: 'preview_model',
        args: { model: m.id, quality: $('modelQuality').value } });
      studio.activity?.poll();
      const job = await watchJob(api, r.job, status, `building ${m.id}`);
      const res = (job && job.result) || {};
      status.textContent = job && job.ok
        ? `built in ${job.seconds}s · ${res.height ? res.height.toFixed(3) + ' m' : ''} · ${res.poly_count || '?'} polys`
        : `failed: ${job ? job.error : 'no job'}`;
    } catch (e) { status.textContent = e.message; }
    await refresh();
  }

  $('modelBuild').addEventListener('click', build);
  $('modelReload').addEventListener('click', () => refresh());
  return { refresh, current: () => state.current };
}
