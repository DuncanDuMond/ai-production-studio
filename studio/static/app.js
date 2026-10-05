/* AI Production Studio. No third-party client code; no browser persistence. */
'use strict';

function createApi(fetcher = (...args) => fetch(...args)) {
  let bearer = '';
  return {
    setToken(value) { bearer = String(value || '').trim(); },
    hasToken() { return Boolean(bearer); },
    async request(path, { method = 'GET', body, raw = false } = {}) {
      if (!/^\/api\/[a-zA-Z0-9/_?=&.%+-]*$/.test(path) || path.includes('..')) {
        throw new Error('Only same-origin studio API requests are allowed.');
      }
      const headers = { Accept: 'application/json' };
      if (bearer) headers.Authorization = `Bearer ${bearer}`;
      if (body !== undefined) headers['Content-Type'] = 'application/json';
      let response;
      try {
        response = await fetcher(path, { method, headers, credentials: 'same-origin', cache: 'no-store', redirect: 'error', ...(body !== undefined ? { body: JSON.stringify(body) } : {}) });
      } catch (_) {
        throw new Error('Cannot reach the studio API. Check the server, then refresh. A submitted run may still finish; check history before retrying.');
      }
      if (!response.ok) {
        let detail;
        try { detail = (await response.json()).detail; } catch (_) { /* Non-JSON server error. */ }
        const message = typeof detail === 'string' ? detail : `Request failed (${response.status}). Check your inputs and server configuration.`;
        const error = new Error(message);
        error.status = response.status;
        throw error;
      }
      if (response.status === 204) return null;
      return raw ? response : response.json();
    }
  };
}

function formatMetric(value) {
  return typeof value === 'number' && Number.isFinite(value) ? value.toLocaleString('en-US', { maximumFractionDigits: 1 }) : '—';
}

function formatScore(value) {
  return typeof value === 'number' && Number.isFinite(value) ? String(value) : '—';
}

function hasRunData(snapshot) { return Boolean(snapshot.workspaceLoaded || (snapshot.loaded && !snapshot.projectId)); }

const api = createApi();
const state = { projects: [], documents: [], runs: [], integrations: [], integrationTotal: null, summary: null, settings: null, health: null, projectId: '', currentRun: null, view: 'overview', loaded: false, workspaceLoaded: false, loading: false, submitting: false };
const titles = { overview: 'Overview', workspaces: 'Workspaces', workbench: 'Workbench', history: 'Run history', integrations: 'Integrations', setup: 'Setup & access' };
const $ = id => document.getElementById(id);
const key = value => encodeURIComponent(String(value));
function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}
function icon(name) {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('aria-hidden', 'true');
  const use = document.createElementNS('http://www.w3.org/2000/svg', 'use');
  use.setAttribute('href', `#i-${name}`);
  svg.append(use);
  return svg;
}
function button(text, handler, kind = '', symbol) {
  const node = el('button', `button ${kind}`, text);
  node.type = 'button';
  if (symbol) node.prepend(icon(symbol));
  node.addEventListener('click', handler);
  return node;
}
function navButton(text, view, kind = '', symbol) { return button(text, () => navigate(view), kind, symbol); }
function badge(text, tone = '') { return el('span', `badge ${tone}`, text); }
function panel(title, subtitle) {
  const node = el('section', 'panel');
  const heading = el('div', 'panel-heading');
  const copy = el('div');
  copy.append(el('h2', '', title));
  if (subtitle) copy.append(el('p', '', subtitle));
  heading.append(copy);
  node.append(heading);
  return node;
}
function empty(title, description, action, symbol = 'box') {
  const node = el('div', 'empty-state');
  const emblem = el('div', 'empty-icon');
  emblem.append(icon(symbol));
  node.append(emblem, el('h3', '', title), el('p', '', description));
  if (action) node.append(action);
  return node;
}
function actions(...nodes) { const node = el('div', 'actions'); node.append(...nodes); return node; }
function currentProject() { return state.projects.find(project => project.id === state.projectId); }
function dateText(value) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? 'Date unavailable' : date.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}
let toastTimer;
function notify(message, isError = false) {
  const node = $('toast');
  node.textContent = message;
  node.className = `toast${isError ? ' error' : ''}`;
  node.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { node.hidden = true; }, isError ? 9000 : 5000);
}
function showError(error) {
  const node = $('error-banner');
  node.textContent = error.message || 'The request could not be completed.';
  node.hidden = false;
  if (error.status === 401 || error.status === 403) {
    node.append(document.createTextNode(' '), button('Enter session token', openAccess, 'small'));
  }
}
function navigate(view) {
  if (!Object.hasOwn(titles, view)) view = 'overview';
  if (location.hash !== `#${view}`) location.hash = view;
  state.view = view;
  for (const name of Object.keys(titles)) $(`view-${name}`).hidden = name !== view;
  document.querySelectorAll('[data-view]').forEach(link => {
    const active = link.dataset.view === view;
    link.classList.toggle('active', active);
    if (active) link.setAttribute('aria-current', 'page'); else link.removeAttribute('aria-current');
  });
  $('breadcrumb-current').textContent = titles[view];
  document.title = `${titles[view]} · AI Production Studio`;
}
function renderSelectors() {
  const select = $('workspace-select');
  select.replaceChildren();
  if (!state.projects.length) {
    const option = el('option', '', state.loaded ? 'No workspaces yet' : 'Workspaces unavailable');
    option.value = '';
    select.append(option);
  }
  for (const project of state.projects) {
    const option = el('option', '', project.name);
    option.value = project.id;
    select.append(option);
  }
  select.value = state.projectId;
  select.disabled = state.loading || state.submitting || !state.projects.length;
  $('workspace-count').textContent = state.loaded ? String(state.projects.length) : '—';
  $('integration-count').textContent = state.integrationTotal === null ? '—' : String(state.integrationTotal);
}
function renderOverview() {
  const root = $('overview-content');
  root.replaceChildren();
  const hero = el('div', 'hero');
  const copy = el('div', 'hero-copy');
  copy.append(badge('THE EVIDENCE-FIRST WORKFLOW', 'indigo'), el('h2', '', 'From context to confidence.'), el('p', '', 'Bring your documents. Run a grounded pipeline. Inspect retrieval, guardrails, evaluations, and traces — in one place.'));
  copy.append(actions(navButton('Open workbench', 'workbench', 'primary', 'run'), button('Add sample workspace', importSample, '', 'plus')));
  const diagram = el('div', 'hero-diagram');
  diagram.setAttribute('aria-label', 'Pipeline design, not a live execution');
  const orbit = el('div', 'pipeline-orbit');
  const nucleus = el('div', 'pipeline-nucleus');
  nucleus.append(icon('shield'), el('span', '', 'EVIDENCE'));
  orbit.append(el('span', 'orbit-label orbit-top', 'CONTEXT'), el('span', 'orbit-label orbit-right', 'RUN'), el('span', 'orbit-label orbit-bottom', 'EVALUATE'), el('span', 'orbit-label orbit-left', 'TRACE'), nucleus);
  diagram.append(orbit, el('span', 'diagram-caption', 'ONE PIPELINE. INSPECTABLE BY DESIGN.'));
  hero.append(copy, diagram);
  const stats = el('div', 'stats-grid');
  const s = state.summary;
  for (const [label, value, note, symbol] of [
    ['Workspaces', s?.projects, 'Isolated project context', 'folder'],
    ['Documents', s?.documents, 'Your source of truth', 'file'],
    ['Total runs', s?.runs, 'Persisted execution records', 'run'],
    ['Passed runs', s?.passed_runs, s ? `${formatMetric(s.blocked_runs)} blocked · ${formatMetric(s.average_latency_ms)} ms avg.` : 'Awaiting server metrics', 'shield']
  ]) {
    const card = el('div', 'stat-card');
    const labelRow = el('div', 'stat-label'); labelRow.append(el('span', '', label), icon(symbol));
    card.append(labelRow, el('strong', 'stat-value', formatMetric(value)), el('p', '', note));
    stats.append(card);
  }
  const grid = el('div', 'overview-grid');
  const recent = panel('Recent runs', state.projectId ? `Active workspace · ${currentProject()?.name || ''}` : 'Execution evidence appears here');
  recent.firstChild.append(navButton('View history', 'history', 'small', 'arrow'));
  if (!state.runs.length) recent.append(empty(hasRunData(state) ? 'No runs yet' : 'Run history unavailable', 'Start with source documents, then create your first run.', navButton('Go to workbench', 'workbench', 'small'), 'run'));
  else recent.append(runList(state.runs.slice(0, 4)));
  const next = panel('Your path to production', 'Local validation first. Live inference by choice.');
  const steps = [
    ['01', 'Bring your context', 'Create a workspace and add source documents.', 'workspaces'],
    ['02', 'Exercise the pipeline', 'Local runs are offline schema exercises, not AI inference.', 'workbench'],
    ['03', 'Inspect every signal', 'Review citations, metric scores, and execution steps.', 'history']
  ];
  for (const [number, title, detail, view] of steps) {
    const row = button('', () => navigate(view), 'path-step');
    const content = el('span'); content.append(el('strong', '', title), el('small', '', detail));
    row.append(el('span', 'step-number', number), content, icon('arrow'));
    next.append(row);
  }
  grid.append(recent, next);
  const foundation = el('div', 'foundation-strip');
  const available = state.integrations.filter(item => item.status === 'available').length;
  const foundationCopy = el('div');
  foundationCopy.append(el('strong', '', 'A ten-tool foundation'), el('p', '', state.integrationTotal === null ? 'Integration availability has not been loaded.' : `${available} of ${state.integrationTotal} capabilities available · Availability is not external connectivity. Review each upstream license before commercial hosting.`));
  foundation.append(icon('box'), foundationCopy, navButton('Explore integrations', 'integrations', 'small', 'arrow'));
  root.append(hero, stats, grid, foundation);
}

