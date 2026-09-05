/* JARVAS — CrossPCAI control centre
   One front-end for every install. When a remote machine is selected in the
   node switcher, every API call is proxied to it, so the same screens operate
   the Ubuntu box, the server or this laptop without a second app. */

'use strict';

const S = {
  boot: null,
  view: 'chat',
  node: 'local',
  sessionId: null,
  sessions: [],
  slackChannel: null,
  busy: false,
};

/* ── API ──────────────────────────────────────────────────────────────── */

async function api(path, { method = 'GET', body } = {}) {
  // Remote node: tunnel the same call through the local server.
  if (S.node !== 'local') {
    const r = await fetch(`/api/nodes/${S.node}/proxy`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ method, path, body }),
    });
    const wrapped = await r.json();
    if (!wrapped.ok) throw new Error(wrapped.error || 'remote call failed');
    return wrapped.data;
  }
  const opts = { method, headers: {} };
  if (body !== undefined) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }
  const r = await fetch(path, opts);
  const text = await r.text();
  let data;
  try { data = text ? JSON.parse(text) : {}; }
  catch { throw new Error(`Bad response from ${path}`); }
  if (!r.ok && data.error) throw new Error(data.error);
  return data;
}

/* ── DOM helpers ──────────────────────────────────────────────────────── */

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

function el(tag, attrs = {}, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === 'class') n.className = v;
    else if (k === 'html') n.innerHTML = v;
    else if (k.startsWith('on')) n.addEventListener(k.slice(2).toLowerCase(), v);
    else if (v !== null && v !== undefined && v !== false) n.setAttribute(k, v);
  }
  for (const kid of kids.flat()) {
    if (kid === null || kid === undefined || kid === false) continue;
    n.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  }
  return n;
}

