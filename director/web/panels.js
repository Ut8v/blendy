// The inspector. It shows one context at a time: the shot you are working on,
// or the model you are building. Sections collapse so the panel is never a wall.
const $ = id => document.getElementById(id);
const esc = s => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

function section(title, body, { open = false, count = null } = {}) {
  const badge = count === null ? '' : `<span class="badge badge-muted">${count}</span>`;
  return `<details class="section"${open ? ' open' : ''}><summary>${esc(title)}${badge}</summary>
            <div class="section-body">${body}</div></details>`;
}

const kv = (k, v) => `<div class="kv"><span>${esc(k)}</span><span>${esc(v)}</span></div>`;
const empty = t => `<div class="muted" style="font-size:12px">${esc(t)}</div>`;

export function renderPanel(studio, api, mode = 'shot') {
  $('inspectorTitle').textContent = mode === 'model' ? 'Model' : 'Shot';
  if (mode === 'model') return renderModelPanel(studio);
  const o = studio.overview, info = studio.info, p = $('panel');
  if (!o) return;
  let h = '';

  if (info) {
    const v = info.valid;
    const bad = (v.errors || []).length, warn = (v.warnings || []).length;
    h += section('Validation', bad
      ? v.errors.slice(0, 8).map(e => `<div class="item"><span class="badge badge-bad">${esc(e.code)}</span>
           <span class="beat">${esc(e.entity_id || e.path)}: ${esc(String(e.message).slice(0, 150))}</span></div>`).join('')
      : `<div class="item"><span class="badge badge-ok">valid</span><span class="beat">${warn} warning${warn === 1 ? '' : 's'}</span></div>`,
      { open: bad > 0, count: bad || null });

    h += section('Shot', [
      kv('frames', `${info.frame_start}–${info.frame_end}`),
      kv('fps', info.fps),
      kv('aspect', info.aspect.toFixed(3)),
      info.resolved?.look ? kv('look', info.resolved.look.id) : '',
      info.resolved?.location ? kv('location', info.resolved.location.id) : '',
      info.resolved?.cast?.length ? kv('cast', info.resolved.cast.map(c => c && c.id).join(', ')) : '',
    ].join(''), { open: true });

    const cps = (info.checkpoints || []).slice().reverse();
    h += section('Checkpoints', `<div class="row"><input id="cpLabel" class="select" style="flex:1" placeholder="label">
        <button id="cpSave" class="btn btn-sm">Save</button></div>` +
      (cps.map(c => `<div class="item"><code>${esc(c.label)}</code>
         <span class="beat">${new Date(c.created * 1000).toLocaleTimeString()}${c.blend ? ' · .blend' : ''}</span>
         <button class="btn btn-ghost btn-sm" data-restore="${esc(c.label)}">Restore</button></div>`).join('') || empty('None yet')),
      { count: cps.length || null });

    const takes = info.takes || [];
    h += section('Takes', takes.map(t => `<div class="item"><code>${esc(t.id.slice(5, 20))}</code>
        <span class="beat">${esc(t.mode)} · ${t.samples}${t.promoted_to ? ' → ' + esc(t.promoted_to) : ''}</span>
        <button class="btn btn-ghost btn-sm" data-apply="${esc(t.id)}">Apply</button>
        <button class="btn btn-ghost btn-sm" data-promote="${esc(t.id)}" title="Promote to a camera preset">★</button></div>`).join('')
      || empty('Record one in the Director tab'), { count: takes.length || null });
  }

  // The bible and breakdown belong to a sequence. A standalone scene like a model
  // blockout is not part of one, so showing them there is just noise.
  const inSequence = !!(info && info.sequence);
  if (o.breakdown && inSequence) {
    const ok = o.breakdown.approved;
    h += section('Breakdown', (ok ? '' : `<div class="row"><span class="badge badge-bad">not approved</span>
          <button id="approveBd" class="btn btn-primary btn-sm">Approve</button></div>`) +
      o.breakdown.shots.map(s => `<div class="item click" data-shot="${esc(s.id)}"><code>${esc(s.id)}</code>
         <span class="beat">${esc(s.shot_type)} · ${s.duration_frames}f · ${esc(s.beat)}</span></div>`).join(''),
      { count: o.breakdown.shots.length });
  }
  h += section('Shots', o.shots.map(s => `<div class="item click${s.id === studio.shot ? ' sel' : ''}" data-shot="${esc(s.id)}">
      <code>${esc(s.id)}</code><span class="beat">${s.frames}f</span>
      <span class="badge ${s.render_state === 'done' ? 'badge-ok' : s.render_state === 'pending' ? 'badge-muted' : 'badge-warn'}">${esc(s.render_state)}</span></div>`).join(''),
    { count: o.shots.length });
  if (o.bible && inSequence) {
    h += section('Bible', [kv('title', o.bible.title), kv('cast', o.bible.cast.join(', ')),
                           kv('locations', o.bible.locations.join(', ')), kv('looks', o.bible.looks.join(', '))].join(''));
  }
  p.innerHTML = h;
  wire(p, studio, api);
}