function field(labelText, tag, name, options = {}) {
  const wrapper = el('label', 'field');
  wrapper.append(el('span', '', labelText));
  const input = el(tag);
  input.name = name;
  for (const [attribute, value] of Object.entries(options)) {
    if (attribute === 'value') input.value = value;
    else if (attribute === 'required') input.required = value;
    else input.setAttribute(attribute, value);
  }
  wrapper.append(input);
  return { wrapper, input };
}
function openModal(title, content) {
  $('modal-title').textContent = title;
  $('modal-content').replaceChildren(content);
  if (!$('modal').open) $('modal').showModal();
}
function formDialog(title, fields, submitText, callback, description) {
  const form = el('form');
  if (description) form.append(el('p', 'dialog-description', description));
  for (const item of fields) form.append(item.wrapper);
  const message = el('p', 'form-error');
  message.setAttribute('role', 'alert');
  const submit = button(submitText, () => {}, 'primary'); submit.type = 'submit';
  form.append(message, actions(button('Cancel', () => $('modal').close()), submit));
  let busy = false;
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (busy) return;
    busy = true; submit.disabled = true; message.textContent = '';
    try { await callback(new FormData(form)); }
    catch (error) { message.textContent = error.message; }
    finally { busy = false; submit.disabled = false; }
  });
  openModal(title, form);
}
function openAccess() {
  const token = field('Bearer token', 'input', 'token', { type: 'password', autocomplete: 'off', placeholder: 'Enter server access token', maxlength: '4096' });
  formDialog('Session access', [token], 'Apply for this tab', async data => {
    api.setToken(data.get('token'));
    token.input.value = '';
    state.projects = []; state.documents = []; state.runs = []; state.currentRun = null; state.projectId = ''; state.integrations = []; state.integrationTotal = null; state.loaded = false; state.workspaceLoaded = false;
    $('modal').close();
    await refresh();
    notify(api.hasToken() ? 'Token held in tab memory only. Reloading clears it.' : 'Session token cleared.');
  }, 'The token is held only in this tab’s memory, never browser storage. Leave blank to clear it. Provider keys belong in server configuration, not here.');
}
function openCreateWorkspace() {
  formDialog('Create workspace', [field('Workspace name', 'input', 'name', { required: true, maxlength: '100', placeholder: 'e.g. Customer knowledge' }), field('Description (optional)', 'textarea', 'description', { rows: '3', maxlength: '1000', placeholder: 'What will this workspace help you validate?' })], 'Create workspace', async data => {
    const name = String(data.get('name')).trim();
    if (!name) throw new Error('Enter a workspace name.');
    const project = await api.request('/api/projects', { method: 'POST', body: { name, description: String(data.get('description')).trim() } });
    const latest = await api.request('/api/projects');
    if (!latest.items.some(item => item.id === project.id)) throw new Error('Workspace could not be verified. Refresh before retrying.');
    state.projects = latest.items;
    state.projectId = project.id;
    state.currentRun = null;
    $('modal').close();
    await refresh();
    navigate('workspaces');
    notify('Workspace created. Add a document to give your pipeline context.');
  });
}
function openAddDocument() {
  const projectId = state.projectId;
  if (!projectId) { openCreateWorkspace(); return; }
  formDialog('Add source document', [field('Document title', 'input', 'title', { required: true, maxlength: '200', placeholder: 'e.g. Product handbook' }), field('Document content', 'textarea', 'content', { required: true, rows: '10', maxlength: '50000', placeholder: 'Paste plain-text source material. This is persisted on the studio server.' })], 'Save document', async data => {
    const body = { title: String(data.get('title')).trim(), content: String(data.get('content')).trim() };
    if (!body.title || !body.content) throw new Error('A title and non-empty document are required.');
    const doc = await api.request(`/api/projects/${key(projectId)}/documents`, { method: 'POST', body });
    const latest = await api.request(`/api/projects/${key(projectId)}/documents`);
    if (!latest.items.some(item => item.id === doc.id)) throw new Error('Document could not be verified. Refresh before retrying.');
    $('modal').close();
    await refresh();
    notify('Document saved and verified.');
  }, 'Only add content you are authorized to process. Local runs stay offline; live runs may send retrieved context to your configured provider.');
}
async function deleteDocument(doc) {
  const projectId = state.projectId;
  if (!window.confirm(`Delete “${doc.title}”? Existing run evidence is retained. This cannot be undone.`)) return;
  try {
    await api.request(`/api/projects/${key(projectId)}/documents/${key(doc.id)}`, { method: 'DELETE' });
    const latest = await api.request(`/api/projects/${key(projectId)}/documents`);
    if (latest.items.some(item => item.id === doc.id)) throw new Error('Deletion could not be verified. Refresh before retrying.');
    await refresh();
    notify('Document deleted.');
  } catch (error) { showError(error); }
}
async function createSampleWorkspace(client) {
  const project = await client.request('/api/projects', { method: 'POST', body: { name: 'Sample · Launch knowledge', description: 'Explicitly imported sample content for a local pipeline exercise. Not production policy.' } });
  const documents = [
    { title: 'Sample · Studio release policy', content: 'SAMPLE CONTENT — fictional policy for testing only. The Atlas studio release process requires a documented evaluation pass, reviewed source citations, and a guardrail check before launch. The release owner records evidence in the run history. A live run requires explicit consent and a configured provider; local exercises do not make provider calls.' },
    { title: 'Sample · Customer support guide', content: 'SAMPLE CONTENT — fictional support guidance for testing only. Atlas support is available Monday to Friday, 09:00–17:00 UTC. Standard support requests are acknowledged within one business day. Security reports are escalated to the release owner. Unanswered questions must be identified as unsupported rather than invented.' }
  ];
  try {
    const created = [];
    for (const body of documents) created.push(await client.request(`/api/projects/${key(project.id)}/documents`, { method: 'POST', body }));
    const verified = await client.request(`/api/projects/${key(project.id)}/documents`);
    if (!created.every(doc => verified.items.some(item => item.id === doc.id))) throw new Error('Sample document verification failed.');
  } catch (_) {
    throw new Error('Sample workspace was created, but document import did not finish. Refresh and inspect it before adding another sample.');
  }
  return project;
}
async function importSample(event) {
  if (state.importing) return;
  state.importing = true;
  const trigger = event?.currentTarget;
  if (trigger) trigger.disabled = true;
  try {
    const project = await createSampleWorkspace(api);
    state.projectId = project.id;
    state.currentRun = null;
    await refresh();
    navigate('workspaces');
    notify('Sample workspace imported with two clearly labeled sample documents. No run was started.');
  } catch (error) { showError(error); }
  finally { state.importing = false; if (trigger) trigger.disabled = false; }
}
let workspaceRequest = 0;
async function loadWorkspace() {
  const generation = ++workspaceRequest;
  const projectId = state.projectId;
  state.workspaceLoaded = false;
  if (!projectId) { state.documents = []; state.runs = []; return; }
  const [documents, runs] = await Promise.all([api.request(`/api/projects/${key(projectId)}/documents`), api.request(`/api/projects/${key(projectId)}/runs`)]);
  if (generation !== workspaceRequest || projectId !== state.projectId) return;
  state.documents = documents.items;
  state.runs = runs.items;
  state.workspaceLoaded = true;
}
async function selectWorkspace(projectId) {
  if (!state.projects.some(project => project.id === projectId)) return;
  state.projectId = projectId;
  state.currentRun = null;
  state.documents = []; state.runs = []; state.workspaceLoaded = false;
  renderAll();
  try { await loadWorkspace(); } catch (error) { showError(error); }
  renderAll();
}
function renderWorkspaces() {
  const root = $('workspaces-content'); root.replaceChildren();
  const heading = el('div', 'section-toolbar');
  heading.append(el('span', 'muted', state.loaded ? `${state.projects.length} workspaces on this server` : 'Workspace data unavailable'), actions(button('Add sample workspace', importSample, 'small'), button('Create workspace', openCreateWorkspace, 'primary', 'plus')));
  root.append(heading);
  if (!state.projects.length) {
    root.append(empty(state.loaded ? 'A clean slate for your next idea' : 'Connect to your studio', state.loaded ? 'Create a workspace to collect documents and inspect your first grounded run. Nothing is pre-seeded.' : 'Start the API or enter your session token to load workspaces.', state.loaded ? button('Create workspace', openCreateWorkspace, 'primary', 'plus') : button('Session access', openAccess), 'folder'));
    return;
  }
  const grid = el('div', 'workspace-grid');
  const projects = el('div', 'project-list');
  for (const project of state.projects) {
    const card = button('', () => selectWorkspace(project.id), `project-card ${project.id === state.projectId ? 'selected' : ''}`);
    card.setAttribute('aria-pressed', String(project.id === state.projectId));
    card.append(icon('folder'), el('strong', '', project.name), el('p', '', project.description || 'No description provided.'), el('span', 'project-meta', `${formatMetric(project.document_count)} documents · ${formatMetric(project.run_count)} runs`));
    projects.append(card);
  }
  const documents = panel(currentProject()?.name || 'Source documents', 'Plain-text context · stored on this server');
  documents.firstChild.append(button('Add document', openAddDocument, 'small', 'plus'));
  if (!state.documents.length) documents.append(empty(state.workspaceLoaded ? 'No source documents yet' : 'Documents not loaded', 'Add the knowledge you want this workspace to retrieve from.', state.workspaceLoaded ? button('Add document', openAddDocument, 'small', 'plus') : null, 'file'));
  for (const doc of state.documents) {
    const row = el('article', 'document-row');
    const details = el('details');
    const summary = el('summary');
    summary.append(icon('file'), el('strong', '', doc.title), el('span', 'mono muted', `${doc.content.length.toLocaleString()} chars`));
    details.append(summary, el('p', 'document-content', doc.content), el('p', 'document-date', `Added ${dateText(doc.created_at)}`));
    row.append(details, button('Delete', () => deleteDocument(doc), 'small danger'));
    documents.append(row);
  }
  const bottom = el('div', 'panel-bottom');
  bottom.append(el('span', 'hint', 'Documents are not edited in place. Delete and re-add to replace.'), navButton('Open workbench', 'workbench', 'small', 'arrow'));
  documents.append(bottom);
  grid.append(projects, documents); root.append(grid);
}