const esc = (s) => String(s ?? '').replace(/[&<>"']/g,
  (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

function ago(ts) {
  if (!ts) return '';
  const d = Date.now() / 1000 - ts;
  if (d < 60) return 'just now';
  if (d < 3600) return `${Math.floor(d / 60)}m ago`;
  if (d < 86400) return `${Math.floor(d / 3600)}h ago`;
  return `${Math.floor(d / 86400)}d ago`;
}

function toast(title, detail = '', kind = '', actions = []) {
  const t = el('div', { class: `toast ${kind}` }, el('div', { class: 't' }, title));
  if (detail) t.append(el('div', { class: 'd' }, detail));
  if (actions.length) {
    const bar = el('div', { class: 'act' });
    for (const a of actions) {
      bar.append(el('button', {
        class: 'btn sm ' + (a.primary ? 'primary' : ''),
        onclick: () => { t.remove(); a.fn(); },
      }, a.label));
    }
    t.append(bar);
  }
  $('#toasts').append(t);
  setTimeout(() => t.remove(), actions.length ? 20000 : 6000);
}

/* ── Modal ────────────────────────────────────────────────────────────── */

function modal({ title, sub = '', fields = [], okLabel = 'Save', onOk }) {
  const bg = $('#modal');
  $('#modal-title').textContent = title;
  $('#modal-sub').textContent = sub;
  const body = $('#modal-body');
  body.innerHTML = '';

  for (const f of fields) {
    const wrap = el('div', { class: 'field' });
    if (f.type === 'checkbox') {
      const box = el('input', { type: 'checkbox', id: `f_${f.name}` });
      box.checked = !!f.value;
      wrap.append(el('label', { class: 'check' }, box,
        el('span', {}, el('div', {}, f.label),
          f.hint ? el('div', { class: 'hint' }, f.hint) : null)));
    } else {
      wrap.append(el('label', { for: `f_${f.name}` }, f.label));
      let input;
      if (f.type === 'textarea') {
        input = el('textarea', { id: `f_${f.name}`, placeholder: f.placeholder || '' });
        input.value = f.value || '';
      } else if (f.type === 'select') {
        input = el('select', { id: `f_${f.name}` },
          ...(f.options || []).map((o) => {
            const opt = el('option', { value: o.value }, o.label);
            if (o.value === f.value) opt.selected = true;
            return opt;
          }));
      } else {
        input = el('input', {
          type: f.type || 'text', id: `f_${f.name}`,
          placeholder: f.placeholder || '', value: f.value || '',
        });
      }
      wrap.append(input);
      if (f.hint) wrap.append(el('div', { class: 'hint' }, f.hint));
    }
    body.append(wrap);
  }

  $('#modal-ok').textContent = okLabel;
  bg.classList.add('open');

  const close = () => {
    bg.classList.remove('open');
    $('#modal-ok').onclick = null;
    $('#modal-cancel').onclick = null;
  };
  $('#modal-cancel').onclick = close;
  $('#modal-ok').onclick = async () => {
    const values = {};
    for (const f of fields) {
      const node = $(`#f_${f.name}`);
      values[f.name] = f.type === 'checkbox' ? node.checked : node.value;
    }
    try {
      const keep = await onOk(values);
      if (keep !== false) close();
    } catch (e) {
      toast('That did not work', e.message, 'err');
    }
  };
  const first = body.querySelector('input, textarea, select');
  if (first) first.focus();
}

/* ── Routing ──────────────────────────────────────────────────────────── */

const LOADERS = {
  chat: loadChat, agents: loadAgents, sandbox: loadSandbox, slack: loadSlack,
  connectors: loadConnectors, tools: loadTools, nodes: loadNodes,
  reports: loadNeeds, system: loadSystem, settings: loadSettings,
};

function go(view) {
  S.view = view;
  $$('#nav button').forEach((b) => b.classList.toggle('active', b.dataset.view === view));
  $$('.view').forEach((v) => v.classList.toggle('active', v.dataset.view === view));
  $('#rail-title').textContent = view === 'chat' ? 'Sessions' : 'Recent activity';
  $('#rail-new').hidden = view !== 'chat';
  location.hash = `#/${view}`;
  (LOADERS[view] || (() => {}))();
  if (view !== 'chat') loadRailActivity();
}

/* ── Chat ─────────────────────────────────────────────────────────────── */

function renderMarkdownish(text) {
  // Deliberately minimal: fenced code and inline code only. Everything else
  // stays literal so a model cannot inject markup into the window.
  const parts = String(text).split(/```/);
  const frag = document.createDocumentFragment();
  parts.forEach((chunk, i) => {
    if (i % 2 === 1) {
      const body = chunk.replace(/^\w*\n/, '');
      frag.append(el('pre', {}, el('code', {}, body)));
    } else {
      const div = el('div', { class: 'body' });
      div.innerHTML = esc(chunk).replace(/`([^`]+)`/g, '<code>$1</code>');
      frag.append(div);
    }
  });
  return frag;
}

function addMessage(role, content, actions) {
  const log = $('#chat-log');
  const msg = el('div', { class: `msg ${role}` },
    el('div', { class: 'who' }, role === 'user' ? 'YOU' : '◆'),
    el('div', { class: 'bubble' }));
  const bubble = msg.querySelector('.bubble');
  bubble.append(renderMarkdownish(content));

  for (const a of actions || []) {
    const ok = a.result && a.result.ok;
    const note = el('div', { class: 'action-note' },
      el('div', { class: 'head' }, `${ok ? '✓' : '✕'} ${a.call.tool}`));
    if (a.result && a.result.error) note.append(el('div', {}, a.result.error));
    if (a.result && a.result.stdout) note.append(el('pre', {}, a.result.stdout.slice(0, 1500)));
    // A missing tool is an offer, not a dead end.
    if (a.result && a.result.report_id) {
      note.append(el('div', { class: 'act', style: 'margin-top:8px' },
        el('button', {
          class: 'btn sm',
          onclick: () => sendReport(a.result.report_id),
        }, 'Send report asking for this')));
    }
    bubble.append(note);
  }
  log.append(msg);
  log.scrollTop = log.scrollHeight;
  return msg;
}

async function loadChat() {
  const { sessions } = await api('/api/sessions');
  S.sessions = sessions;
  renderRailSessions();
  if (!S.sessionId && sessions.length) S.sessionId = sessions[0].id;
  await openSession(S.sessionId);
}

function renderRailSessions() {
  if (S.view !== 'chat') return;
  const list = $('#rail-list');
  list.innerHTML = '';
  if (!S.sessions.length) {
    list.append(el('div', { class: 'rail-item dim' }, 'No sessions yet'));
    return;
  }
  for (const s of S.sessions) {
    list.append(el('div', {
      class: 'rail-item' + (s.id === S.sessionId ? ' active' : ''),
      onclick: () => openSession(s.id),
    }, el('div', {}, s.title), el('div', { class: 'when' }, `${s.n || 0} messages · ${ago(s.updated_at)}`)));
  }
}

async function openSession(sid) {
  const log = $('#chat-log');
  log.innerHTML = '';
  S.sessionId = sid;
  renderRailSessions();
  if (!sid) {
    log.append(el('div', { class: 'empty' },
      el('div', { class: 'big' }, '◆'),
      el('div', {}, 'Ask JARVAS anything, or tell it what to do.'),
      el('div', { class: 'dim', style: 'margin-top:6px;font-size:12.5px' },
        'It can queue work on Hermes, run commands in the sandbox and post to Slack.')));
    return;
  }
  const { messages } = await api(`/api/sessions/${sid}/messages`);
  for (const m of messages) {
    addMessage(m.role, m.content, m.meta && m.meta.actions);
  }
}

async function sendChat() {
  const input = $('#chat-input');
  const text = input.value.trim();
  if (!text || S.busy) return;
  input.value = '';
  input.style.height = 'auto';
  addMessage('user', text);

  S.busy = true;
  $('#orb').classList.add('busy');
  const pending = addMessage('assistant', '…');

  try {
    const res = await api('/api/chat', {
      method: 'POST', body: { session_id: S.sessionId, message: text },
    });
    S.sessionId = res.session_id;
    pending.remove();
    addMessage('assistant', res.reply || '(no reply)', res.actions);
    const { sessions } = await api('/api/sessions');
    S.sessions = sessions;
    renderRailSessions();
  } catch (e) {
    pending.remove();
    addMessage('assistant', `I could not reach the model: ${e.message}`);
  } finally {
    S.busy = false;
    $('#orb').classList.remove('busy');
  }
}

/* ── Agents ───────────────────────────────────────────────────────────── */

async function loadAgents() {
  const { agents } = await api('/api/agents');
  const grid = $('#agents-grid');
  grid.innerHTML = '';
  if (!agents.length) {
    grid.append(el('div', { class: 'empty' }, el('div', { class: 'big' }, '⬡'),
      'No agents yet — create one to get started.'));
    return;
  }
  for (const a of agents) {
    grid.append(el('div', { class: 'card hover' },
      el('div', { class: 'row-between' },
        el('h3', {}, a.name),
        el('span', { class: 'tag ' + (a.enabled ? 'green' : 'grey') },
          a.enabled ? 'active' : 'paused')),
      el('div', { class: 'meta' }, a.role || 'No role set'),
      el('div', { class: 'meta', style: 'margin-top:6px' },
        `${a.run_count || 0} runs${a.last_run ? ' · last ' + ago(a.last_run) : ''}`),
      el('div', { class: 'actions' },
        el('button', { class: 'btn sm primary', onclick: () => runAgent(a) }, 'Run'),
        el('button', { class: 'btn sm', onclick: () => editAgent(a) }, 'Edit'),
        a.builtin ? null : el('button', {
          class: 'btn sm danger',
          onclick: async () => { await api(`/api/agents/${a.id}`, { method: 'DELETE' }); loadAgents(); },
        }, 'Delete'))));
  }
}

function runAgent(a) {
  modal({
    title: `Run ${a.name}`,
    sub: 'This is queued on Hermes and runs in the background.',
    fields: [{ name: 'input', type: 'textarea', label: 'What should it do?',
               placeholder: 'Summarise yesterday and flag anything that failed' }],
    okLabel: 'Run',
    onOk: async (v) => {
      const res = await api(`/api/agents/${a.id}/run`, { method: 'POST', body: { input: v.input } });
      if (res.ok) toast('Queued', `${a.name} is working on it.`, 'ok');
      else toast('Could not queue that', res.error || '', 'err');
      loadAgents();
    },
  });
}

function editAgent(a) {
  modal({
    title: a ? `Edit ${a.name}` : 'New agent',
    sub: 'An agent is a name, a brief, and the connectors it may use.',
    fields: [
      { name: 'name', label: 'Name', value: a?.name || '', placeholder: 'Invoice Chaser' },
      { name: 'role', label: 'What it does', value: a?.role || '',
        placeholder: 'Chases unpaid invoices weekly' },
      { name: 'prompt', type: 'textarea', label: 'Standing brief', value: a?.prompt || '',
        placeholder: 'You chase unpaid invoices. Be polite, never threaten…' },
      { name: 'schedule', label: 'Schedule (optional)', value: a?.schedule || '',
        placeholder: 'daily 09:00', hint: 'Leave blank to run it by hand.' },
    ],
    okLabel: a ? 'Save' : 'Create',
    onOk: async (v) => {
      if (!v.name.trim()) { toast('Name required', '', 'err'); return false; }
      if (a) await api(`/api/agents/${a.id}`, { method: 'PATCH', body: v });
      else await api('/api/agents', { method: 'POST', body: v });
      loadAgents();
      refreshBadges();
    },
  });
}

/* ── Sandbox ──────────────────────────────────────────────────────────── */

function termLine(cls, text) {
  const t = $('#sb-term');
  t.append(el('div', { class: cls }, text));
  t.scrollTop = t.scrollHeight;
}

async function runSandbox() {
  const input = $('#sb-cmd');
  const cmd = input.value.trim();
  if (!cmd) return;
  input.value = '';
  termLine('cmd', `$ ${cmd}`);
  try {
    const r = await api('/api/sandbox/exec', { method: 'POST', body: { cmd } });
    if (r.stdout) termLine('', r.stdout.trimEnd());
    if (r.stderr) termLine('err', r.stderr.trimEnd());
    if (!r.stdout && !r.stderr) termLine('dim', `(exit ${r.code})`);
    loadSandboxFiles();
  } catch (e) {
    termLine('err', e.message);
  }
}

async function loadSandboxFiles() {
  try {
    const { files } = await api('/api/sandbox/files');
    const box = $('#sb-files');
    box.innerHTML = '';
    if (!files || !files.length) {
      box.append(el('div', { class: 'dim' }, 'No files yet.'));
      return;
    }
    for (const f of files) {
      box.append(el('div', {
        class: 'rail-item mono',
        onclick: async () => {
          const r = await api(`/api/sandbox/read?name=${encodeURIComponent(f.name)}`);
          if (r.ok) {
            termLine('dim', `── ${f.name} ──`);
            termLine('', r.content.slice(0, 4000));
          } else toast('Could not read that', r.error || '', 'err');
        },
      }, `${f.name}  `, el('span', { class: 'dim' }, `${f.size} B`)));
    }
  } catch (e) {
    $('#sb-files').innerHTML = `<div class="dim">${esc(e.message)}</div>`;
  }
}

function loadSandbox() { loadSandboxFiles(); }

/* ── Slack ────────────────────────────────────────────────────────────── */

async function loadSlack() {
  const box = $('#slack-container');
  let res;
  try { res = await api('/api/slack/channels'); }
  catch (e) { res = { ok: false, error: e.message }; }

  if (!res.ok) {
    box.innerHTML = '';
    box.append(el('div', { class: 'empty' },
      el('div', { class: 'big' }, '◈'),
      el('div', {}, res.error || 'Slack is not connected.'),
      el('div', { style: 'margin-top:14px' },
        el('button', { class: 'btn primary', onclick: connectSlack }, 'Connect Slack'))));
    return;
  }

  box.innerHTML = '';
  const chans = el('div', { class: 'slack-chans' });
  const feed = el('div', { class: 'slack-feed' });
  const input = el('input', { type: 'text', placeholder: 'Message…' });
  const sendBtn = el('button', { class: 'btn primary' }, 'Send');

  const send = async () => {
    const text = input.value.trim();
    if (!text || !S.slackChannel) return;
    sendBtn.disabled = true;
    try {
      const r = await api('/api/slack/send', {
        method: 'POST', body: { channel: S.slackChannel, text },
      });
      if (r.ok) { input.value = ''; openChannel(S.slackChannel); }
      else toast('Slack refused that', r.error || '', 'err');
    } finally { sendBtn.disabled = false; }
  };
  sendBtn.onclick = send;
  input.onkeydown = (e) => { if (e.key === 'Enter') send(); };

  async function openChannel(id) {
    S.slackChannel = id;
    $$('.rail-item', chans).forEach((n) => n.classList.toggle('active', n.dataset.id === id));
    feed.innerHTML = '<div class="dim">Loading…</div>';
    const r = await api(`/api/slack/history?channel=${encodeURIComponent(id)}`);
    feed.innerHTML = '';
    if (!r.ok || !r.messages.length) {
      feed.append(el('div', { class: 'dim' }, 'Nothing here yet.'));
      return;
    }
    for (const m of r.messages) {
      feed.append(el('div', { class: 'slack-msg' },
        el('div', {}, el('span', { class: 'from' }, m.user),
          el('span', { class: 'at' }, ago(m.time))),
        el('div', { class: 'text' }, m.text)));
    }
    feed.scrollTop = feed.scrollHeight;
  }

  for (const c of res.channels) {
    chans.append(el('div', {
      class: 'rail-item', 'data-id': c.id, onclick: () => openChannel(c.id),
    }, `#${c.name}`, c.is_member ? '' : el('span', { class: 'dim' }, ' · not joined')));
  }

  box.append(el('div', { class: 'slack-wrap' }, chans,
    el('div', { class: 'slack-msgs' }, feed,
      el('div', { class: 'slack-send' }, input, sendBtn))));

  if (res.channels.length) openChannel(S.slackChannel || res.channels[0].id);
}

function connectSlack() {
  modal({
    title: 'Connect Slack',
    sub: 'Create a Slack app, add a bot token, and paste it here.',
    fields: [
      { name: 'bot_token', label: 'Bot token', placeholder: 'xoxb-…',
        hint: 'Scopes: channels:read, channels:history, chat:write, users:read' },
      { name: 'default_channel', label: 'Default channel (optional)', placeholder: '#ai-ops-log' },
    ],
    okLabel: 'Connect',
    onOk: async (v) => {
      const test = await api('/api/setup/test', {
        method: 'POST', body: { what: 'slack', token: v.bot_token },
      });
      if (!test.ok) { toast('Slack rejected that token', test.error || '', 'err'); return false; }
      await api('/api/settings', { method: 'POST', body: { slack: { ...v, enabled: true } } });
      toast('Slack connected', `Workspace: ${test.team}`, 'ok');
      loadSlack();
      refreshStatus();
    },
  });
}

/* ── Connectors ───────────────────────────────────────────────────────── */

async function loadConnectors() {
  const [cat, mine] = await Promise.all([
    api('/api/catalog'),
    api('/api/connectors'),
  ]);

  const box = $('#conn-catalog');
  box.innerHTML = '';

  // A one-line honest summary beats a wall of logos.
  const c = cat.counts;
  box.append(el('div', { class: 'meta', style: 'margin-bottom:16px' },
    `${c.total} connectors · ${c.native} built in · ${c.template} ready to wire `
    + `· ${c.planned} not available yet`));

  const filter = el('input', {
    type: 'text', placeholder: 'Filter connectors…',
    style: 'margin-bottom:20px; max-width:340px',
  });
  filter.oninput = () => {
    const q = filter.value.trim().toLowerCase();
    $$('.cat-card', box).forEach((n) => {
      n.hidden = !!q && !n.dataset.search.includes(q);
    });
    $$('.cat-group', box).forEach((g) => {
      g.hidden = !$$('.cat-card', g).some((n) => !n.hidden);
    });
  };
  box.append(filter);

  for (const group of cat.groups) {
    const grid = el('div', { class: 'grid' });
    const section = el('div', { class: 'cat-group', style: 'margin-bottom:26px' },
      el('div', { style: 'margin-bottom:10px' },
        el('h3', { style: 'font-size:13px;letter-spacing:1.2px;text-transform:uppercase;color:var(--muted)' },
          group.label),
        el('div', { class: 'meta' }, group.description)),
      grid);

    for (const item of group.connectors) {
      grid.append(connectorCard(item));
    }
    box.append(section);
  }

  // Connectors this customer actually wired up.
  const custom = $('#conn-custom');
  custom.innerHTML = '';
  if (!mine.custom.length) {
    custom.append(el('div', { class: 'empty' },
      'Nothing wired yet. Add one above, or build a custom one.'));
  }
  for (const c2 of mine.custom) {
    custom.append(el('div', { class: 'card hover' },
      el('div', { class: 'row-between' }, el('h3', {}, c2.name),
        el('span', { class: 'tag ' + (c2.enabled ? '' : 'grey') }, c2.kind)),
      el('div', { class: 'meta mono' }, c2.base_url || '—'),
      el('div', { class: 'actions' },
        el('button', {
          class: 'btn sm', onclick: async () => {
            const r = await api(`/api/connectors/${c2.id}/test`, { method: 'POST' });
            toast(r.ok ? 'Connector works' : 'Test failed',
                  r.ok ? '' : (r.error || ''), r.ok ? 'ok' : 'err');
          },
        }, 'Test'),
        el('button', { class: 'btn sm', onclick: () => editConnector(c2) }, 'Edit'),
        el('button', {
          class: 'btn sm danger', onclick: async () => {
            await api(`/api/connectors/${c2.id}`, { method: 'DELETE' });
            loadConnectors();
          },
        }, 'Delete'))));
  }
}

const STATUS_TAG = {
  native: ['green', 'built in'],
  template: ['', 'ready to wire'],
  planned: ['grey', 'not yet'],
};

function connectorCard(item) {
  const [cls, label] = STATUS_TAG[item.status] || ['grey', item.status];
  const card = el('div', {
    class: 'card hover cat-card',
    'data-search': `${item.name} ${item.description} ${item.category}`.toLowerCase(),
  },
    el('div', { class: 'row-between' },
      el('h3', {}, item.name),
      el('span', { class: 'tag ' + (item.healthy ? 'green' : cls) },
        item.healthy ? 'connected' : label)),
    el('div', { class: 'meta' }, item.description));

  if (item.note) card.append(el('div', { class: 'meta', style: 'margin-top:6px' }, item.note));
  if (item.auth && item.auth.type !== 'none') {
    card.append(el('div', { class: 'meta', style: 'margin-top:6px' },
      `Needs: ${item.auth.label}`));
  }

  const actions = el('div', { class: 'actions' });
  if (item.status === 'template') {
    actions.append(el('button', {
      class: 'btn sm primary', onclick: () => addFromCatalog(item),
    }, 'Add'));
  } else if (item.status === 'planned') {
    actions.append(el('button', {
      class: 'btn sm', onclick: () => requestFromCatalog(item),
    }, 'Request it'));
  } else if (item.id === 'slack' && !item.configured) {
    actions.append(el('button', { class: 'btn sm primary', onclick: connectSlack }, 'Connect'));
  } else if (['anthropic', 'openai', 'ollama'].includes(item.id)) {
    actions.append(el('button', {
      class: 'btn sm', onclick: () => go('settings'),
    }, item.configured ? 'Settings' : 'Set up'));
  }
  if (item.docs) {
    actions.append(el('a', {
      class: 'btn sm', href: item.docs, target: '_blank', rel: 'noopener noreferrer',
    }, 'Docs'));
  }
  if (actions.children.length) card.append(actions);
  return card;
}

function addFromCatalog(item) {
  modal({
    title: `Add ${item.name}`,
    sub: 'The address and auth style are filled in. You supply the credential.',
    fields: [
      { name: 'base_url', label: 'Base URL', value: item.base_url,
        hint: item.base_url.includes('your-')
          ? 'Replace the placeholder with your own address.' : '' },
      { name: 'auth_value', type: 'password',
        label: item.auth.label,
        hint: item.note || 'Stored on this machine only. Never included in reports.' },
    ],
    okLabel: 'Add connector',
    onOk: async (v) => {
      const r = await api(`/api/catalog/${item.id}/add`, { method: 'POST', body: v });
      if (!r.ok) { toast('Could not add it', r.error || '', 'err'); return false; }
      toast(`${item.name} added`, 'Use Test to check the credential.', 'ok');
      loadConnectors();
    },
  });
}

function requestFromCatalog(item) {
  modal({
    title: `Request ${item.name}`,
    sub: 'Saved on this machine. Nothing is sent until you press Send report.',
    fields: [
      { name: 'reason', type: 'textarea', label: 'What would you use it for?',
        placeholder: item.description },
    ],
    okLabel: 'Save request',
    onOk: async (v) => {
      const r = await api(`/api/catalog/${item.id}/request`, { method: 'POST', body: v });
      toast('Saved', r.note || '', 'ok', [
        { label: 'Send report now', primary: true, fn: () => sendReport(r.id) },
      ]);
      refreshBadges();
    },
  });
}

function editConnector(c) {
  modal({
    title: c ? `Edit ${c.name}` : 'Add connector',
    sub: 'Point JARVAS at any HTTP API. Your agents can then use it.',
    fields: [
      { name: 'name', label: 'Name', value: c?.name || '', placeholder: 'Shopify' },
      { name: 'base_url', label: 'Base URL', value: c?.base_url || '',
        placeholder: 'https://api.example.com/v1' },
      { name: 'auth_type', type: 'select', label: 'Authentication',
        value: c?.auth_type || 'none',
        options: [
          { value: 'none', label: 'None' },
          { value: 'bearer', label: 'Bearer token' },
          { value: 'header', label: 'Custom header (Name: value)' },
          { value: 'query', label: 'Query string (key=value)' },
        ] },
      { name: 'auth_value', type: 'password', label: 'Secret', value: '',
        placeholder: c?.has_auth ? 'unchanged' : '',
        hint: 'Stored on this machine only. It is never included in reports.' },
      { name: 'test_path', label: 'Test path (optional)', value: c?.test_path || '',
        placeholder: '/ping' },
    ],
    okLabel: c ? 'Save' : 'Add',
    onOk: async (v) => {
      if (!v.name.trim()) { toast('Name required', '', 'err'); return false; }
      if (c) await api(`/api/connectors/${c.id}`, { method: 'PATCH', body: v });
      else await api('/api/connectors', { method: 'POST', body: v });
      loadConnectors();
    },
  });
}

/* ── Tools ────────────────────────────────────────────────────────────── */

async function loadTools() {
  const { tools } = await api('/api/tools');
  const grid = $('#tools-grid');
  grid.innerHTML = '';
  if (!tools.length) {
    grid.append(el('div', { class: 'empty' }, el('div', { class: 'big' }, '⚙'),
      'No custom tools yet. Build one, or ask us to.'));
    return;
  }
  for (const t of tools) {
    grid.append(el('div', { class: 'card hover' },
      el('div', { class: 'row-between' }, el('h3', {}, t.name),
        el('span', { class: 'tag' }, t.kind)),
      el('div', { class: 'meta' }, t.description || '—'),
      t.variables.length
        ? el('div', { class: 'meta mono', style: 'margin-top:6px' },
            `takes: ${t.variables.join(', ')}`)
        : null,
      el('div', { class: 'meta', style: 'margin-top:6px' }, `${t.call_count} calls`),
      el('div', { class: 'actions' },
        el('button', { class: 'btn sm', onclick: () => editTool(t) }, 'Edit'),
        el('button', {
          class: 'btn sm danger', onclick: async () => {
            await api(`/api/tools/${t.id}`, { method: 'DELETE' }); loadTools();
          },
        }, 'Delete'))));
  }
}

function editTool(t) {
  const spec = t?.spec || {};
  modal({
    title: t ? `Edit ${t.name}` : 'New tool',
    sub: 'A named action your agents can call. Use {braces} for arguments.',
    fields: [
      { name: 'name', label: 'Name', value: t?.name || '', placeholder: 'lookup_order' },
      { name: 'description', label: 'What it does', value: t?.description || '' },
      { name: 'kind', type: 'select', label: 'Kind', value: t?.kind || 'http',
        options: [
          { value: 'http', label: 'HTTP request' },
          { value: 'shell', label: 'Shell command (sandbox)' },
          { value: 'prompt', label: 'Prompt macro' },
        ] },
      { name: 'spec', type: 'textarea', label: 'Definition (JSON)',
        value: JSON.stringify(spec, null, 2) === '{}'
          ? '{\n  "method": "GET",\n  "url": "https://api.example.com/orders/{order_id}"\n}'
          : JSON.stringify(spec, null, 2),
        hint: 'http: method, url or path, body · shell: command · prompt: template' },
    ],
    okLabel: t ? 'Save' : 'Create',
    onOk: async (v) => {
      let spec;
      try { spec = JSON.parse(v.spec || '{}'); }
      catch { toast('That JSON is not valid', '', 'err'); return false; }
      const body = { name: v.name, description: v.description, kind: v.kind, spec };
      const r = t
        ? await api(`/api/tools/${t.id}`, { method: 'PATCH', body })
        : await api('/api/tools', { method: 'POST', body });
      if (r.ok === false) { toast('Could not save', r.error || '', 'err'); return false; }
      loadTools();
    },
  });
}

/* ── Reports (optional sends) ─────────────────────────────────────────── */

function requestThing(kind) {
  const label = { connector: 'connector', tool: 'tool', integration: 'app integration' }[kind];
  modal({
    title: `Request a ${label}`,
    sub: 'Saved on this machine. Nothing is sent until you press Send report.',
    fields: [
      { name: 'name', label: 'What do you need?', placeholder: kind === 'connector' ? 'QuickBooks' : 'send_invoice' },
      { name: 'category', label: 'Category (optional)', placeholder: 'accounting' },
      { name: 'reason', type: 'textarea', label: 'What would you use it for?',
        placeholder: 'So the invoice agent can pull unpaid bills automatically.' },
    ],
    okLabel: 'Save request',
    onOk: async (v) => {
      if (!v.name.trim()) { toast('Give it a name', '', 'err'); return false; }
      const r = await api('/api/needs', { method: 'POST', body: { kind, ...v } });
      toast('Saved', r.note || '', 'ok', [
        { label: 'Send report now', primary: true, fn: () => sendReport(r.id) },
      ]);
      refreshBadges();
      if (S.view === 'reports') loadNeeds();
    },
  });
}

async function sendReport(id) {
  const preview = await api(`/api/needs/${id}/preview`);
  if (!preview.ok) { toast('Nothing to send', preview.error || '', 'err'); return; }
  modal({
    title: 'Send this report?',
    sub: 'This is exactly what leaves your machine. Nothing else is included.',
    fields: [{
      name: 'payload', type: 'textarea', label: 'Report contents',
      value: JSON.stringify(preview.payload, null, 2),
      hint: 'Read-only preview — it is sent as shown.',
    }],
    okLabel: 'Send report',
    onOk: async () => {
      const r = await api(`/api/needs/${id}/send`, { method: 'POST' });
      if (r.ok) { toast('Sent', 'Thanks — this goes straight to the build queue.', 'ok'); }
      else if (r.needs_optin) {
        toast('Reporting is off', r.error, 'err', [
          { label: 'Open privacy settings', primary: true, fn: () => go('settings') },
        ]);
      } else toast('Could not send', r.error || '', 'err');
      refreshBadges();
      if (S.view === 'reports') loadNeeds();
    },
  });
}

async function loadNeeds() {
  const { needs, can_send } = await api('/api/needs');
  $('#needs-consent').hidden = can_send;
  const box = $('#needs-list');
  box.innerHTML = '';
  if (!needs.length) {
    box.append(el('div', { class: 'empty' }, el('div', { class: 'big' }, '↑'),
      'Nothing outstanding. When JARVAS is missing something, it lands here.'));
    return;
  }
  for (const n of needs) {
    box.append(el('div', { class: 'card' },
      el('div', { class: 'row-between' },
        el('div', {}, el('h3', {}, n.name),
          el('div', { class: 'meta' },
            `${n.kind}${n.category ? ' · ' + n.category : ''}${n.hits > 1 ? ' · asked ' + n.hits + ' times' : ''}`),
          n.reason ? el('div', { class: 'meta', style: 'margin-top:5px' }, n.reason) : null),
        el('div', { class: 'row' },
          n.sent
            ? el('span', { class: 'tag green' }, 'sent')
            : el('button', { class: 'btn sm primary', onclick: () => sendReport(n.id) }, 'Send report'),
          el('button', {
            class: 'btn sm ghost', onclick: async () => {
              await api(`/api/needs/${n.id}`, { method: 'DELETE' });
              loadNeeds(); refreshBadges();
            },
          }, '✕')))));
  }
}

/* ── Machines ─────────────────────────────────────────────────────────── */

async function loadNodes() {
  const { nodes } = await api('/api/nodes');
  const grid = $('#nodes-grid');
  grid.innerHTML = '';
  for (const n of nodes) {
    grid.append(el('div', { class: 'card hover' },
      el('div', { class: 'row-between' }, el('h3', {}, n.name),
        el('span', { class: 'tag ' + (n.online ? 'green' : 'red') },
          n.online ? 'online' : 'offline')),
      el('div', { class: 'meta mono' }, n.url),
      el('div', { class: 'meta' }, `${n.role}${n.platform ? ' · ' + n.platform : ''}`),
      n.error ? el('div', { class: 'meta', style: 'color:var(--red)' }, n.error) : null,
      el('div', { class: 'actions' },
        el('button', {
          class: 'btn sm', onclick: () => { S.node = n.id; syncNodeSwitch(); go('system'); },
        }, 'Operate'),
        n.is_local ? null : el('button', {
          class: 'btn sm danger', onclick: async () => {
            await api(`/api/nodes/${n.id}`, { method: 'DELETE' });
            if (S.node === n.id) S.node = 'local';
            loadNodes(); syncNodeSwitch();
          },
        }, 'Unpair'))));
  }
  syncNodeSwitch(nodes);
}

function syncNodeSwitch(list) {
  const sel = $('#node-switch');
  if (list) {
    sel.innerHTML = '';
    for (const n of list) {
      sel.append(el('option', { value: n.id }, n.is_local ? 'This machine' : n.name));
    }
  }
  sel.value = S.node;
}

function pairNode() {
  modal({
    title: 'Pair a machine',
    sub: 'Install JARVAS there, then point this one at it.',
    fields: [
      { name: 'name', label: 'Name', placeholder: 'Ubuntu box' },
      { name: 'url', label: 'Address', placeholder: '192.168.1.50:5580',
        hint: 'The other machine must be running with --server, or have its UI port reachable.' },
      { name: 'token', type: 'password', label: 'Token (if it is not on your LAN)',
        hint: 'Found on that machine under Settings › Advanced.' },
      { name: 'role', type: 'select', label: 'Role', value: 'server',
        options: [
          { value: 'workstation', label: 'Workstation' },
          { value: 'server', label: 'Server' },
          { value: 'appliance', label: 'Appliance (TrueNAS, NAS)' },
        ] },
    ],
    okLabel: 'Pair',
    onOk: async (v) => {
      const r = await api('/api/nodes', { method: 'POST', body: v });
      if (!r.ok) { toast('Could not pair', r.error || '', 'err'); return false; }
      toast('Paired', `${r.name} is now available in the switcher.`, 'ok');
      loadNodes();
    },
  });
}

async function scanNetwork() {
  toast('Scanning your network…', 'This takes a few seconds.');
  const { found } = await api('/api/nodes/discover', { method: 'POST' });
  if (!found.length) { toast('Nothing found', 'No other JARVAS installs answered.', 'err'); return; }
  for (const f of found) {
    toast(`Found ${f.hostname}`, f.url, 'ok', [{
      label: 'Pair it', primary: true,
      fn: async () => {
        const r = await api('/api/nodes', {
          method: 'POST', body: { name: f.hostname, url: f.url, role: f.role || 'server' },
        });
        if (r.ok) { toast('Paired', f.hostname, 'ok'); loadNodes(); }
        else toast('Could not pair', r.error || '', 'err');
      },
    }]);
  }
}

/* ── System ───────────────────────────────────────────────────────────── */

async function loadSystem() {
  const st = await api('/api/status');
  const grid = $('#sys-services');
  grid.innerHTML = '';
  for (const s of st.services) {
    const cls = s.state === 'running' ? 'green' : s.state === 'external' ? 'yellow' : 'red';
    grid.append(el('div', { class: 'card' },
      el('div', { class: 'row-between' }, el('h3', {}, s.label),
        el('span', { class: `tag ${cls}` }, s.state)),
      el('div', { class: 'meta' },
        `port ${s.port}${s.pid ? ' · pid ' + s.pid : ''}${s.restarts ? ' · ' + s.restarts + ' restarts' : ''}`),
      s.state === 'external'
        ? el('div', { class: 'meta', style: 'margin-top:5px' },
            'Something else already serves this port — JARVAS attached instead of starting its own.')
        : null,
      s.error ? el('div', { class: 'meta', style: 'color:var(--red)' }, s.error) : null,
      el('div', { class: 'actions' },
        el('button', { class: 'btn sm', onclick: () => svcAction(s.id, 'restart') }, 'Restart'),
        el('button', { class: 'btn sm', onclick: () => svcAction(s.id, 'stop') }, 'Stop'),
        el('button', { class: 'btn sm', onclick: () => showLogs(s) }, 'Logs'))));
  }

  const { events } = await api('/api/events');
  const box = $('#sys-events');
  box.innerHTML = '';
  for (const e of events.slice(0, 60)) {
    box.append(el('div', { style: 'padding:3px 0' },
      el('span', { class: 'dim' }, `${new Date(e.created_at * 1000).toLocaleTimeString()}  `),
      el('span', { class: e.level === 'error' ? 'err' : '' }, `[${e.source}] `),
      e.text));
  }
}

async function svcAction(id, action) {
  const r = await api(`/api/services/${id}/${action}`, { method: 'POST' });
  toast(r.ok ? `${action} ok` : `${action} failed`, r.note || r.error || '', r.ok ? 'ok' : 'err');
  loadSystem();
}

async function showLogs(s) {
  const r = await api(`/api/services/${s.id}/logs`);
  modal({
    title: `${s.label} log`, sub: 'Most recent output',
    fields: [{ name: 'log', type: 'textarea', label: '', value: r.log || '(empty)' }],
    okLabel: 'Close', onOk: () => {},
  });
}

/* ── Settings ─────────────────────────────────────────────────────────── */

async function loadSettings() {
  const { config: cfg } = await api('/api/settings');
  const lic = await api('/api/license');
  const tel = await api('/api/telemetry/preview');
  const body = $('#settings-body');
  body.innerHTML = '';
  $('#settings-sub').textContent = `${S.boot.host.hostname} · ${S.boot.host.platform} · v${S.boot.app.version}`;

  // Licence
  body.append(el('div', { class: 'card', style: 'margin-bottom:16px' },
    el('div', { class: 'row-between' },
      el('div', {},
        el('h3', {}, lic.activated ? `${lic.label} licence` : 'Trial'),
        el('div', { class: 'meta' }, lic.activated
          ? `${lic.seats} seat${lic.seats === 1 ? '' : 's'}${lic.expiry ? ' · renews ' + new Date(lic.expiry * 1000).toLocaleDateString() : ''}`
          : `${lic.days_left ?? 0} days left on your trial`)),
      el('button', { class: 'btn primary', onclick: activateLicense },
        lic.activated ? 'Change key' : 'Activate'))));

  // Model — driven by /api/providers so the model list is real, not hardcoded.
  const provs = await api('/api/providers');
  const provSel = el('select', { id: 'set_provider' });
  const modelSel = el('select', { id: 'set_model' });
  const modelFree = el('input', {
    id: 'set_model_free', type: 'text', placeholder: 'or type a model id',
    style: 'margin-top:6px',
  });
  const keyInput = el('input', {
    id: 'set_key', type: 'password',
    placeholder: 'paste key to change it',
  });
  const provNote = el('div', { class: 'hint' });

  const paintProvider = () => {
    const p = provs.providers.find((x) => x.id === provSel.value);
    modelSel.innerHTML = '';
    for (const m of p.models) {
      modelSel.append(el('option', { value: m.id }, m.label));
    }
    const current = { anthropic: cfg.chat.anthropic_model, openai: cfg.chat.openai_model,
                      ollama: cfg.chat.ollama_model }[p.id];
    if (current && p.models.some((m) => m.id === current)) modelSel.value = current;
    keyInput.parentElement.hidden = !p.needs_key;
    provNote.textContent = p.needs_key
      ? `${p.note} ${p.configured ? 'A key is already saved.' : 'A key is required.'}`
      : p.note;
  };

  for (const p of provs.providers) {
    provSel.append(el('option', { value: p.id },
      p.name + (p.configured ? '' : (p.needs_key ? ' — needs a key' : ''))));
  }
  provSel.value = cfg.chat.provider || 'ollama';
  provSel.onchange = paintProvider;

  const keyField = el('div', { class: 'field' },
    el('label', {}, 'API key'), keyInput,
    el('div', { class: 'hint' },
      'Stored on this machine only. Never included in a report.'));

  body.append(el('div', { class: 'card', style: 'margin-bottom:16px' },
    el('h3', {}, 'AI model'),
    el('div', { class: 'meta', style: 'margin:6px 0 14px' },
      'Where JARVAS thinks. This model also drives your agents.'),
    el('div', { class: 'field' }, el('label', {}, 'Provider'), provSel, provNote),
    el('div', { class: 'field' }, el('label', {}, 'Model'), modelSel, modelFree),
    keyField,
    el('div', { class: 'actions' },
      el('button', {
        class: 'btn primary', onclick: async () => {
          const provider = provSel.value;
          const model = modelFree.value.trim() || modelSel.value;
          const key = keyInput.value.trim();
          const chat = { provider };
          if (provider === 'ollama') chat.ollama_model = model;
          if (provider === 'anthropic') {
            chat.anthropic_model = model;
            if (key) chat.anthropic_key = key;
          }
          if (provider === 'openai') {
            chat.openai_model = model;
            if (key) chat.openai_key = key;
          }
          await api('/api/settings', { method: 'POST', body: { chat } });
          toast('Saved', `${provider} · ${model}`, 'ok');
          refreshStatus();
          loadSettings();
        },
      }, 'Save'),
      el('button', {
        class: 'btn', onclick: async () => {
          const r = await api('/api/setup/test', {
            method: 'POST',
            body: { what: 'chat', chat: { provider: provSel.value } },
          });
          toast(r.ok ? 'Provider reachable' : 'Not reachable',
                r.ok ? '' : 'Check the key, or that Ollama is running.',
                r.ok ? 'ok' : 'err');
        },
      }, 'Test'))));
  paintProvider();

  // Privacy — the consent surface for everything telemetry does.
  const optin = el('input', { type: 'checkbox', id: 'set_tel' });
  optin.checked = !!tel.enabled;
  body.append(el('div', { class: 'card', style: 'margin-bottom:16px' },
    el('h3', {}, 'Privacy and reporting'),
    el('div', { class: 'meta', style: 'margin:6px 0 12px' },
      'When you ask for a connector or tool that does not exist, JARVAS can tell '
      + 'CrossPCAI so it gets built. Reports contain only what you saw in the preview: '
      + 'the name of the thing you asked for and why. Never your chats, files, '
      + 'commands, Slack messages or keys.'),
    el('label', { class: 'check' }, optin,
      el('span', {}, el('div', {}, 'Send reports to CrossPCAI'),
        el('div', { class: 'hint' }, 'You still press Send on each one. This only allows it.'))),
    el('div', { class: 'field', style: 'margin-top:12px' },
      el('label', {}, 'Where reports go'),
      inputEl('set_ingest', (cfg.telemetry || {}).ingest_url || '', 'text',
        'https://your-n8n/webhook/jarvas')),
    el('div', { class: 'actions' },
      el('button', {
        class: 'btn primary', onclick: async () => {
          await api('/api/settings', {
            method: 'POST',
            body: { telemetry: { enabled: $('#set_tel').checked, ingest_url: $('#set_ingest').value } },
          });
          toast('Saved', '', 'ok'); refreshBadges();
        },
      }, 'Save'),
      el('button', {
        class: 'btn', onclick: async () => {
          const p = await api('/api/telemetry/preview');
          modal({
            title: 'Everything queued to send', sub: 'Exactly this, and nothing more.',
            fields: [{ name: 'q', type: 'textarea', label: '',
                       value: JSON.stringify(p.queued, null, 2) || '(nothing queued)' }],
            okLabel: 'Close', onOk: () => {},
          });
        },
      }, 'Show queued data'),
      el('button', {
        class: 'btn danger', onclick: async () => {
          await api('/api/telemetry/clear', { method: 'POST' });
          toast('Cleared', 'The queue is empty.', 'ok');
        },
      }, 'Clear queue'))));

  // This machine — the icon and start-at-login, driven by the installer.
  const ins = await api('/api/install');
  const auto = el('input', { type: 'checkbox', id: 'set_auto' });
  auto.checked = !!ins.autostart;
  auto.onchange = async () => {
    const r = await api('/api/install', {
      method: 'POST', body: { action: 'autostart', enabled: auto.checked },
    });
    if (r.ok) toast(auto.checked ? 'JARVAS will start at login' : 'Start at login turned off', '', 'ok');
    else { auto.checked = !auto.checked; toast('Could not change that', r.error || '', 'err'); }
  };
  body.append(el('div', { class: 'card', style: 'margin-bottom:16px' },
    el('h3', {}, 'This machine'),
    el('div', { class: 'meta', style: 'margin:6px 0 12px' },
      ins.installed
        ? `Installed. Launched by: ${ins.command}`
        : 'JARVAS has not put an icon on this machine yet.'),
    el('label', { class: 'check' }, auto,
      el('span', {}, el('div', {}, 'Start JARVAS when I sign in'),
        el('div', { class: 'hint' }, 'Your agents keep working without opening the window.'))),
    el('div', { class: 'actions' },
      el('button', {
        class: 'btn' + (ins.installed ? '' : ' primary'),
        onclick: async () => {
          const r = await api('/api/install', {
            method: 'POST', body: { action: 'install', autostart: auto.checked },
          });
          toast(r.ok ? 'Icon added' : 'Could not add the icon',
                r.ok ? 'Look in your applications menu.' : (r.error || ''),
                r.ok ? 'ok' : 'err');
          loadSettings();
        },
      }, ins.installed ? 'Repair icon' : 'Add icon to this machine'),
      ins.installed ? el('button', {
        class: 'btn danger',
        onclick: async () => {
          const r = await api('/api/install', { method: 'POST', body: { action: 'uninstall' } });
          toast(r.ok ? 'Icon removed' : 'Could not remove it',
                'Your data was left alone.', r.ok ? 'ok' : 'err');
          loadSettings();
        },
      }, 'Remove icon') : null)));

  // Advanced
  body.append(section('Advanced', 'For pairing other machines and pointing at an existing stack.', [
    field('CrossPCAI stack host', inputEl('set_host', cfg.stack_host || '127.0.0.1', 'text',
      '127.0.0.1 or another machine on your LAN')),
    field('Listen address', selectEl('set_bind', cfg.bind, [
      { value: '127.0.0.1', label: 'This machine only (127.0.0.1)' },
      { value: '0.0.0.0', label: 'Reachable on the network (0.0.0.0)' },
    ])),
    field('Manage background services', (() => {
      const c = el('input', { type: 'checkbox', id: 'set_sup' });
      c.checked = cfg.supervise !== false;
      return el('label', { class: 'check' }, c,
        el('span', {}, 'Start and restart Hermes and the sandbox automatically'));
    })()),
  ], async () => {
    await api('/api/settings', {
      method: 'POST',
      body: { stack_host: $('#set_host').value, bind: $('#set_bind').value,
              supervise: $('#set_sup').checked },
    });
    toast('Saved', 'Some changes need a restart of JARVAS.', 'ok');
  }));
}

function section(title, sub, fields, onSave) {
  return el('div', { class: 'card', style: 'margin-bottom:16px' },
    el('h3', {}, title),
    el('div', { class: 'meta', style: 'margin:6px 0 14px' }, sub),
    ...fields,
    el('div', { class: 'actions' }, el('button', { class: 'btn primary', onclick: onSave }, 'Save')));
}
const field = (label, node) => el('div', { class: 'field' }, el('label', {}, label), node);
const inputEl = (id, value = '', type = 'text', ph = '') =>
  el('input', { id, type, value: value || '', placeholder: ph });
const selectEl = (id, value, options) =>
  el('select', { id }, ...options.map((o) => {
    const n = el('option', { value: o.value }, o.label);
    if (o.value === value) n.selected = true;
    return n;
  }));

function activateLicense() {
  modal({
    title: 'Activate JARVAS',
    sub: 'Paste the key from your purchase email.',
    fields: [{ name: 'key', label: 'Licence key', placeholder: 'JARVAS-plus-0-3-…' }],
    okLabel: 'Activate',
    onOk: async (v) => {
      const r = await api('/api/license/activate', { method: 'POST', body: { key: v.key } });
      if (!r.ok) { toast('That key was not accepted', r.error || '', 'err'); return false; }
      toast('Activated', `${r.label} licence is live.`, 'ok');
      loadSettings(); refreshStatus();
    },
  });
}

/* ── Rail activity ────────────────────────────────────────────────────── */

async function loadRailActivity() {
  try {
    const { events } = await api('/api/events');
    const list = $('#rail-list');
    list.innerHTML = '';
    for (const e of events.slice(0, 30)) {
      list.append(el('div', { class: 'rail-item' },
        el('div', {}, e.text),
        el('div', { class: 'when' }, ago(e.created_at))));
    }
  } catch { /* the rail is decoration; never block a view on it */ }
}

/* ── Status ───────────────────────────────────────────────────────────── */

function setPill(id, up, label) {
  const p = $(id);
  p.querySelector('.dot').className = 'dot ' + (up === true ? 'up' : up === null ? 'warn' : 'down');
  p.querySelector('span:last-child').textContent = label;
}

async function refreshStatus() {
  try {
    const st = await api('/api/status');
    const byId = Object.fromEntries(st.services.map((s) => [s.id, s]));
    const up = (s) => s && (s.state === 'running' || s.state === 'external');
    setPill('#pill-hermes', up(byId.hermes), 'Hermes');
    setPill('#pill-sandbox', up(byId.sandbox), 'Sandbox');
    setPill('#pill-model', st.chat.ready, st.chat.model || 'Model');
  } catch { /* transient */ }
}

async function refreshBadges() {
  try {
    const { pending } = await api('/api/needs');
    const b = $('#needs-badge');
    b.hidden = !pending.total;
    b.textContent = pending.total;
  } catch { /* transient */ }
}

/* ── First-run wizard ─────────────────────────────────────────────────── */

const WIZ = { step: 0, data: { chat: {}, slack: {}, telemetry: {}, role: 'workstation', supervise: true } };

const WIZ_STEPS = [
  {
    title: 'Welcome to JARVAS',
    sub: 'Two minutes and it will be running. You can change any of this later.',
    render: () => el('div', {},
      el('p', { class: 'meta', style: 'margin-bottom:14px' },
        'JARVAS is one app. It runs your agents, keeps a sandbox for work you do not '
        + 'want touching your desktop, and talks to your tools. Install it on as many '
        + 'machines as your licence allows and drive them all from this window.'),
      el('div', { class: 'field' },
        el('label', {}, 'What is this machine?'),
        selectEl('wiz_role', 'workstation', [
          { value: 'workstation', label: 'My computer — I will use JARVAS here' },
          { value: 'server', label: 'A server — others connect to it' },
        ])),
      (() => {
        const c = el('input', { type: 'checkbox', id: 'wiz_auto' });
        c.checked = true;
        return el('label', { class: 'check' }, c,
          el('span', {}, el('div', {}, 'Start JARVAS when I sign in'),
            el('div', { class: 'hint' },
              'Adds the icon and keeps your agents working in the background. '
              + 'You can change this later in Settings.')));
      })()),
    collect: () => {
      WIZ.data.role = $('#wiz_role').value;
      WIZ.data.autostart = $('#wiz_auto').checked;
      WIZ.data.install = true;
    },
  },
  {
    title: 'Choose a model',
    sub: 'JARVAS needs somewhere to think. Local costs nothing and stays on this machine.',
    render: () => el('div', {},
      el('div', { class: 'field' },
        el('label', {}, 'Provider'),
        selectEl('wiz_provider', 'ollama', [
          { value: 'ollama', label: 'Ollama — runs locally, no key needed' },
          { value: 'anthropic', label: 'Anthropic — Claude' },
          { value: 'openai', label: 'OpenAI' },
        ])),
      el('div', { class: 'field' },
        el('label', {}, 'Model'),
        inputEl('wiz_model', 'llama3.2:latest')),
      el('div', { class: 'field' },
        el('label', {}, 'API key (cloud providers only)'),
        inputEl('wiz_key', '', 'password'),
        el('div', { class: 'hint' }, 'Stored on this machine. Never sent to CrossPCAI.')),
      el('button', {
        class: 'btn', onclick: async () => {
          const chat = wizChat();
          const r = await api('/api/setup/test', { method: 'POST', body: { what: 'chat', chat } });
          if (r.ok) {
            toast('Model reachable', r.models?.length ? `Found: ${r.models.slice(0, 3).join(', ')}` : '', 'ok');
          } else {
            toast('Cannot reach that model',
                  chat.provider === 'ollama' ? 'Is Ollama installed and running?' : 'Check the key.', 'err');
          }
        },
      }, 'Test connection')),
    collect: () => { WIZ.data.chat = wizChat(); },
  },
  {
    title: 'Connect Slack',
    sub: 'Optional — you can do this later from the Slack pane.',
    render: () => el('div', {},
      el('div', { class: 'field' },
        el('label', {}, 'Bot token'),
        inputEl('wiz_slack', '', 'password', 'xoxb-…'),
        el('div', { class: 'hint' },
          'Scopes: channels:read, channels:history, chat:write, users:read. Leave blank to skip.')),
      el('div', { class: 'field' },
        el('label', {}, 'Default channel'),
        inputEl('wiz_chan', '', 'text', '#ai-ops-log'))),
    collect: () => {
      const t = $('#wiz_slack').value.trim();
      WIZ.data.slack = t ? { bot_token: t, default_channel: $('#wiz_chan').value, enabled: true } : {};
    },
  },
  {
    title: 'Help us build what you need',
    sub: 'Entirely optional, and off unless you turn it on.',
    render: () => {
      const c = el('input', { type: 'checkbox', id: 'wiz_tel' });
      return el('div', {},
        el('p', { class: 'meta', style: 'margin-bottom:14px' },
          'When you ask JARVAS for a connector or tool it does not have, it makes a '
          + 'note. If you turn this on, you can send those notes to CrossPCAI with one '
          + 'click and we build the missing piece.'),
        el('div', { class: 'card', style: 'margin-bottom:14px' },
          el('div', { style: 'font-weight:620;margin-bottom:6px' }, 'What a report contains'),
          el('div', { class: 'meta' }, 'The name of the thing you asked for, the category, '
            + 'your reason, and how many times you asked.'),
          el('div', { style: 'font-weight:620;margin:10px 0 6px' }, 'What it never contains'),
          el('div', { class: 'meta' }, 'Your conversations, files, command output, Slack '
            + 'messages, API keys or anything identifying you personally.')),
        el('label', { class: 'check' }, c,
          el('span', {}, el('div', {}, 'Let me send reports to CrossPCAI'),
            el('div', { class: 'hint' }, 'You still press Send on each one.'))),
        el('div', { class: 'field', style: 'margin-top:14px' },
          el('label', {}, 'Reporting endpoint (leave as is unless told otherwise)'),
          inputEl('wiz_ingest', '', 'text', 'https://your-n8n/webhook/jarvas')));
    },
    collect: () => {
      WIZ.data.telemetry = {
        enabled: $('#wiz_tel').checked,
        ingest_url: $('#wiz_ingest').value.trim(),
      };
    },
  },
];

function wizChat() {
  const provider = $('#wiz_provider').value;
  const model = $('#wiz_model').value;
  const key = $('#wiz_key').value;
  const chat = { provider };
  if (provider === 'ollama') chat.ollama_model = model;
  if (provider === 'anthropic') { chat.anthropic_model = model; if (key) chat.anthropic_key = key; }
  if (provider === 'openai') { chat.openai_model = model; if (key) chat.openai_key = key; }
  return chat;
}

function renderWizard() {
  const step = WIZ_STEPS[WIZ.step];
  $('#wiz-title').textContent = step.title;
  $('#wiz-sub').textContent = step.sub;
  const steps = $('#wiz-steps');
  steps.innerHTML = '';
  WIZ_STEPS.forEach((_, i) =>
    steps.append(el('div', { class: 'step' + (i <= WIZ.step ? ' done' : '') })));
  const body = $('#wiz-body');
  body.innerHTML = '';
  body.append(step.render());
  $('#wiz-back').style.visibility = WIZ.step === 0 ? 'hidden' : 'visible';
  $('#wiz-next').textContent = WIZ.step === WIZ_STEPS.length - 1 ? 'Finish setup' : 'Continue';

  // Default the model field to what the provider expects.
  const prov = $('#wiz_provider');
  if (prov) {
    prov.onchange = () => {
      $('#wiz_model').value = { ollama: 'llama3.2:latest', anthropic: 'claude-opus-5',
                                openai: 'gpt-4o-mini' }[prov.value];
    };
  }
}

async function wizardNext() {
  WIZ_STEPS[WIZ.step].collect();
  if (WIZ.step < WIZ_STEPS.length - 1) {
    WIZ.step += 1;
    renderWizard();
    return;
  }
  $('#wiz-next').disabled = true;
  $('#wiz-next').textContent = 'Setting up…';
  try {
    await api('/api/setup', { method: 'POST', body: WIZ.data });
    $('#wizard').classList.remove('open');
    toast('JARVAS is ready', 'Your services are starting now.', 'ok');
    await boot(true);
  } catch (e) {
    toast('Setup failed', e.message, 'err');
    $('#wiz-next').disabled = false;
    $('#wiz-next').textContent = 'Finish setup';
  }
}

/* ── Boot ─────────────────────────────────────────────────────────────── */

async function boot(afterSetup = false) {
  S.boot = await api('/api/bootstrap');

  const lic = S.boot.license;
  setPill('#pill-license', lic.activated ? true : (lic.days_left > 0 ? null : false),
          lic.activated ? lic.label : `Trial · ${lic.days_left ?? 0}d`);

  syncNodeSwitch(S.boot.nodes);
  refreshStatus();
  refreshBadges();

  if (!S.boot.setup_complete && !afterSetup) {
    $('#wizard').classList.add('open');
    renderWizard();
    return;
  }

  const hash = (location.hash || '').replace('#/', '');
  go(LOADERS[hash] ? hash : 'chat');
}

/* ── Wiring ───────────────────────────────────────────────────────────── */

document.addEventListener('DOMContentLoaded', () => {
  $$('#nav button').forEach((b) => (b.onclick = () => go(b.dataset.view)));
  $$('[data-goto]').forEach((b) => (b.onclick = () => go(b.dataset.goto)));

  $('#chat-send').onclick = sendChat;
  $('#chat-input').addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendChat(); }
  });
  $('#chat-input').addEventListener('input', (e) => {
    e.target.style.height = 'auto';
    e.target.style.height = Math.min(e.target.scrollHeight, 200) + 'px';
  });

  $('#rail-new').onclick = async () => {
    const { session } = await api('/api/sessions', { method: 'POST', body: { title: 'New session' } });
    S.sessions.unshift(session);
    openSession(session.id);
  };

  $('#agent-new').onclick = () => editAgent(null);
  $('#sb-run').onclick = runSandbox;
  $('#sb-cmd').addEventListener('keydown', (e) => { if (e.key === 'Enter') runSandbox(); });
  $('#sb-refresh').onclick = loadSandboxFiles;
  $('#slack-refresh').onclick = loadSlack;
  $('#conn-new').onclick = () => editConnector(null);
  $('#conn-request').onclick = () => requestThing('connector');
  $('#tool-new').onclick = () => editTool(null);
  $('#tool-request').onclick = () => requestThing('tool');
  $('#needs-add').onclick = () => requestThing('integration');
  $('#needs-send-all').onclick = async () => {
    const r = await api('/api/needs/send-all', { method: 'POST', body: {} });
    if (r.ok) toast('Sent', `${r.sent} report${r.sent === 1 ? '' : 's'} delivered.`, 'ok');
    else if (r.needs_optin) {
      toast('Reporting is off', r.error, 'err',
            [{ label: 'Open privacy settings', primary: true, fn: () => go('settings') }]);
    } else toast('Could not send', r.error || '', 'err');
    loadNeeds(); refreshBadges();
  };
  $('#node-add').onclick = pairNode;
  $('#node-scan').onclick = scanNetwork;
  $('#sys-refresh').onclick = loadSystem;

  $('#node-switch').onchange = (e) => {
    S.node = e.target.value;
    toast('Now operating', e.target.selectedOptions[0].textContent);
    go(S.view);
    refreshStatus();
  };

  $('#wiz-next').onclick = wizardNext;
  $('#wiz-back').onclick = () => { if (WIZ.step > 0) { WIZ.step -= 1; renderWizard(); } };

  window.addEventListener('hashchange', () => {
    const v = (location.hash || '').replace('#/', '');
    if (v && v !== S.view && LOADERS[v]) go(v);
  });

  setInterval(refreshStatus, 12000);
  boot().catch((e) => toast('JARVAS could not start', e.message, 'err'));
});
