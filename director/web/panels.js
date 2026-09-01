// Right panel (bible, breakdown, checkpoints, takes) and the previews / renders tabs.
const $ = id => document.getElementById(id);
const esc = s => String(s).replace(/[&<>]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]));

export function renderPanel(studio, api) {
  const o = studio.overview, info = studio.info, p = $('panel');
  if (!o) return;
  let h = '';
  if (o.bible) h += `<h3>bible · ${esc(o.bible.title)}</h3><div class="item"><span class="beat">cast: ${o.bible.cast.join(', ')}<br>locations: ${o.bible.locations.join(', ')}<br>looks: ${o.bible.looks.join(', ')}</span></div>`;
  if (o.breakdown) {
    h += `<h3>breakdown <span class="badge ${o.breakdown.approved ? 'ok' : 'bad'}">${o.breakdown.approved ? 'approved' : 'NOT approved'}</span></h3>`;
    if (!o.breakdown.approved) h += `<div class="item"><button id="approveBd" class="primary">approve breakdown</button></div>`;
    h += o.breakdown.shots.map(s => `<div class="item" data-shot="${s.id}"><code>${s.id}</code><span class="beat">${esc(s.shot_type)} · ${s.duration_frames}f · ${esc(s.beat)}</span></div>`).join('');
  }
  h += `<h3>shots</h3>` + o.shots.map(s => `<div class="item" data-shot="${s.id}"><code>${s.id}</code><span class="beat">${s.frames}f</span><span class="badge ${s.render_state}">${s.render_state}${s.frames_done ? ' ' + s.frames_done : ''}</span></div>`).join('');
  if (info) {
    const v = info.valid;
    h += `<h3>validation</h3>` + (v.ok ? `<div class="item"><span class="badge ok">ok</span><span class="beat">${v.warnings.length} warnings</span></div>` : v.errors.slice(0, 8).map(e => `<div class="item"><span class="badge bad">${e.code}</span><span class="beat">${esc(e.entity_id || e.path)}: ${esc(e.message).slice(0, 140)}</span></div>`).join(''));
    if (info.resolved) h += `<h3>inherits</h3><div class="item"><span class="beat">look ${esc(info.resolved.look?.id)} · ${esc(info.resolved.location?.id)} · cast ${(info.resolved.cast || []).map(c => c?.id).join(', ')}</span></div>`;
    h += `<h3>checkpoints</h3><div class="item"><input id="cpLabel" placeholder="label" style="width:120px"><button id="cpSave">checkpoint</button></div>`;
    h += (info.checkpoints || []).slice().reverse().map(c => `<div class="item"><code>${esc(c.label)}</code><span class="beat">${new Date(c.created * 1000).toLocaleTimeString()}${c.blend ? ' · .blend' : ''}</span><button data-restore="${esc(c.label)}">restore</button></div>`).join('');
    h += `<h3>takes</h3>` + ((info.takes || []).map(t => `<div class="item"><code>${t.id.slice(5, 20)}</code><span class="beat">${t.mode} · ${t.samples}s${t.promoted_to ? ' → ' + esc(t.promoted_to) : ''}</span><button data-apply="${t.id}">apply</button><button data-promote="${t.id}" title="promote to camera preset">★</button></div>`).join('') || '<div class="item beat muted">none recorded</div>');
  }
  p.innerHTML = h;
  p.querySelectorAll('[data-shot]').forEach(el => el.addEventListener('click', () => studio.select(el.dataset.shot)));
  const act = (action, args) => api.post('/api/action', { shot: studio.shot, action, args }).then(() => studio.refresh()).catch(e => alert(e.message));
  p.querySelector('#approveBd')?.addEventListener('click', async () => { const r = await api.post('/api/breakdown/approve'); if (!r.approved) alert(JSON.stringify(r.errors, null, 1)); studio.overviewRefresh(); });
  p.querySelector('#cpSave')?.addEventListener('click', () => act('checkpoint', { label: p.querySelector('#cpLabel').value || 'step' }));
  p.querySelectorAll('[data-restore]').forEach(b => b.addEventListener('click', () => act('restore', { label: b.dataset.restore })));
  p.querySelectorAll('[data-apply]').forEach(b => b.addEventListener('click', () => act('apply_take', { take_id: b.dataset.apply })));
  p.querySelectorAll('[data-promote]').forEach(b => b.addEventListener('click', () => { const name = prompt('preset name (snake_case)'); if (name) act('promote_take', { take_id: b.dataset.promote, name, description: prompt('description') || '', shot_types: [] }); }));
}

export function renderPreviews(studio) {
  const info = studio.info, latest = $('latest'), hist = $('history');
  if (!info) { latest.innerHTML = ''; hist.innerHTML = ''; return; }
  const byAngle = {};
  for (const p of info.previews) if (!byAngle[p.angle]) byAngle[p.angle] = p;
  latest.innerHTML = ['camera', 'top', 'three_quarter'].map(a => byAngle[a]
    ? `<figure><img src="/files/${byAngle[a].path}?t=${byAngle[a].mtime}" data-full="/files/${byAngle[a].path}"><figcaption>${a} · ${byAngle[a].path.split('/').pop()}</figcaption></figure>`
    : `<figure><figcaption>${a}: no preview yet</figcaption></figure>`).join('');
  hist.innerHTML = info.previews.slice(3, 40).map(p => `<figure><img src="/files/${p.path}?t=${p.mtime}" data-full="/files/${p.path}" loading="lazy"><figcaption>${p.path.split('/').pop()}</figcaption></figure>`).join('');
}

export function renderRenders(studio) {
  const info = studio.info;
  $('renderSummary').textContent = info ? `${info.renders.count} frames on disk for ${info.shot_id}` : '';
  $('renderFrames').innerHTML = info ? info.renders.frames.filter((_, i) => i % Math.max(1, Math.floor(info.renders.frames.length / 60)) === 0).map(f => `<figure><img src="/files/${f}" data-full="/files/${f}" loading="lazy"><figcaption>${f.split('/').pop()}</figcaption></figure>`).join('') : '';
}
