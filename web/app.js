// digimod page: base firmware -> mods -> build (verified) -> emulator. Talks to server/app.py.
const $ = id => document.getElementById(id);
const api = async (path, opts = {}) => {
  const r = await fetch(path, opts);
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || r.statusText);
  return j;
};
const post = (path, body) => api(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const kb = n => n >= 1e6 ? (n / 1e6).toFixed(2) + ' MB' : Math.round(n / 1e3) + ' KB';
const when = t => new Date(t * 1000).toLocaleString();

let firmwares = [], modList = [], builds = [], fwSel = null, modSel = new Set(), modOpts = {}, emuWanted = null;
let emuOff = false;   // the emulator cannot run here (Docker, or not built): no Start, no "Test in emulator"
const checksOpen = {};   // build id -> its checks list expanded or not, kept across the re-renders while a build runs
let buildsShown = '';

// ---------------------------------------------------------------- 1. firmware
async function loadFirmwares() {
  firmwares = await api('/api/firmwares');
  if (!fwSel || !firmwares.some(f => f.id === fwSel)) fwSel = (firmwares.find(f => f.emulator) || firmwares[0] || {}).id;
  const el = $('fw-list');
  if (!firmwares.length) { el.innerHTML = '<div class="empty">No firmware yet: add an official OS .syx</div>'; return; }
  el.innerHTML = firmwares.map(f => `
    <div class="item ${f.id === fwSel ? 'on' : ''}" data-id="${f.id}">
      <input type="radio" name="fw" ${f.id === fwSel ? 'checked' : ''}>
      <div class="grow">
        <div class="t">${esc(f.device)} OS ${esc(f.version)}
          ${f.checksums_ok ? '<span class="tag ok">checksums ok</span>' : '<span class="tag bad">invalid</span>'}
          ${f.emulator ? '<span class="tag run">emulator</span>' : ''}</div>
        <div class="d">${esc(f.file)} · ${kb(f.size)} · ${f.sections.length} sections</div>
      </div>
    </div>`).join('');
  el.querySelectorAll('.item').forEach(i => i.onclick = () => { fwSel = i.dataset.id; modSel.clear(); render(); });
}

$('fw-add').onchange = async e => {
  const f = e.target.files[0]; e.target.value = '';
  if (!f) return;
  try { await api('/api/firmwares?name=' + encodeURIComponent(f.name), { method: 'POST', body: f }); await loadFirmwares(); renderMods(); }
  catch (err) { alert(err.message); }
};

// ---------------------------------------------------------------- 2. mods
async function loadMods() { modList = await api('/api/mods'); }

function renderMods() {
  const fw = firmwares.find(f => f.id === fwSel);
  const el = $('mod-list');
  if (!modList.length) {
    el.innerHTML = '<div class="empty">No mods in the storage yet.<br>Each mod is a folder in <code>mods/</code>; see mods/README.md.</div>';
  } else {
    el.innerHTML = modList.map(m => {
      const fits = fw && m.firmwares.some(x => x.main_os_sha256 === fw.main_os_sha256);
      const usable = fits && !m.problems.length;
      const why = m.problems.length ? m.problems.join('; ') : fits ? '' : 'not made for this firmware';
      const opts = modSel.has(m.id) && (m.options || []).length ? `<div class="opts">${m.options.map(o => `
          <label title="${esc(o.help || '')}">${esc(o.label || o.id)}
            <input type="number" data-mod="${esc(m.id)}" data-opt="${esc(o.id)}" min="${o.min}" max="${o.max}"
                   value="${(modOpts[m.id] || {})[o.id] ?? o.default}"></label>`).join('')}</div>` : '';
      return `<div class="item ${modSel.has(m.id) ? 'on' : ''} ${usable ? '' : 'off'}">
        <input type="checkbox" data-id="${esc(m.id)}" ${modSel.has(m.id) ? 'checked' : ''} ${usable ? '' : 'disabled'}>
        <div class="grow"><div class="t">${esc(m.name)}${m.version ? `<span class="tag">v${esc(m.version)}</span>` : ''}${m.lab ? '<span class="tag run">lab</span>' : ''}</div>
          <div class="d">${esc(m.description || '')}${why ? ` <span class="tag bad">${esc(why)}</span>` : ''}</div>${opts}</div>
      </div>`;
    }).join('');
    el.querySelectorAll('input[type=checkbox]').forEach(i => i.onchange = () => { i.checked ? modSel.add(i.dataset.id) : modSel.delete(i.dataset.id); renderMods(); });
    el.querySelectorAll('input[data-opt]').forEach(i => i.onchange = () => {
      (modOpts[i.dataset.mod] = modOpts[i.dataset.mod] || {})[i.dataset.opt] = parseInt(i.value, 10);
    });
  }
  $('build').disabled = !fw || !fw.checksums_ok;
  $('build-hint').textContent = !fw ? 'Pick a base firmware first.' :
    modSel.size ? `${modSel.size} mod(s) onto ${fw.device} OS ${fw.version}.` :
    `No mods ticked: the build is the original, repacked and verified byte for byte.`;
}

$('build').onclick = async () => {
  $('build').disabled = true;
  const options = Object.fromEntries([...modSel].map(id => [id, modOpts[id] || {}]));
  try { await post('/api/builds', { firmware: fwSel, mods: [...modSel], options }); await loadBuilds(); }
  catch (err) { alert(err.message); }
  renderMods();
};

// ---------------------------------------------------------------- 3. builds
async function loadBuilds() {
  builds = await api('/api/builds');
  const el = $('build-list');
  const running = builds.some(b => b.status === 'queued' || b.status === 'building');
  const seen = JSON.stringify(builds);
  if (seen === buildsShown) { if (running) setTimeout(loadBuilds, 1000); return; }   // nothing new: keep the DOM as it is
  buildsShown = seen;
  if (!builds.length) { el.innerHTML = '<div class="empty">Nothing built yet</div>'; renderEmuSources(); return; }
  el.innerHTML = builds.map(b => {
    const tag = b.status === 'ok' ? '<span class="tag ok">verified</span>' :
      b.status === 'failed' ? '<span class="tag bad">failed</span>' : `<span class="tag run">${esc(b.status)}…</span>`;
    const optText = m => m.options && Object.keys(m.options).length ? ' (' + Object.entries(m.options).map(([k, v]) => `${k} ${v}`).join(', ') + ')' : '';
    const mods = b.mods.length ? b.mods.map(m => esc(m.name + optText(m))).join(', ') : 'no mods';
    const checks = (b.checks || []).map(c => `<li class="${c.ok ? '' : 'no'}">${esc(c.name)}${c.detail && c.ok && c.name === 'pack' ? ` (${esc(c.detail)})` : ''}</li>`).join('');
    return `<div class="item" style="cursor:default">
      <div class="grow">
        <div class="t">${esc(b.base.device)} OS ${esc(b.base.version)} + ${mods} ${tag}</div>
        <div class="d">${when(b.created)}${b.size ? ' · ' + kb(b.size) : ''}${b.sections_changed && b.sections_changed.length ? ' · sections changed: ' + b.sections_changed.join(', ') : ''}</div>
        ${b.error ? `<div class="d" style="color:var(--bad)">${esc(b.error)}</div>` : ''}
        ${checks ? `<details data-build="${b.id}" ${(b.id in checksOpen ? checksOpen[b.id] : b.status === 'failed') ? 'open' : ''}><summary>${(b.checks || []).filter(c => c.ok).length}/${(b.checks || []).length} checks</summary><ul class="checks">${checks}</ul></details>` : ''}
        ${b.status === 'ok' ? `<div class="acts"><a href="/download/${b.id}">Download .syx</a>${emuOff ? '' : `<button data-emu="${b.id}">Test in emulator</button>`}</div>` : ''}
      </div></div>`;
  }).join('');
  el.querySelectorAll('details[data-build]').forEach(d => d.ontoggle = () => { checksOpen[d.dataset.build] = d.open; });
  el.querySelectorAll('[data-emu]').forEach(btn => btn.onclick = () => { emuWanted = 'build:' + btn.dataset.emu; renderEmuSources(); startEmu(); });
  renderEmuSources();
  if (running) setTimeout(loadBuilds, 1000);
}

// ---------------------------------------------------------------- 4. emulator
function renderEmuSources() {
  const sel = $('emu-src'), cur = emuWanted || sel.value;
  const opts = [
    ...firmwares.filter(f => f.emulator).map(f => [`base:${f.id}`, `${f.device} OS ${f.version} — original`]),
    ...builds.filter(b => b.status === 'ok').map(b => [`build:${b.id}`, `${b.base.device} OS ${b.base.version} + ${b.mods.map(m => m.name).join(', ') || 'no mods'} — build ${b.id}`]),
  ];
  sel.innerHTML = opts.map(([v, t]) => `<option value="${v}">${esc(t)}</option>`).join('') || '<option value="">No firmware the emulator can run</option>';
  if (opts.some(o => o[0] === cur)) sel.value = cur;
  emuWanted = null;
}

async function startEmu() {
  const [kind, id] = $('emu-src').value.split(':');
  if (!id) return;
  try { await post('/api/emulator/start', { kind, id }); } catch (err) { alert(err.message); }
  pollEmu();
}
$('emu-start').onclick = startEmu;
$('emu-stop').onclick = async () => { await post('/api/emulator/stop', {}); pollEmu(); };

let emuTimer = null, shownUrl = null;
async function pollEmu() {
  clearTimeout(emuTimer);
  const s = await api('/api/emulator');
  const st = $('emu-status'), frame = $('emu-frame');
  if (s.status === 'unavailable' && !emuOff) { emuOff = true; buildsShown = ''; loadBuilds(); }
  st.textContent = s.status === 'unavailable' ? s.error :
    s.status === 'running' ? `running: ${s.label}` :
    s.status === 'stopped' ? (s.error ? `stopped: ${s.error}` : 'stopped') :
    s.status === 'failed' ? `failed: ${s.error}` : `${s.status}: ${s.label || ''}`;
  st.style.color = s.status === 'failed' ? 'var(--bad)' : s.status === 'running' ? 'var(--ok)' : '';
  const running = s.status === 'running';
  if (running && shownUrl !== s.url) { frame.src = s.url; shownUrl = s.url; }
  if (!running && shownUrl) { frame.src = 'about:blank'; shownUrl = null; }
  frame.hidden = !running;
  $('emu-open').hidden = !running; $('emu-open').href = s.url || '#';
  $('emu-log').textContent = running ? '' : (s.log || []).join('\n');
  $('emu-stop').disabled = ['stopped', 'unavailable'].includes(s.status);
  $('emu-start').disabled = $('emu-src').disabled = s.status === 'unavailable';
  if (!['stopped', 'failed', 'unavailable'].includes(s.status)) emuTimer = setTimeout(pollEmu, running ? 5000 : 1000);
}

// ----------------------------------------------------------------
function render() { loadFirmwares().then(renderMods); }
(async () => {
  try { await Promise.all([loadFirmwares(), loadMods()]); renderMods(); await loadBuilds(); pollEmu(); }
  catch (err) { document.querySelector('main').insertAdjacentHTML('afterbegin', `<div class="card wide" style="color:var(--bad)">${esc(err.message)}</div>`); }
})();
