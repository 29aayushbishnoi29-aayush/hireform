/* Hireform — front end. Vanilla JS, no build step. */

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];

const state = {
  me: null,
  aiEnabled: false,
  current: null,      // {id?, title, template}
  data: blankData(),
  tab: 'login',
  dirty: false,
};

function blankData() {
  return {
    name: '', location: '', email: '', phone: '', website: '', summary: '',
    experience: [], education: [], skills: [],
  };
}

/* ---------------- helpers ---------------- */

let toastTimer;
function toast(msg, kind = '') {
  const t = $('#toast');
  t.textContent = msg;
  t.className = 'toast ' + kind;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.add('hidden'), 3800);
}

async function api(path, opts = {}) {
  const res = await fetch(path, {
    credentials: 'same-origin',
    headers: opts.body ? { 'Content-Type': 'application/json' } : {},
    ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  if (res.status === 401) {
    state.me = null;
    showAuth();
    throw new Error('Log in to continue');
  }
  if (!res.ok) {
    let msg = res.statusText;
    try { const j = await res.json(); msg = j.detail || msg; } catch {}
    throw new Error(msg);
  }
  const ct = res.headers.get('content-type') || '';
  return ct.includes('application/json') ? res.json() : res.blob();
}

function busy(el, on) {
  el.classList.toggle('spin', on);
  el.disabled = on;
}

/* ---------------- auth ---------------- */

function showAuth() {
  $('#auth').classList.remove('hidden');
  $('#main').classList.add('hidden');
}

function showApp() {
  $('#auth').classList.add('hidden');
  $('#main').classList.remove('hidden');
  paintMe();
}

function paintMe() {
  const pro = state.me.plan === 'pro';
  state.aiEnabled = state.me.ai_enabled !== false;
  const badge = $('#plan-badge');
  badge.textContent = pro ? 'Pro' : 'Free';
  badge.classList.toggle('pro', pro);
  $('#ai-left').textContent = !state.aiEnabled
    ? 'AI is off until a key is added'
    : pro ? 'Unlimited AI edits' : `${state.me.ai_left} free AI edits left`;
  $('#tailor-note').classList.toggle('hidden', state.aiEnabled);
  $('#btn-tailor').disabled = !state.aiEnabled;
  $('#btn-tailor').classList.toggle('hidden', !state.aiEnabled);
  $('#btn-upgrade').classList.toggle('hidden', pro);
}

$$('.tab').forEach((b) => b.addEventListener('click', () => {
  $$('.tab').forEach((x) => x.classList.toggle('active', x === b));
  state.tab = b.dataset.tab;
  $('#auth-submit').textContent = state.tab === 'signup' ? 'Create account' : 'Log in';
  $('#auth-err').textContent = '';
}));

$('#auth-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const f = new FormData(e.target);
  const btn = $('#auth-submit');
  busy(btn, true);
  $('#auth-err').textContent = '';
  try {
    state.me = await api('/api/' + state.tab, {
      method: 'POST',
      body: { email: f.get('email'), password: f.get('password') },
    });
    e.target.reset();
    showApp();
    await loadVersions();
    newResume();
    toast(state.tab === 'signup' ? 'Account created. Start with your name.' : 'Welcome back');
  } catch (err) {
    $('#auth-err').textContent = err.message;
  } finally {
    busy(btn, false);
  }
});

$('#btn-logout').addEventListener('click', async () => {
  try { await api('/api/logout', { method: 'POST' }); } catch {}
  state.me = null;
  showAuth();
});

$('#btn-upgrade').addEventListener('click', async (e) => {
  busy(e.target, true);
  try {
    const { url } = await api('/api/checkout', { method: 'POST' });
    location.href = url;
  } catch (err) {
    toast(err.message, 'err');
  } finally {
    busy(e.target, false);
  }
});

/* ---------------- versions ---------------- */

async function loadVersions() {
  const rows = await api('/api/resumes');
  const ul = $('#versions');
  ul.innerHTML = '';
  if (!rows.length) {
    ul.innerHTML = '<li style="cursor:default;color:#8a919c;font-size:13px">No saved versions yet</li>';
    return;
  }
  rows.forEach((r) => {
    const li = document.createElement('li');
    li.dataset.id = r.id;
    if (state.current && state.current.id === r.id) li.classList.add('active');
    const span = document.createElement('span');
    span.textContent = r.title || 'Untitled';
    span.title = `${r.title} — ${r.template}`;
    const del = document.createElement('button');
    del.textContent = '✕';
    del.title = 'Delete';
    del.addEventListener('click', async (ev) => {
      ev.stopPropagation();
      if (!confirm(`Delete "${r.title}"? This cannot be undone.`)) return;
      try {
        await api('/api/resumes/' + r.id, { method: 'DELETE' });
        if (state.current && state.current.id === r.id) newResume();
        await loadVersions();
        toast('Deleted');
      } catch (err) { toast(err.message, 'err'); }
    });
    li.append(span, del);
    li.addEventListener('click', () => openResume(r.id));
    ul.append(li);
  });
}

async function openResume(id) {
  try {
    const r = await api('/api/resumes/' + id);
    state.current = { id: r.id, title: r.title, template: r.template };
    state.data = { ...blankData(), ...r.data };
    fillForm();
    await loadVersions();
    toast('Loaded');
  } catch (err) { toast(err.message, 'err'); }
}

function newResume() {
  state.current = { title: 'My resume', template: 'classic' };
  state.data = blankData();
  fillForm();
  $$('#versions li').forEach((li) => li.classList.remove('active'));
}

$('#btn-new').addEventListener('click', newResume);

/* ---------------- form <-> data ---------------- */

function fillForm() {
  const d = state.data;
  $('#resume-title').value = state.current.title || 'My resume';
  $('#template').value = state.current.template || 'classic';
  ['name', 'location', 'email', 'phone', 'website', 'summary'].forEach((k) => {
    $('#f-' + k).value = d[k] || '';
  });
  renderExp();
  renderEdu();
  renderSkills();
}

function bindField(sel, key) {
  $(sel).addEventListener('input', (e) => { state.data[key] = e.target.value; });
}

['name', 'location', 'email', 'phone', 'website', 'summary'].forEach((k) => bindField('#f-' + k, k));

// People type "linkedin.com/in/me"; the renderer wants a real URL.
$('#f-website').addEventListener('blur', (e) => {
  const v = e.target.value.trim();
  if (v && !/^[a-z][a-z0-9+.-]*:\/\//i.test(v) && !/^mailto:/i.test(v)) {
    e.target.value = 'https://' + v.replace(/^\/+/, '');
    state.data.website = e.target.value;
  }
});
$('#resume-title').addEventListener('input', (e) => { state.current.title = e.target.value; });
$('#template').addEventListener('change', (e) => { state.current.template = e.target.value; });

function cardShell(title, onRemove, extraButtons = []) {
  const card = document.createElement('div');
  card.className = 'card';
  const head = document.createElement('div');
  head.className = 'card-head';
  const label = document.createElement('span');
  label.textContent = title;
  const tools = document.createElement('div');
  extraButtons.forEach((b) => tools.append(b));
  const rm = document.createElement('button');
  rm.textContent = 'Remove';
  rm.addEventListener('click', onRemove);
  tools.append(rm);
  head.append(label, tools);
  card.append(head);
  return card;
}

function field(card, labelText, value, onInput, opts = {}) {
  const l = document.createElement('label');
  l.textContent = labelText;
  const input = document.createElement(opts.textarea ? 'textarea' : 'input');
  if (opts.textarea) input.rows = opts.rows || 4;
  if (opts.placeholder) input.placeholder = opts.placeholder;
  input.value = value || '';
  input.addEventListener('input', (e) => onInput(e.target.value));
  l.append(input);
  card.append(l);
  return input;
}

function row2(card) {
  const g = document.createElement('div');
  g.className = 'grid2';
  card.append(g);
  return g;
}

function renderExp() {
  const list = $('#exp-list');
  list.innerHTML = '';
  state.data.experience.forEach((e, i) => {
    const tools = [];
    if (state.aiEnabled) {
      const rewrite = document.createElement('button');
      rewrite.textContent = 'AI rewrite';
      rewrite.className = 'ai';
      rewrite.addEventListener('click', () => rewriteBullets(i, rewrite));
      tools.push(rewrite);
    }

    const card = cardShell(`Role ${i + 1}`, () => {
      state.data.experience.splice(i, 1);
      renderExp();
    }, tools);

    const g1 = row2(card);
    field(g1, 'Company', e.company, (v) => (e.company = v));
    field(g1, 'Position', e.position, (v) => (e.position = v));
    const g2 = row2(card);
    field(g2, 'Location', e.location, (v) => (e.location = v));
    field(g2, 'Start (YYYY-MM)', e.start, (v) => (e.start = v), { placeholder: '2022-01' });
    field(card, 'End (blank = present)', e.end, (v) => (e.end = v), { placeholder: '2024-06' });
    field(card, 'Bullets — one per line', e.bullets, (v) => (e.bullets = v),
      { textarea: true, rows: 5, placeholder: 'Cut onboarding time 40% by rebuilding signup\nLed a team of 4 engineers' });
    list.append(card);
  });
}

function renderEdu() {
  const list = $('#edu-list');
  list.innerHTML = '';
  state.data.education.forEach((e, i) => {
    const card = cardShell(`School ${i + 1}`, () => {
      state.data.education.splice(i, 1);
      renderEdu();
    });
    const g1 = row2(card);
    field(g1, 'Institution', e.institution, (v) => (e.institution = v));
    field(g1, 'Degree', e.degree, (v) => (e.degree = v));
    const g2 = row2(card);
    field(g2, 'Area of study', e.area, (v) => (e.area = v));
    field(g2, 'Start', e.start, (v) => (e.start = v), { placeholder: '2017-08' });
    field(card, 'End', e.end, (v) => (e.end = v), { placeholder: '2021-05' });
    list.append(card);
  });
}

function renderSkills() {
  const list = $('#skill-list');
  list.innerHTML = '';
  state.data.skills.forEach((s, i) => {
    const card = cardShell(`Group ${i + 1}`, () => {
      state.data.skills.splice(i, 1);
      renderSkills();
    });
    field(card, 'Label', s.label, (v) => (s.label = v), { placeholder: 'Languages' });
    field(card, 'Details', s.details, (v) => (s.details = v), { placeholder: 'Python, TypeScript, SQL' });
    list.append(card);
  });
}

$$('[data-add]').forEach((b) => b.addEventListener('click', () => {
  const kind = b.dataset.add;
  if (kind === 'exp') { state.data.experience.push({ company: '', position: '', location: '', start: '', end: '', bullets: '' }); renderExp(); }
  if (kind === 'edu') { state.data.education.push({ institution: '', degree: '', area: '', start: '', end: '' }); renderEdu(); }
  if (kind === 'skill') { state.data.skills.push({ label: '', details: '' }); renderSkills(); }
}));

/* ---------------- AI ---------------- */

async function rewriteBullets(i, btn) {
  const e = state.data.experience[i];
  if (!e.bullets || !e.bullets.trim()) return toast('Add a few bullets first', 'err');
  busy(btn, true);
  try {
    const out = await api('/api/ai/rewrite', {
      method: 'POST',
      body: { bullets: e.bullets, role: e.position, company: e.company },
    });
    e.bullets = (out.bullets || []).join('\n');
    renderExp();
    state.me.ai_left = state.me.plan === 'pro' ? null : Math.max(0, state.me.ai_left - 1);
    paintMe();
    toast('Bullets rewritten — edit anything that sounds off');
  } catch (err) {
    toast(err.message, 'err');
  } finally {
    busy(btn, false);
  }
}

$('#btn-tailor').addEventListener('click', async (e) => {
  const jd = $('#f-jd').value;
  if (jd.trim().length < 50) return toast('Paste the full job description first', 'err');
  busy(e.target, true);
  try {
    const out = await api('/api/ai/tailor', { method: 'POST', body: { data: state.data, jd } });
    state.data = { ...state.data, ...out.data };
    fillForm();
    const miss = out.missing_keywords || [];
    $('#missing').textContent = miss.length
      ? 'Not in your resume yet: ' + miss.join(', ')
      : 'Every keyword in the posting is covered.';
    $('#missing').classList.toggle('warn', miss.length > 0);
    if (state.me.plan !== 'pro') {
      state.me.ai_left = Math.max(0, state.me.ai_left - 1);
      paintMe();
    }
    toast('Tailored — review before you export');
  } catch (err) {
    toast(err.message, 'err');
  } finally {
    busy(e.target, false);
  }
});

/* ---------------- save / preview ---------------- */

$('#btn-save').addEventListener('click', async (e) => {
  busy(e.target, true);
  try {
    const payload = {
      id: state.current.id,
      title: state.current.title || 'Untitled',
      template: state.current.template,
      data: state.data,
    };
    const r = await api('/api/resumes', { method: 'POST', body: payload });
    state.current.id = r.id;
    await loadVersions();
    toast('Saved');
  } catch (err) {
    toast(err.message, 'err');
  } finally {
    busy(e.target, false);
  }
});

$('#btn-preview').addEventListener('click', async (e) => {
  const btn = e.currentTarget;
  busy(btn, true);
  const t = setTimeout(() => toast('Typesetting your PDF…'), 1200);
  try {
    const blob = await api('/api/render', {
      method: 'POST',
      body: { data: state.data, template: state.current.template },
    });
    clearTimeout(t);
    const url = URL.createObjectURL(blob);
    $('#pdf-frame').src = url;
    const dl = $('#btn-download');
    dl.href = url;
    dl.download = (state.data.name ? state.data.name.replace(/\s+/g, '-') : 'resume') + '.pdf';
    $('#modal').classList.remove('hidden');
  } catch (err) {
    clearTimeout(t);
    toast(err.message, 'err');
  } finally {
    busy(btn, false);
  }
});

$('#btn-close-modal').addEventListener('click', () => $('#modal').classList.add('hidden'));
$('#modal').addEventListener('click', (e) => { if (e.target.id === 'modal') $('#modal').classList.add('hidden'); });

/* ---------------- boot ---------------- */

(async function boot() {
  const params = new URLSearchParams(location.search);
  try {
    state.me = await api('/api/me');
    showApp();
    await loadVersions();
    newResume();
    if (params.get('upgraded')) toast('Payment received — Pro is active', '');
  } catch {
    showAuth();
  }
})();
