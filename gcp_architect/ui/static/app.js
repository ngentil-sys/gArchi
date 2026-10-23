/* gArchi workspace front-end: engagements, customer brief, streaming chat, deliverables. */
(() => {
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => [...el.querySelectorAll(s)];
  const api = {
    async get(url) { const r = await fetch(url); if (!r.ok) throw new Error(await r.text()); return r.json(); },
    async send(method, url, body) {
      const r = await fetch(url, { method, headers: { 'Content-Type': 'application/json' }, body: body ? JSON.stringify(body) : undefined });
      if (!r.ok) throw new Error(await r.text()); return r.json();
    },
  };

  const state = { sessionId: null, dirty: false, streaming: false, abort: null };
  const els = {
    select: $('#sessionSelect'), form: $('#briefForm'), status: $('#briefStatus'), messages: $('#messages'),
    input: $('#input'), send: $('#send'), stop: $('#stop'), health: $('#health'),
    deckThumb: $('#deckThumb'), deckLink: $('#deckLink'), deckHint: $('#deckHint'), tfPath: $('#tfPath'),
  };

  // ---------------------------------------------------------------- markdown
  function md(text) {
    if (window.marked && window.DOMPurify) {
      marked.setOptions({ breaks: true, gfm: true });
      const html = marked.parse(text || '');
      return DOMPurify.sanitize(html, { ADD_ATTR: ['target'] }).replace(/<a /g, '<a target="_blank" rel="noopener" ');
    }
    const div = document.createElement('div'); div.textContent = text || ''; return div.innerHTML.replace(/\n/g, '<br>');
  }

  // ---------------------------------------------------------------- sessions
  async function loadSessions(selectId) {
    const list = await api.get('/api/sessions');
    els.select.innerHTML = '';
    for (const s of list) {
      const o = document.createElement('option');
      o.value = s.id; o.textContent = s.title + (s.deliverables.presentation_url ? '  ·  deck' : '') + (s.deliverables.terraform_path ? '  ·  tf' : '');
      els.select.appendChild(o);
    }
    if (!list.length) { const s = await api.send('POST', '/api/sessions', { title: 'New engagement' }); return loadSessions(s.id); }
    const id = selectId && list.some(s => s.id === selectId) ? selectId : list[0].id;
    els.select.value = id;
    await openSession(id);
  }

  async function openSession(id) {
    state.sessionId = id;
    const s = await api.get(`/api/sessions/${id}`);
    fillBrief(s.brief);
    renderDeliverables(s.deliverables);
    els.messages.innerHTML = '';
    if (!s.messages.length) addSystem('New engagement. Fill in the customer brief, save it, then ask the agent to design the architecture.');
    for (const m of s.messages) {
      if (m.type === 'text') addMessage(m.role, m.text);
      else if (m.type === 'tool_call') addTool(m.name, m.args, 'done');
      else if (m.type === 'tool_result') updateLastTool(m);
    }
    scrollBottom();
  }

  $('#newSession').onclick = async () => {
    const title = prompt('Engagement / customer name', 'New engagement'); if (title === null) return;
    const s = await api.send('POST', '/api/sessions', { title });
    await loadSessions(s.id);
  };
  $('#renameSession').onclick = async () => {
    const cur = els.select.selectedOptions[0]?.textContent.split('  ·')[0] || '';
    const title = prompt('Rename engagement', cur); if (!title) return;
    await api.send('PUT', `/api/sessions/${state.sessionId}/title`, { title });
    await loadSessions(state.sessionId);
  };
  $('#deleteSession').onclick = async () => {
    if (!confirm('Delete this engagement and its chat history? Decks and Terraform files are kept.')) return;
    await api.send('DELETE', `/api/sessions/${state.sessionId}`);
    await loadSessions();
  };
  els.select.onchange = () => openSession(els.select.value);

  // ---------------------------------------------------------------- brief
  function fillBrief(brief) {
    for (const el of els.form.elements) if (el.name) el.value = brief[el.name] ?? '';
    setDirty(false);
  }
  function readBrief() {
    const out = {}; for (const el of els.form.elements) if (el.name) out[el.name] = el.value; return out;
  }
  function setDirty(d) {
    state.dirty = d;
    els.status.textContent = d ? 'unsaved changes' : 'saved';
    els.status.className = 'status ' + (d ? 'dirty' : 'saved');
  }
  els.form.addEventListener('input', () => setDirty(true));
  async function saveBrief() {
    if (!state.dirty) return;
    await api.send('PUT', `/api/sessions/${state.sessionId}/brief`, readBrief());
    setDirty(false);
    const opt = els.select.selectedOptions[0]; const c = readBrief().customer.trim();
    if (opt && c) opt.textContent = c;
  }
  $('#saveBrief').onclick = (e) => { e.preventDefault(); saveBrief(); };
  $$('#briefTabs button').forEach(b => b.onclick = () => {
    $$('#briefTabs button').forEach(x => x.classList.toggle('active', x === b));
    $$('.tabpane').forEach(p => p.classList.toggle('active', p.dataset.tab === b.dataset.tab));
  });
  $$('.chip').forEach(c => c.onclick = () => { els.input.value = c.dataset.prompt; els.input.focus(); sendMessage(); });

  // ---------------------------------------------------------------- deliverables
  function renderDeliverables(d) {
    if (!d) return;
    if (d.presentation_url) {
      els.deckLink.href = d.presentation_url; els.deckLink.classList.remove('hidden');
      els.deckHint.textContent = 'Editable Google Slides · rebuilt in place on changes';
    }
    if (d.deck_thumbnail_url) {
      els.deckThumb.innerHTML = `<img src="${d.deck_thumbnail_url}" alt="deck thumbnail">`;
    }
    if (d.terraform_path) els.tfPath.textContent = d.terraform_path;
  }

  // ---------------------------------------------------------------- messages
  function addMessage(role, text, streaming = false) {
    const div = document.createElement('div');
    div.className = `msg ${role}` + (streaming ? ' typing' : '');
    if (role === 'user') div.textContent = text; else div.innerHTML = md(text);
    els.messages.appendChild(div); scrollBottom(); return div;
  }
  function addSystem(text) { const d = document.createElement('div'); d.className = 'msg system'; d.textContent = text; els.messages.appendChild(d); }
  function addError(text) { const d = document.createElement('div'); d.className = 'msg error'; d.textContent = text; els.messages.appendChild(d); scrollBottom(); }
  const TOOL_LABELS = {
    search_gcp_docs: 'Searching official docs', fetch_official_doc: 'Reading cloud.google.com', list_gcp_products: 'Browsing product catalog',
    search_gcp_products: 'Searching product catalog', get_gcp_product: 'Looking up product', get_architecture_spec_schema: 'Loading spec schema',
    validate_architecture_spec: 'Validating diagram layout', render_architecture_deck: 'Rendering Google Slides deck',
    get_last_architecture_spec: 'Loading current design', check_setup: 'Checking setup', get_terraform_resources: 'Mapping Terraform resources',
    fetch_terraform_resource_doc: 'Reading Terraform provider docs', scaffold_terraform_project: 'Scaffolding Terraform project',
    write_terraform_files: 'Writing Terraform + linting', lint_terraform_project: 'Linting Terraform', read_terraform_project: 'Reading Terraform project',
  };
  const tools = [];
  function addTool(name, args, status = 'running') {
    const div = document.createElement('div'); div.className = 'tool' + (status === 'done' ? ' done' : '');
    const arg = args && Object.keys(args).length ? Object.entries(args).map(([k, v]) => `${k}=${typeof v === 'string' ? v : JSON.stringify(v)}`).join(' · ') : '';
    div.innerHTML = `<span class="spin"></span><span class="label">${TOOL_LABELS[name] || name}</span>${arg ? `<span class="args" title="${arg.replace(/"/g, '&quot;')}">${arg}</span>` : ''}<span class="meta"></span>`;
    div.dataset.name = name; els.messages.appendChild(div); tools.push(div); scrollBottom(); return div;
  }
  function updateLastTool(r) {
    const div = [...tools].reverse().find(t => t.dataset.name === r.name && !t.classList.contains('done') && !t.classList.contains('fail')) || [...tools].reverse().find(t => t.dataset.name === r.name);
    if (!div) return;
    const ok = r.ok !== false && !r.error && r.valid !== false && r.found !== false;
    div.classList.add(ok ? 'done' : 'fail');
    const meta = [];
    if (r.url) meta.push('deck ready'); if (r.errors && typeof r.errors === 'number') meta.push(`${r.errors} lint errors`);
    if (typeof r.warnings === 'number') meta.push(`${r.warnings} warnings`); if (r.nodes) meta.push(`${r.nodes} nodes`);
    if (r.title) meta.push(r.title.slice(0, 50)); if (r.resource) meta.push(r.resource + (r.provider === 'google-beta' ? ' (beta)' : ''));
    if (r.error && typeof r.error === 'string') meta.push(r.error.slice(0, 80));
    $('.meta', div).textContent = meta.join(' · ');
  }
  function scrollBottom() { els.messages.scrollTop = els.messages.scrollHeight; }

  // ---------------------------------------------------------------- chat streaming
  async function sendMessage() {
    const text = els.input.value.trim();
    if (!text || state.streaming) return;
    if (state.dirty) await saveBrief();
    els.input.value = ''; addMessage('user', text);
    state.streaming = true; els.send.disabled = true; els.stop.classList.remove('hidden');
    const ctrl = new AbortController(); state.abort = ctrl;
    let bubble = null, buffer = '';
    const closeBubble = () => { if (bubble) { bubble.classList.remove('typing'); bubble = null; buffer = ''; } };
    try {
      const resp = await fetch(`/api/sessions/${state.sessionId}/chat`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ message: text }), signal: ctrl.signal,
      });
      if (!resp.ok) throw new Error(await resp.text());
      const reader = resp.body.getReader(); const dec = new TextDecoder(); let raw = '';
      while (true) {
        const { value, done } = await reader.read(); if (done) break;
        raw += dec.decode(value, { stream: true });
        let idx;
        while ((idx = raw.indexOf('\n\n')) >= 0) {
          const chunk = raw.slice(0, idx); raw = raw.slice(idx + 2);
          const ev = /^event: (.*)$/m.exec(chunk)?.[1]; const data = /^data: (.*)$/m.exec(chunk)?.[1];
          if (!ev) continue;
          handle(ev, data ? JSON.parse(data) : {});
        }
      }
    } catch (e) {
      if (e.name !== 'AbortError') addError(String(e.message || e));
    } finally {
      closeBubble(); state.streaming = false; els.send.disabled = false; els.stop.classList.add('hidden'); state.abort = null;
    }

    function handle(ev, d) {
      switch (ev) {
        case 'delta':
          if (!bubble) bubble = addMessage('agent', '', true);
          buffer += d.text; bubble.innerHTML = md(buffer); scrollBottom(); break;
        case 'message':
          if (bubble) { bubble.innerHTML = md(d.text); closeBubble(); } else addMessage('agent', d.text);
          break;
        case 'tool_call': closeBubble(); addTool(d.name, d.args); break;
        case 'tool_result': updateLastTool(d); break;
        case 'deliverable':
          renderDeliverables(d);
          api.send('POST', `/api/sessions/${state.sessionId}/deliverables`, { deck_thumbnail_url: d.deck_thumbnail_url, terraform_path: d.terraform_path }).catch(() => {});
          break;
        case 'state': renderDeliverables(d); break;
        case 'error': closeBubble(); addError(d.message); break;
        case 'done': closeBubble(); loadSessionsQuiet(); break;
      }
    }
  }
  async function loadSessionsQuiet() {
    try { const list = await api.get('/api/sessions'); for (const o of els.select.options) { const s = list.find(x => x.id === o.value); if (s) o.textContent = s.title + (s.deliverables.presentation_url ? '  ·  deck' : '') + (s.deliverables.terraform_path ? '  ·  tf' : ''); } } catch {}
  }
  $('#composer').onsubmit = (e) => { e.preventDefault(); sendMessage(); };
  els.input.addEventListener('keydown', (e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); } });
  els.stop.onclick = () => state.abort?.abort();

  // ---------------------------------------------------------------- boot
  (async () => {
    try {
      const h = await api.get('/api/health');
      els.health.textContent = `${h.model} · ${h.project || 'no project'} · icons ${h.icons ? 'on' : 'off'}`;
      els.health.className = 'health ' + (h.project ? 'ok' : 'bad');
    } catch { els.health.textContent = 'backend unreachable'; els.health.className = 'health bad'; }
    await loadSessions();
  })();
})();