function wire(p, studio, api) {
  const act = (action, args) => api.post('/api/action', { shot: studio.shot, action, args })
    .then(() => studio.refresh()).catch(e => alert(e.message));
  p.querySelectorAll('[data-shot]').forEach(el => el.addEventListener('click', () => studio.select(el.dataset.shot)));
  p.querySelector('#approveBd')?.addEventListener('click', async () => {
    const r = await api.post('/api/breakdown/approve');
    if (!r.approved) alert(JSON.stringify(r.errors, null, 1));
    studio.overviewRefresh();
  });
  p.querySelector('#cpSave')?.addEventListener('click', () => act('checkpoint', { label: p.querySelector('#cpLabel').value || 'step' }));
  p.querySelectorAll('[data-restore]').forEach(b => b.addEventListener('click', () => act('restore', { label: b.dataset.restore })));
  p.querySelectorAll('[data-apply]').forEach(b => b.addEventListener('click', () => act('apply_take', { take_id: b.dataset.apply })));
  p.querySelectorAll('[data-promote]').forEach(b => b.addEventListener('click', () => {
    const name = prompt('Preset name (snake_case)');
    if (name) act('promote_take', { take_id: b.dataset.promote, name, description: prompt('Description') || '', shot_types: [] });
  }));
}

export function renderModelPanel(studio) {
  const p = $('panel'), m = studio.modelDetail;
  if (!m) { p.innerHTML = empty('Select a model'); return; }
  const byOp = {};
  for (const part of m.parts) (byOp[part.op] ||= []).push(part.id);
  let h = section('Model', [kv('id', m.id), kv('kind', m.kind),
                            kv('parts', m.parts.length), kv('height', m.height ? m.height + ' m' : '—'),
                            m.reference ? kv('reference', m.reference.split('/').pop()) : ''].join(''), { open: true });
  h += section('Parts by builder', Object.entries(byOp).sort()
    .map(([op, ids]) => `<div class="item"><code>${esc(op)}</code><span class="beat">${esc(ids.join(', '))}</span>
       <span class="badge badge-muted">${ids.length}</span></div>`).join(''), { open: true, count: m.parts.length });
  h += section('Materials', Object.keys(m.materials || {}).map(k => `<div class="item"><code>${esc(k)}</code></div>`).join('')
    || empty('None'), { count: Object.keys(m.materials || {}).length || null });
  h += section('Landmarks', Object.keys(m.landmarks || {}).map(k => `<div class="item"><code>${esc(k)}</code>
      <span class="beat">${esc((m.landmarks[k].anchor) || 'position')}</span></div>`).join('') || empty('None'),
    { count: Object.keys(m.landmarks || {}).length || null });
  if (m.description) h += section('Description', `<div class="muted" style="font-size:12px">${esc(m.description)}</div>`, { open: true });
  p.innerHTML = h;
}

export function renderPreviews(studio) {
  const info = studio.info, latest = $('latest'), hist = $('history');
  if (!info) { latest.innerHTML = ''; hist.innerHTML = ''; return; }
  const byAngle = {};
  for (const p of info.previews) if (!byAngle[p.angle]) byAngle[p.angle] = p;
  latest.innerHTML = ['camera', 'top', 'three_quarter'].map(a => byAngle[a]
    ? `<figure><img src="/files/${byAngle[a].path}?t=${byAngle[a].mtime}" data-full="/files/${byAngle[a].path}">
         <figcaption>${a}</figcaption></figure>`
    : `<div class="empty">No ${a} preview yet</div>`).join('');
  const rest = info.previews.slice(3, 40);
  $('historyLabel').hidden = rest.length === 0;
  hist.innerHTML = rest.map(p => `<figure><img src="/files/${p.path}?t=${p.mtime}" data-full="/files/${p.path}" loading="lazy">
      <figcaption>${p.path.split('/').pop()}</figcaption></figure>`).join('');
}

export function renderRenders(studio) {
  const info = studio.info;
  $('renderSummary').textContent = info ? `${info.renders.count} frames on disk for ${info.shot_id}` : '';
  if (!info) return;
  const frames = info.renders.frames;
  $('renderFrames').innerHTML = frames.length
    ? frames.filter((_, i) => i % Math.max(1, Math.floor(frames.length / 60)) === 0)
        .map(f => `<figure><img src="/files/${f}" data-full="/files/${f}" loading="lazy">
           <figcaption>${f.split('/').pop()}</figcaption></figure>`).join('')
    : `<div class="empty">No final frames yet. Ask for a render, or run the render queue.</div>`;
}
