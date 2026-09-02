// Blendy Studio entry: overview, working shot, tabs, one-click actions, lightbox.
import { Chat } from './chat.js';
import { renderPanel, renderPreviews, renderRenders } from './panels.js';
import { mountDirector } from './director.js';
import { mountModels } from './models.js';

const $ = id => document.getElementById(id);
export const api = {
  get: async (url) => { const r = await fetch(url); const j = await r.json(); if (!r.ok) throw new Error(j.error || r.statusText); return j; },
  post: async (url, body) => { const r = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) }); const j = await r.json(); if (!r.ok) throw new Error(j.error || r.statusText); return j; },
};

export const studio = { shot: null, overview: null, info: null, director: null, chat: null,
  select: id => selectShot(id), refresh: () => refreshShot(), overviewRefresh: () => refreshOverview() };

export async function refreshOverview() {
  studio.overview = await api.get('/api/overview');
  const sel = $('shotSelect');
  const current = studio.shot || studio.overview.studio.shot || (studio.overview.shots[0] || {}).id;
  sel.innerHTML = studio.overview.shots.map(s => `<option value="${s.id}">${s.id} · ${s.kind} · ${s.render_state}</option>`).join('');
  if (current) sel.value = current;
  $('claudeStatus').textContent = studio.overview.claude ? 'claude: ready (subscription)' : 'claude: CLI not found';
  if (current && current !== studio.shot) await selectShot(current); else renderPanel(studio, api);
}

export async function selectShot(id) {
  studio.shot = id;
  $('shotSelect').value = id;
  try {
    studio.info = await api.get('/api/shot?id=' + encodeURIComponent(id));
    const v = studio.info.valid;
    $('shotState').textContent = v.ok ? `valid${v.warnings.length ? ` · ${v.warnings.length} warnings` : ''}` : `${v.errors.length} validation errors`;
    $('shotState').style.color = v.ok ? '' : 'var(--bad)';
  } catch (e) {
    studio.info = null; $('shotState').textContent = e.message;
  }
  renderPanel(studio, api);
  renderPreviews(studio);
  renderRenders(studio);
  if (studio.director) studio.director.load(studio.info);
}

export async function refreshShot() { if (studio.shot) await selectShot(studio.shot); }

async function runAction(action, args) {
  if (!studio.shot) return;
  const status = $('actionStatus');
  status.textContent = `${action}…`;
  try {
    const r = await api.post('/api/action', { shot: studio.shot, action, args: args || {} });
    status.textContent = r.ok === false ? `${action} failed at ${r.stage}${r.entity_id ? ' · ' + r.entity_id : ''}: ${(r.error || '').split('\n')[0]}`
      : (r.error ? r.error : `${action} ok${r.seconds ? ` (${r.seconds}s)` : ''}`);
    if (r.errors && r.errors.length) status.textContent = r.errors.map(e => `${e.code} @ ${e.entity_id || e.path}`).join('; ');
    if (action === 'export_proxy') $('directorStatus').textContent = r.path ? (r.cached ? 'proxy up to date' : 'proxy exported') : status.textContent;
  } catch (e) { status.textContent = e.message; }
  await refreshShot();
}

export function lightbox(src) {
  let lb = $('lightbox');
  if (!lb) { lb = document.createElement('div'); lb.id = 'lightbox'; lb.innerHTML = '<img>'; lb.onclick = () => lb.style.display = 'none'; document.body.appendChild(lb); }
  lb.querySelector('img').src = src; lb.style.display = 'flex';
}

function wire() {
  $('shotSelect').addEventListener('change', e => selectShot(e.target.value));
  document.querySelectorAll('#tabs button').forEach(b => b.addEventListener('click', () => {
    document.querySelectorAll('#tabs button').forEach(x => x.classList.toggle('active', x === b));
    document.querySelectorAll('.tab').forEach(t => t.classList.toggle('active', t.id === 'tab-' + b.dataset.tab));
    try {
      if (b.dataset.tab === 'models' && studio.models) studio.models.refresh();
      if (b.dataset.tab === 'director' && !studio.director) {
        studio.director = mountDirector($('director-view'), $('frame'), api, studio);
        studio.director.load(studio.info);
      }
      if (studio.director) studio.director.resize();
    } catch (e) { console.error('tab switch failed', e); }
  }));
  document.querySelectorAll('[data-action]').forEach(b => b.addEventListener('click', () => runAction(b.dataset.action, b.dataset.args ? JSON.parse(b.dataset.args) : {})));
  document.addEventListener('click', e => { if (e.target.matches('img[data-full]')) lightbox(e.target.dataset.full); });
  $('resetChat').addEventListener('click', async () => { await api.post('/api/chat/reset'); studio.chat.reset(); });
}

wire();
studio.chat = new Chat($('messages'), $('composer'), $('input'), $('send'), $('stop'), $('turnStatus'), $('quick'), studio, refreshShot);
studio.models = mountModels(api, studio);
studio.models.refresh().catch(e => console.error('models', e));
refreshOverview();
setInterval(() => { if (!studio.chat.busy) refreshOverview(); }, 15000);
