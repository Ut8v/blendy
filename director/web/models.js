// Models tab: the recipes the modeling agent builds, their turntables, and a rebuild button.
const $ = id => document.getElementById(id);
const esc = s => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
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
      <div class="item ${m.id === state.current ? 'sel' : ''}" data-model="${esc(m.id)}">
        <code>${esc(m.id)}</code>
        <span class="beat">${esc(m.kind)} · ${m.parts} parts${m.height ? ' · ' + m.height.toFixed(2) + 'm' : ''}</span>
        <span class="badge ${m.built ? 'done' : ''}">${m.built ? 'built' : 'not built'}</span>
      </div>`).join('') || '<div class="beat muted">no model recipes yet</div>';
    list.querySelectorAll('[data-model]').forEach(el =>
      el.addEventListener('click', () => { state.current = el.dataset.model; render(); }));

    const m = state.models.find(x => x.id === state.current);
    $('modelTitle').textContent = m ? m.id : '—';
    if (!m) { body.innerHTML = ''; return; }
    const shots = [...m.previews].sort((a, b) => ORDER.indexOf(a.view) - ORDER.indexOf(b.view));
    body.innerHTML = shots.length
      ? shots.map(p => `<figure class="${p.view === 'compare' ? 'wide' : ''}">
           <img src="/files/${p.path}?t=${p.mtime}" data-full="/files/${p.path}" loading="lazy">
           <figcaption>${esc(p.view)}</figcaption></figure>`).join('')
      : `<div class="beat muted" style="padding:12px">no turntable yet — press build.${
           m.reference ? ` reference: <img src="/files/${m.reference}" style="height:120px;display:block;margin-top:8px">` : ''}</div>`;
  }

  async function build() {
    const m = state.models.find(x => x.id === state.current);
    if (!m) return;
    const status = $('modelStatus');
    status.textContent = `building ${m.id}…`;
    try {
      const r = await api.post('/api/action', { action: 'preview_model',
        args: { model: m.id, quality: $('modelQuality').value } });
      status.textContent = r.ok === false || r.ok === undefined && r.errors
        ? `failed: ${(r.error || (r.errors || []).map(e => e.code + ' @ ' + (e.entity_id || e.path)).join('; '))}`
        : `built in ${r.seconds}s · ${r.height ? r.height.toFixed(3) + 'm' : ''} · ${r.poly_count} polys`;
    } catch (e) { status.textContent = e.message; }
    await refresh();
  }

  $('modelBuild').addEventListener('click', build);
  $('modelReload').addEventListener('click', () => refresh());
  return { refresh, current: () => state.current };
}
