// Chat with Claude Code, streamed over SSE from /api/chat. Tool calls render as
// collapsible chips; any preview/render image a tool returns shows inline.
const esc = s => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

export class Chat {
  constructor(list, form, input, send, stop, status, quick, studio, onTurnEnd) {
    Object.assign(this, { list, form, input, send, stop, status, quick, studio, onTurnEnd, busy: false, tools: {} });
    form.addEventListener('submit', e => { e.preventDefault(); this.submit(input.value); });
    input.addEventListener('keydown', e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); this.submit(input.value); } });
    stop.addEventListener('click', () => fetch('/api/chat/stop', { method: 'POST' }));
    quick.querySelectorAll('button').forEach(b => b.addEventListener('click', () => this.submit(b.dataset.q.replace('{shot}', studio.shot || 'the current shot'))));
    this.add('note', 'Pick a shot or a model, then talk to the pipeline. Every human gate stops for you.');
  }

  add(kind, text) {
    const el = document.createElement('div');
    el.className = 'msg ' + kind; el.textContent = text;
    this.list.appendChild(el); this.scroll(); return el;
  }

  scroll() { this.list.scrollTop = this.list.scrollHeight; }

  reset() { this.list.innerHTML = ''; this.add('note', 'New conversation.'); }

  toolChip(ev) {
    const d = document.createElement('details');
    d.className = 'tool'; d.dataset.id = ev.id;
    const input = ev.input || {};
    const brief = Object.entries(input).map(([k, v]) => {
      const s = typeof v === 'string' ? v : JSON.stringify(v);
      return `${k}=${s.length > 28 ? s.slice(0, 28) + '…' : s}`;
    }).join(' ');
    d.innerHTML = `<summary><i class="state"></i><b>${esc(ev.name.replace('mcp__blendy__', ''))}</b>
      <span class="arg">${esc(brief)}</span></summary><pre></pre><div class="thumbs"></div>`;
    this.tools[ev.id] = d; this.list.appendChild(d); this.scroll();
  }

  toolResult(ev) {
    const d = this.tools[ev.tool_use_id]; if (!d) return;
    d.querySelector('pre').textContent = ev.text;
    d.classList.add(ev.is_error ? 'err' : 'done');
    const thumbs = d.querySelector('.thumbs');
    (ev.images || []).forEach(p => { const img = document.createElement('img'); img.src = '/files/' + p; img.dataset.full = '/files/' + p; thumbs.appendChild(img); });
    if (ev.images && ev.images.length) { d.open = true; this.onTurnEnd(); }
    this.scroll();
  }

  async attach() {
    // a turn started before this page load is still running: replay it
    const st = await fetch('/api/chat/state').then(r => r.json()).catch(() => null);
    if (!st || !st.running) return;
    this.add('note', 'Re-attached to a turn already in progress.');
    this.setBusy(true);
    await this.consume(fetch('/api/chat/attach', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ from: 0 }) }));
    this.setBusy(false);
    this.onTurnEnd();
  }

  async submit(text) {
    text = (text || '').trim();
    if (!text || this.busy) return;
    this.input.value = '';
    this.add('user', text);
    this.setBusy(true);
    await this.consume(fetch('/api/chat', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ message: text, shot: this.studio.shot }) }));
    this.setBusy(false);
    this.onTurnEnd();
  }

  async consume(promise) {
    let current = null;
    try {
      const r = await promise;
      if (!r.ok) { this.add('error', (await r.json().catch(() => ({}))).error || r.statusText); return; }
      const reader = r.body.getReader(); const dec = new TextDecoder(); let buf = '';
      while (true) {
        const { value, done } = await reader.read(); if (done) break;
        buf += dec.decode(value, { stream: true });
        let i;
        while ((i = buf.indexOf('\n\n')) >= 0) {
          const line = buf.slice(0, i).trim(); buf = buf.slice(i + 2);
          if (!line.startsWith('data: ')) continue;
          const ev = JSON.parse(line.slice(6));
          if (ev.type === 'delta') { if (!current) current = this.add('assistant', ''); current.textContent += ev.text; this.scroll(); }
          else if (ev.type === 'text') { if (current) { current.textContent = ev.text; current = null; } else this.add('assistant', ev.text); }
          else if (ev.type === 'tool_use') { current = null; this.toolChip(ev); this.status.textContent = ev.name.replace('mcp__blendy__', ''); }
          else if (ev.type === 'tool_result') { this.toolResult(ev); }
          else if (ev.type === 'error') { this.add('error', ev.text); }
          else if (ev.type === 'result') { this.status.textContent = ev.duration_ms ? `${(ev.duration_ms / 1000).toFixed(1)}s` : ''; }
          else if (ev.type === 'init') { this.status.textContent = 'thinking…'; }
        }
      }
    } catch (e) { this.add('error', e.message); }
  }

  setBusy(b) { this.busy = b; this.send.disabled = b; this.stop.hidden = !b; if (!b) this.status.textContent = ''; }
}