function buildRunPayload(values, settings) {
  const prompt = String(values.prompt || '').trim();
  const mode = values.mode;
  const engine = values.engine;
  const topK = Number(values.top_k);
  if (!prompt) throw new Error('Enter a prompt before running the pipeline.');
  if (!['local', 'live'].includes(mode)) throw new Error('Choose local or live mode.');
  if (!['pydantic-ai', 'instructor'].includes(engine)) throw new Error('Choose a supported engine.');
  if (mode === 'local' && engine !== 'pydantic-ai') throw new Error('Local mode uses Pydantic AI TestModel; Instructor requires live mode.');
  if (!Number.isInteger(topK) || topK < 1 || topK > 10) throw new Error('Retrieve between 1 and 10 documents.');
  if (mode === 'live' && settings?.live_enabled !== true) throw new Error('Live execution is not enabled on this server. See Setup & access.');
  if (mode === 'live' && values.allow_paid !== true) throw new Error('Explicit consent is required for each live run.');
  return { prompt, mode, engine, top_k: topK, reference: String(values.reference || '').trim(), allow_paid: mode === 'live' && values.allow_paid === true };
}
function evaluationStatus(metric) { return metric.passed === null || metric.passed === undefined ? 'Not evaluated' : metric.passed ? 'Passed' : 'Below threshold'; }
function statusBadge(status) {
  const tone = ['passed', 'available', 'completed'].includes(status) ? 'success' : ['failed', 'unavailable', 'error'].includes(status) ? 'danger' : ['blocked', 'unconfigured'].includes(status) ? 'warning' : '';
  return badge(status || 'unknown', tone);
}
const draft = { prompt: '', mode: 'local', engine: 'pydantic-ai', top_k: 3, reference: '', allow_paid: false };
function renderWorkbench() {
  const root = $('workbench-content'); root.replaceChildren();
  if (!state.projectId) {
    root.append(empty('Start with a workspace', 'A run belongs to a workspace. Add your documents first, or explicitly import the labeled sample workspace.', actions(button('Create workspace', openCreateWorkspace, 'primary', 'plus'), button('Add sample workspace', importSample)), 'run'));
    return;
  }
  const context = el('div', 'context-bar');
  context.append(icon('folder'), el('strong', '', currentProject()?.name || ''), badge(state.workspaceLoaded ? `${state.documents.length} source documents` : 'Sources unavailable'), navButton('Manage sources', 'workspaces', 'small', 'arrow'));
  root.append(context);
  const grid = el('div', 'workbench-grid');
  const configuration = panel('Run configuration', 'Keep the question precise. Let the sources do the work.');
  const form = el('form', 'run-form'); form.id = 'run-form';
  const fieldset = el('fieldset'); fieldset.disabled = state.submitting;
  const prompt = field('Prompt', 'textarea', 'prompt', { rows: '7', required: true, maxlength: '8000', placeholder: 'What do your documents say? Ask a question grounded in your workspace context.', value: draft.prompt });
  prompt.input.id = 'run-prompt';
  prompt.input.addEventListener('input', () => { draft.prompt = prompt.input.value; });
  fieldset.append(prompt.wrapper);
  const modeGroup = el('fieldset', 'mode-options');
  modeGroup.append(el('legend', '', 'Execution mode'));
  const liveAvailable = state.settings?.live_enabled === true;
  if (!liveAvailable) draft.allow_paid = false;
  if (draft.mode === 'local') draft.engine = 'pydantic-ai';
  for (const [value, title, detail] of [['local', 'Local exercise', 'Offline · no provider calls'], ['live', 'Live inference', liveAvailable ? 'Configured provider · may incur cost' : 'Requires server configuration']]) {
    const label = el('label', `mode-card ${draft.mode === value ? 'selected' : ''}`);
    const input = el('input'); input.type = 'radio'; input.name = 'mode'; input.value = value; input.checked = draft.mode === value; input.disabled = value === 'live' && !liveAvailable;
    const copy = el('span'); copy.append(el('strong', '', title), el('small', '', detail));
    label.append(input, copy);
    input.addEventListener('change', () => { draft.mode = value; draft.allow_paid = false; renderWorkbench(); });
    modeGroup.append(label);
  }
  fieldset.append(modeGroup);
  const engine = field('Structured-output engine', 'select', 'engine');
  for (const [value, label] of [['pydantic-ai', 'Pydantic AI'], ['instructor', 'Instructor']]) {
    const option = el('option', '', label); option.value = value; option.disabled = draft.mode === 'local' && value === 'instructor'; engine.input.append(option);
  }
  engine.input.value = draft.engine;
  engine.input.addEventListener('change', () => { draft.engine = engine.input.value; });
  fieldset.append(engine.wrapper);
  const advanced = el('details', 'advanced');
  advanced.append(el('summary', '', 'Retrieval & reference settings'));
  const topK = field('Maximum retrieved documents', 'input', 'top_k', { type: 'number', min: '1', max: '10', step: '1', value: draft.top_k });
  topK.input.addEventListener('input', () => { draft.top_k = topK.input.value; });
  const reference = field('Reference answer (optional)', 'textarea', 'reference', { rows: '3', maxlength: '8000', placeholder: 'Provide a known-good answer for reference-based metrics.', value: draft.reference });
  reference.input.addEventListener('input', () => { draft.reference = reference.input.value; });
  advanced.append(topK.wrapper, reference.wrapper, el('p', 'hint', 'Metrics that need a missing reference are reported as not evaluated, never as a fabricated pass.'));
  fieldset.append(advanced);
  if (draft.mode === 'live') {
    const consent = el('label', 'consent');
    const checkbox = el('input'); checkbox.type = 'checkbox'; checkbox.name = 'allow_paid'; checkbox.required = true; checkbox.checked = draft.allow_paid;
    checkbox.addEventListener('change', () => { draft.allow_paid = checkbox.checked; });
    consent.append(checkbox, el('span', '', 'I authorize this live run to send my prompt and retrieved context to the configured provider and incur possible charges.'));
    fieldset.append(consent);
  } else fieldset.append(el('div', 'mode-explanation', 'Local is an offline schema / retrieval exercise. Its output is not model-generated intelligence. No live fallback is attempted.'));
  const submit = button(state.submitting ? 'Executing pipeline…' : draft.mode === 'live' ? 'Run live pipeline' : 'Run local pipeline', () => {}, 'primary full-width', 'run');
  submit.type = 'submit'; submit.id = 'run-submit'; submit.disabled = state.submitting || !state.workspaceLoaded || !state.documents.length;
  fieldset.append(submit);
  if (!state.documents.length) fieldset.append(el('p', 'hint run-hint', 'Add at least one source document before running.'));
  form.append(fieldset);
  form.addEventListener('submit', submitRun);
  configuration.append(form);
  const result = el('div', 'result-column');
  if (state.submitting) {
    const progress = el('div', 'running-state'); progress.setAttribute('role', 'status'); progress.append(el('span', 'loading-mark'), el('strong', '', 'Executing the pipeline'), el('p', '', 'The server is processing this synchronous run. Evidence will appear when it returns. Do not resubmit.'));
    result.append(progress);
  } else if (state.currentRun) result.append(renderEvidence(state.currentRun));
  else {
    const blank = panel('Run evidence', 'The full picture, not just the answer.');
    blank.append(empty('Ready when you are', 'Run the pipeline to see its actual output, retrieved citations, evaluation scores, and trace steps.', null, 'shield'));
    const placeholders = el('div', 'evidence-checklist');
    for (const [name, detail] of [['Grounded output', 'Structured answer with source references'], ['Evaluation evidence', 'Scores, thresholds, and explicit skips'], ['Execution trace', 'Actual framework steps and guardrails']]) {
      const row = el('div'); row.append(icon('shield'), el('span', '', name), el('small', '', detail)); placeholders.append(row);
    }
    blank.append(placeholders); result.append(blank);
  }
  grid.append(configuration, result); root.append(grid);
}
async function submitRun(event) {
  event.preventDefault();
  if (state.submitting || !state.projectId) return;
  const projectId = state.projectId;
  try {
    const body = buildRunPayload(draft, state.settings);
    state.submitting = true;
    state.currentRun = null;
    $('error-banner').hidden = true;
    renderWorkbench(); renderSelectors();
    const created = await api.request(`/api/projects/${key(projectId)}/runs`, { method: 'POST', body });
    const verified = await api.request(`/api/runs/${key(created.id)}`);
    if (state.projectId === projectId) state.currentRun = verified;
    notify(`Run ${verified.status}. ${verified.mode === 'local' ? 'Local exercise — no model inference.' : 'Live provider execution.'}`, verified.status === 'failed');
    await refresh();
  } catch (error) { showError(error); }
  finally { state.submitting = false; draft.allow_paid = false; renderAll(); }
}
async function inspectRun(runId) {
  try {
    const run = await api.request(`/api/runs/${key(runId)}`);
    if (run.project_id !== state.projectId) await selectWorkspace(run.project_id);
    state.currentRun = run;
    renderWorkbench();
    navigate('workbench');
  } catch (error) { showError(error); }
}
async function exportRun(runId) {
  try {
    const response = await api.request(`/api/runs/${key(runId)}/export`, { raw: true });
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = el('a'); link.href = url; link.download = `studio-run-${String(runId).replace(/[^a-zA-Z0-9_-]/g, '')}.json`;
    document.body.append(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    notify('Run evidence exported as JSON.');
  } catch (error) { showError(error); }
}
function renderEvidence(run) {
  const root = panel('Run evidence', `${dateText(run.created_at)} · ${run.engine}`);
  root.classList.add('evidence-panel');
  root.firstChild.append(button('Export JSON', () => exportRun(run.id), 'small', 'download'));
  const meta = el('div', 'evidence-meta');
  meta.append(statusBadge(run.status), badge(run.mode === 'local' ? 'LOCAL EXERCISE' : 'LIVE INFERENCE', 'indigo'), el('span', 'mono muted', `${formatMetric(run.latency_ms)} ms`));
  root.append(meta, el('p', 'run-id mono', `RUN ${run.id}`));
  if (run.mode === 'local') root.append(el('div', 'local-disclaimer', 'Offline schema exercise — not model-generated intelligence. A pass is not a certification of production readiness.'));
  if (run.error) root.append(el('div', 'notice error run-error', run.error));
  const panes = {};
  const output = el('section', 'evidence-body');
  output.append(el('h3', 'overline', 'PROMPT'), el('p', 'prompt-readback', run.prompt));
  if (run.output) {
    output.append(el('h3', 'overline', 'ANSWER'), el('div', 'answer-text', run.output.answer), el('p', 'hint', `Reported confidence: ${formatScore(run.output.confidence)} · This value is not a calibrated guarantee.`));
  } else output.append(empty('No output produced', 'This run did not return an answer. Inspect the guardrail and execution evidence for details.', null, 'file'));
  panes.Output = output;
  const citations = el('section', 'evidence-body');
  if (!run.output?.citations?.length) citations.append(empty('No citations returned', 'No source citations were attached to this run.', null, 'file'));
  for (const [index, citation] of (run.output?.citations || []).entries()) {
    const card = el('article', 'citation-card');
    card.append(el('h3', '', `${index + 1}. ${citation.title}`), el('p', 'mono muted', citation.id), el('p', 'document-content', citation.content));
    citations.append(card);
  }
  panes.Citations = citations;
  const evaluations = el('section', 'evidence-body');
  evaluations.append(el('p', 'hint evaluation-note', 'Raw framework scores and thresholds. A missing score means not evaluated; it is not zero or a pass.'));
  if (!run.evaluations?.length) evaluations.append(empty('No evaluations recorded', 'The backend returned no evaluation evidence for this run.', null, 'shield'));
  for (const metric of run.evaluations || []) {
    const card = el('article', 'metric-card');
    const heading = el('div', 'metric-heading'); heading.append(el('h3', '', metric.name), badge(evaluationStatus(metric), metric.passed === true ? 'success' : metric.passed === false ? 'danger' : ''));
    const values = el('div', 'metric-values'); values.append(el('strong', '', formatScore(metric.score)), el('span', '', `threshold ${formatScore(metric.threshold)}`), badge(metric.framework));
    card.append(heading, values, el('p', '', metric.detail)); evaluations.append(card);
  }
  panes.Evaluations = evaluations;
  const trace = el('section', 'evidence-body');
  trace.append(el('h3', 'overline', 'TRACE ID'), el('p', 'trace-id mono', run.trace_id || 'No trace ID returned. External trace delivery is not implied.'));
  if (!run.steps?.length) trace.append(el('p', 'hint', 'No execution steps recorded.'));
  for (const [index, step] of (run.steps || []).entries()) {
    const row = el('article', 'trace-step');
    const heading = el('div', 'trace-heading'); heading.append(el('span', 'step-number', String(index + 1).padStart(2, '0')), el('h3', '', step.name), statusBadge(step.status));
    row.append(heading, el('p', '', step.detail), el('span', 'mono muted', `${step.framework} · ${formatMetric(step.duration_ms)} ms`)); trace.append(row);
  }
  panes.Trace = trace;
  const tabs = el('div', 'tabs'); tabs.setAttribute('role', 'tablist'); tabs.setAttribute('aria-label', 'Run evidence');
  const tabButtons = [];
  Object.entries(panes).forEach(([name, pane], index) => {
    const tab = button(name, () => activate(index), 'tab');
    const suffix = name.toLowerCase();
    tab.id = `evidence-tab-${suffix}`; tab.setAttribute('role', 'tab'); tab.setAttribute('aria-controls', `evidence-${suffix}`);
    pane.id = `evidence-${suffix}`; pane.setAttribute('role', 'tabpanel'); pane.setAttribute('aria-labelledby', tab.id); pane.tabIndex = 0;
    tab.addEventListener('keydown', event => {
      let next;
      if (event.key === 'ArrowRight') next = (index + 1) % 4;
      if (event.key === 'ArrowLeft') next = (index + 3) % 4;
      if (event.key === 'Home') next = 0;
      if (event.key === 'End') next = 3;
      if (next !== undefined) { event.preventDefault(); activate(next); tabButtons[next].focus(); }
    });
    tabButtons.push(tab); tabs.append(tab);
  });
  function activate(selected) {
    Object.values(panes).forEach((pane, index) => {
      pane.hidden = index !== selected;
      tabButtons[index].setAttribute('aria-selected', String(index === selected));
      tabButtons[index].tabIndex = index === selected ? 0 : -1;
      tabButtons[index].classList.toggle('active', index === selected);
    });
  }
  activate(0);
  root.append(tabs, ...Object.values(panes));
  const guard = el('div', `guardrail-strip ${run.guardrails?.passed ? 'passed' : 'not-passed'}`);
  guard.append(icon('shield'), el('strong', '', run.guardrails?.passed === true ? 'Guardrails passed' : run.guardrails?.passed === false ? 'Guardrails not passed' : 'Guardrails unknown'), el('p', '', run.guardrails?.detail || 'No guardrail evidence returned.'));
  root.append(guard);
  return root;
}
function runList(runs) {
  const list = el('div', 'run-list');
  for (const run of runs) {
    const row = el('div', 'run-row');
    const inspect = button('', () => inspectRun(run.id), 'run-inspect');
    const emblem = el('span', 'run-emblem'); emblem.append(icon('run'));
    const copy = el('span', 'run-copy'); copy.append(el('strong', '', run.prompt), el('small', '', `${dateText(run.created_at)} · ${run.engine} · ${run.mode === 'local' ? 'Local exercise' : 'Live inference'}`));
    inspect.append(emblem, copy, statusBadge(run.status));
    const download = button('JSON', () => exportRun(run.id), 'small', 'download'); download.setAttribute('aria-label', `Export run ${run.id} as JSON`);
    row.append(inspect, download); list.append(row);
  }
  return list;
}
const historyFilter = { query: '', status: 'all' };
function renderHistory() {
  const root = $('history-content'); root.replaceChildren();
  const toolbar = el('div', 'section-toolbar');
  const search = field('Search prompts', 'input', 'history-query', { type: 'search', placeholder: 'Filter by prompt…', value: historyFilter.query });
  const status = field('Run status', 'select', 'history-status');
  for (const value of ['all', 'passed', 'blocked', 'failed']) { const option = el('option', '', value === 'all' ? 'All statuses' : value); option.value = value; status.input.append(option); }
  status.input.value = historyFilter.status;
  const filters = el('div', 'history-filters'); filters.append(search.wrapper, status.wrapper);
  toolbar.append(filters, navButton('New run', 'workbench', 'primary', 'plus'));
  root.append(toolbar);
  const content = panel(currentProject()?.name || 'No workspace selected', 'Persisted runs · newest first · active workspace only'); root.append(content);
  const results = el('div'); content.append(results);
  function update() {
    const filtered = state.runs.filter(run => (historyFilter.status === 'all' || run.status === historyFilter.status) && run.prompt.toLowerCase().includes(historyFilter.query.toLowerCase()));
    results.replaceChildren();
    if (filtered.length) results.append(runList(filtered));
    else results.append(empty(state.runs.length ? 'No matching runs' : hasRunData(state) ? 'No runs recorded yet' : 'Run history unavailable', state.runs.length ? 'Try a different prompt or status filter.' : 'Create a run in the workbench. Its persisted evidence will appear here.', state.runs.length ? null : navButton('Open workbench', 'workbench', 'small'), 'history'));
  }
  search.input.addEventListener('input', () => { historyFilter.query = search.input.value; update(); });
  status.input.addEventListener('change', () => { historyFilter.status = status.input.value; update(); });
  update();
}

function validateCatalog(catalog) {
  if (!Array.isArray(catalog.items) || catalog.items.length !== catalog.total || new Set(catalog.items.map(item => item.id)).size !== catalog.total) throw new Error('Integration catalog count does not match its declared total. Refresh or check the server.');
  return catalog.items;
}
function safeRepoUrl(value) {
  try {
    const url = new URL(value);
    return url.protocol === 'https:' && url.hostname === 'github.com' && !url.username && !url.password && !url.port && /^\/[\w.-]+\/[\w.-]+\/?$/.test(url.pathname) ? url.href : null;
  } catch (_) { return null; }
}
const catalogFilter = { query: '', status: 'all' };
function renderIntegrations() {
  const root = $('integrations-content'); root.replaceChildren();
  const note = el('div', 'catalog-notice'); note.append(icon('box'), el('p', '', 'Availability describes server-side capabilities, not external service connectivity. Unconfigured and unavailable tools are not connected. Nothing is installed or enabled from this screen.'));
  root.append(note);
  const toolbar = el('div', 'section-toolbar');
  const search = field('Find an integration', 'input', 'integration-query', { type: 'search', placeholder: 'Search tools or roles…', value: catalogFilter.query });
  search.wrapper.classList.add('catalog-search');
  const filters = el('div', 'catalog-filters');
  const filterButtons = [];
  for (const value of ['all', 'available', 'unconfigured', 'unavailable']) {
    const control = button(value === 'all' ? 'All tools' : value, () => { catalogFilter.status = value; update(); }, 'small');
    control.dataset.filter = value; filterButtons.push(control); filters.append(control);
  }
  toolbar.append(search.wrapper, filters); root.append(toolbar);
  const summary = el('p', 'catalog-summary'); const grid = el('div', 'integration-grid'); root.append(summary, grid);
  function update() {
    filterButtons.forEach(control => { control.classList.toggle('selected', control.dataset.filter === catalogFilter.status); control.setAttribute('aria-pressed', String(control.dataset.filter === catalogFilter.status)); });
    const items = state.integrations.filter(item => (catalogFilter.status === 'all' || item.status === catalogFilter.status) && `${item.name} ${item.role}`.toLowerCase().includes(catalogFilter.query.toLowerCase()));
    summary.textContent = state.integrationTotal === null ? 'Catalog not loaded.' : `${items.length} of ${state.integrationTotal} upstream tools shown`;
    grid.replaceChildren();
    if (!items.length) grid.append(empty(state.integrationTotal === null ? 'Integration data unavailable' : 'No matching integrations', state.integrationTotal === null ? 'Refresh after the studio API is running and your session is authorized.' : 'Try a different tool name or status filter.', state.integrationTotal === null ? button('Refresh status', refresh, 'small') : null));
    for (const item of items) {
      const card = el('article', 'integration-card');
      const header = el('div', 'integration-header');
      const mark = el('span', 'tool-mark', ({ otel: 'Oτ', langfuse: 'Lf', phoenix: 'Ph', ragas: 'Ra', deepeval: 'De', guardrails: 'Gr', 'pydantic-ai': 'Py', instructor: 'In', litellm: 'Li', haystack: 'Hs' })[item.id] || item.name.slice(0, 2));
      header.append(mark, statusBadge(item.status));
      card.append(header, el('h2', '', item.name), el('p', 'tool-role', item.role), el('p', 'tool-detail', item.detail || 'No capability detail returned by the server.'));
      const footer = el('div', 'integration-footer'); footer.append(el('span', 'mono', item.version ? `v${item.version}` : 'Version unavailable'));
      const repo = safeRepoUrl(item.repo);
      if (repo) { const link = el('a', '', 'Repository ↗'); link.href = repo; link.target = '_blank'; link.rel = 'noopener noreferrer'; link.setAttribute('aria-label', `${item.name} repository (opens in a new tab)`); footer.append(link); }
      card.append(footer); grid.append(card);
    }
  }
  search.input.addEventListener('input', () => { catalogFilter.query = search.input.value; update(); });
  update();
  const bottom = el('div', 'foundation-strip'); bottom.append(icon('settings'), el('p', '', 'Provider routing and telemetry are configured on the server. No keys are stored in this browser.'), navButton('View setup', 'setup', 'small', 'arrow')); root.append(bottom);
}
function settingRow(label, value, detail) {
  const row = el('div', 'setting-row'); const copy = el('div'); copy.append(el('strong', '', label)); if (detail) copy.append(el('p', '', detail)); row.append(copy, el('span', 'setting-value', value)); return row;
}
function renderSetup() {
  const root = $('setup-content'); root.replaceChildren();
  const grid = el('div', 'setup-grid');
  const config = panel('Server configuration', 'Read-only snapshot from /api/settings. No secrets are returned.');
  config.firstChild.append(button('Refresh', refresh, 'small', 'refresh'));
  const settings = state.settings;
  const flag = value => value === true ? 'Enabled' : value === false ? 'Disabled' : 'Unknown';
  config.append(settingRow('Live inference', flag(settings?.live_enabled), 'Enabling the server never grants per-run consent.'), settingRow('Configured model', settings ? settings.model || 'Not configured' : 'Unknown', 'Model selection is controlled by the server.'), settingRow('Telemetry export', flag(settings?.telemetry_enabled), 'Configuration does not verify trace delivery.'), settingRow('API authentication', settings ? settings.authentication_required ? 'Required' : 'Not required' : 'Unknown', 'Production deployments require a server access token.'));
  const access = panel('Session access', 'Authenticate this tab without persisting credentials.');
  access.append(settingRow('Bearer token', api.hasToken() ? 'In tab memory' : 'Not set', 'Token value is never displayed.'), el('p', 'setup-copy', 'Your studio token is sent only to this origin’s /api/ endpoints. Reloading or closing the tab clears it. Provider credentials must be configured on the server.'));
  const accessActions = el('div', 'panel-bottom'); accessActions.append(button('Manage session token', openAccess, '', 'shield')); access.append(accessActions);
  grid.append(config, access); root.append(grid);
  const instructions = panel('Enable only what you need', 'Changes are made in your server environment, then take effect after a restart.');
  const steps = el('div', 'setup-instructions');
  const local = el('article'); local.append(el('span', 'step-number', '01'), el('h3', '', 'Start with a local exercise'), el('p', '', 'Create a workspace, add documents, and use Local exercise in the workbench. No model API is called. Required upstream libraries must be installed on the server.'), el('code', 'code-block', 'uv run python -m uvicorn studio.app:app --host 127.0.0.1 --port 8765'));
  const live = el('article'); live.append(el('span', 'step-number', '02'), el('h3', '', 'Opt into live inference'), el('p', '', 'Configure the LiteLLM OpenAI-compatible proxy endpoint, its API key, and the model on the server. Then enable live mode. Never paste provider keys into workspace documents.'), el('code', 'code-block', 'STUDIO_LITELLM_BASE_URL\nSTUDIO_LITELLM_API_KEY\nSTUDIO_MODEL\nSTUDIO_ENABLE_LIVE=true'), el('p', 'hint', 'Every live request also requires the workbench’s explicit cost and data-sharing consent. A failed live call never falls back to a local pass.'));
  const telemetry = el('article'); telemetry.append(el('span', 'step-number', '03'), el('h3', '', 'Route traces intentionally'), el('p', '', 'Configure STUDIO_OTLP_ENDPOINT only when you want export. Route Collector traces to Langfuse or Phoenix separately. Raw content is not sent to telemetry by default; a trace ID does not prove external receipt.'), el('code', 'code-block', 'STUDIO_OTLP_ENDPOINT\nSTUDIO_API_TOKEN'), el('p', 'hint', 'Set STUDIO_API_TOKEN for protected API access. No provider secrets are accepted by this setup screen.'));
  steps.append(local, live, telemetry); instructions.append(steps); root.append(instructions);
}

function renderAll() {
  renderSelectors();
  renderOverview();
  renderWorkspaces();
  renderWorkbench();
  renderHistory();
  renderIntegrations();
  renderSetup();
}

async function readStudioSnapshot(client) {
  const names = ['health', 'summary', 'projects', 'integrations', 'settings'];
  const results = await Promise.allSettled(names.map(name => client.request(`/api/${name}`)));
  const values = {}; const errors = [];
  results.forEach((result, index) => {
    const name = names[index];
    if (result.status === 'fulfilled') values[name] = result.value;
    else {
      const error = new Error(`${name[0].toUpperCase()}${name.slice(1)}: ${result.reason.message}`);
      error.status = result.reason.status;
      errors.push(error);
    }
  });
  return { values, errors };
}
async function refresh() {
  if (state.loading) return;
  state.loading = true;
  $('refresh-button').disabled = true;
  $('error-banner').hidden = true;
  try {
    const { values, errors } = await readStudioSnapshot(api);
    state.health = values.health || null;
    state.summary = values.summary || null;
    state.projects = values.projects?.items || [];
    state.integrations = []; state.integrationTotal = null;
    if (values.integrations) {
      try { state.integrations = validateCatalog(values.integrations); state.integrationTotal = values.integrations.total; }
      catch (error) { errors.push(error); }
    }
    state.settings = values.settings || null;
    state.loaded = Boolean(values.projects);
    if (!state.projects.some(project => project.id === state.projectId)) {
      state.projectId = state.projects[0]?.id || '';
      state.currentRun = null;
    }
    $('connection-status').textContent = state.health ? state.health.mode === 'live-enabled' ? 'API · live enabled' : 'API · local mode' : errors.some(error => error.status === 401) ? 'Authentication required' : 'API unavailable';
    $('connection-status').className = `status-dot ${state.health ? 'online' : 'offline'}`;
    if (state.health) $('app-version').textContent = `v${state.health.version}`;
    state.documents = []; state.runs = []; state.workspaceLoaded = false;
    if (state.projectId) {
      try { await loadWorkspace(); } catch (error) { errors.push(error); }
    }
    if (errors.length) {
      const error = new Error(errors.map(item => item.message).join(' · '));
      error.status = errors.find(item => item.status === 401 || item.status === 403)?.status;
      showError(error);
    }
  } catch (error) { showError(error); }
  finally {
    state.loading = false;
    $('refresh-button').disabled = false;
    renderAll();
  }
}

function boot() {
  document.querySelectorAll('[data-view]').forEach(link => link.addEventListener('click', () => navigate(link.dataset.view)));
  window.addEventListener('hashchange', () => { if (location.hash !== '#main') navigate(location.hash.slice(1)); });
  $('refresh-button').addEventListener('click', refresh);
  $('close-modal').addEventListener('click', () => $('modal').close());
  $('access-button').addEventListener('click', openAccess);
  $('workspace-select').addEventListener('change', event => selectWorkspace(event.target.value));
  navigate(location.hash.slice(1));
  renderAll();
  refresh();
}

if (typeof module !== 'undefined' && module.exports) module.exports = { createApi, formatMetric, createSampleWorkspace, el, buildRunPayload, evaluationStatus, validateCatalog, safeRepoUrl, formatScore, readStudioSnapshot, hasRunData };
if (typeof document !== 'undefined') boot();
