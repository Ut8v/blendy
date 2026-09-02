// Live activity: what the agents are doing right now, not just what they finished.
const $ = id => document.getElementById(id);
const esc = s => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const secs = n => n == null ? '' : (n < 60 ? `${n.toFixed(1)}s` : `${Math.floor(n / 60)}m ${Math.round(n % 60)}s`);

export function mountActivity(api, studio) {
  let last = { tools: [], jobs: [] };

  async function poll() {
    try { last = await api.get('/api/activity'); } catch { return; }
    paint();
  }

  function paint() {
    const { turn, current, tools, jobs } = last;
    const busy = turn.running || jobs.some(j => j.running);
    const bar = $('activityBar');
    bar.hidden = !busy;
    if (busy) {
      const job = jobs.find(j => j.running);
      const what = current ? `${current.name} ${current.brief ? '· ' + current.brief : ''}`
        : job ? job.label : 'thinking';
      const since = current && current.started ? (Date.now() / 1000 - current.started)
        : job ? job.seconds : turn.seconds;
      bar.innerHTML = `<i class="spinner"></i><span class="mono-xs">${esc(what.slice(0, 96))}</span>
                       <span class="muted mono-xs">${secs(since)}</span>`;
    }
    $('tabs').querySelector('[data-tab="activity"]').classList.toggle('busy', busy);
    if (studio.activeTab === 'activity') renderTimeline(tools, jobs, turn);
  }

  function renderTimeline(tools, jobs, turn) {
    const body = $('activityBody');
    const rows = [];
    for (const j of jobs) {
      rows.push({ t: j.started, kind: 'job', name: j.kind, brief: j.label, ok: j.ok,
                  running: j.running, seconds: j.seconds, error: j.error });
    }
    for (const t of tools) {
      rows.push({ t: t.started, kind: 'tool', name: t.name, brief: t.brief, ok: t.ok,
                  running: t.ended === null,
                  seconds: t.ended ? t.ended - t.started : (Date.now() / 1000 - t.started),
                  images: t.images || [] });
    }
    rows.sort((a, b) => (b.t || 0) - (a.t || 0));
    if (!rows.length) {
      body.innerHTML = `<div class="empty">Nothing running. Ask the pipeline for something in the conversation, or press a build button.</div>`;
      return;
    }
    const head = turn.running
      ? `<div class="act-turn"><i class="spinner"></i><div><div>Turn running · ${secs(turn.seconds)}</div>
           <div class="muted mono-xs">${esc((turn.message || '').slice(0, 150))}</div></div></div>`
      : '';
    body.innerHTML = head + rows.map(r => `
      <div class="act-row ${r.running ? 'run' : r.ok === false ? 'bad' : 'ok'}">
        <i class="state"></i>
        <code>${esc(r.name)}</code>
        <span class="beat">${esc(r.brief || '')}</span>
        ${r.error ? `<span class="badge badge-bad">${esc(String(r.error).slice(0, 60))}</span>` : ''}
        <span class="muted mono-xs">${secs(r.seconds)}</span>
      </div>` + (r.images && r.images.length
        ? `<div class="thumbs">${r.images.map(p => `<img src="/files/${p}" data-full="/files/${p}" loading="lazy">`).join('')}</div>`
        : '')).join('');
  }

  setInterval(poll, 1000);
  poll();
  return { poll, paint };
}

// Wait for a background job to finish, reporting progress into an element.
export async function watchJob(api, jobId, statusEl, label) {
  for (;;) {
    const list = await api.get('/api/jobs');
    const job = list.find(j => j.id === jobId);
    if (!job) return null;
    if (statusEl) statusEl.textContent = job.running ? `${label}… ${secs(job.seconds)}` : '';
    if (!job.running) return job;
    await new Promise(r => setTimeout(r, 700));
  }
}
