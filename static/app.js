/* Sprite Studio 前端 — 無框架單檔 */
'use strict';

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = s => String(s == null ? '' : s).replace(/[&<>"]/g,
  c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

/* 內嵌 SVG sprite 的圖示（symbol 定義在 index.html 最上面） */
const ic = n => `<svg class="ic"><use href="#i-${n}"/></svg>`;

const S = {
  cfg: null, spec: null, settings: null, workflows: [],
  pid: localStorage.getItem('ss_pid') || null,
  proj: null, view: 'character',
  clip: null, refs: new Set(), cand: null, base: '',
  jobs: {}, lastFrameClick: null, cursor: 0,
  // fal.ai 線上目錄：models 是清單、index 是 id → 模型資訊、schemas 是各模型的參數表
  fal: { models: { image: [], video: [] }, index: {}, schemas: {}, key: {}, error: '' },
  styles: new Set(), charCand: null,
  // 組合式提示詞：promptDirty＝使用者手改過提示詞，選單就不再覆蓋；comboPid＝組合已還原到哪個專案
  promptDirty: false, comboPid: null,
};

/* ---------------------------------------------------------------- 基礎 */

async function api(path, opts = {}) {
  const o = { ...opts };
  if (o.body && typeof o.body !== 'string' && !(o.body instanceof FormData)) {
    o.body = JSON.stringify(o.body);
    o.headers = { 'Content-Type': 'application/json', ...(o.headers || {}) };
  }
  const r = await fetch(path, o);
  const txt = await r.text();
  let data = null;
  try { data = txt ? JSON.parse(txt) : null; } catch (e) { data = { raw: txt }; }
  if (!r.ok) {
    const msg = (data && (data.description || data.error || data.message)) ||
      (data && data.raw ? String(data.raw).replace(/<[^>]+>/g, '').trim().slice(0, 200) : r.statusText);
    throw new Error(msg);
  }
  return data;
}

function toast(msg, kind = '') {
  const el = document.createElement('div');
  el.className = 'toast ' + kind;
  el.textContent = msg;
  $('#toasts').appendChild(el);
  const t = kind === 'err' ? 5200 : 2500;
  setTimeout(() => { el.style.opacity = '0'; el.style.transition = '.3s'; }, t);
  setTimeout(() => el.remove(), t + 400);
}

function showModal(title, bodyHTML, onOk, okLabel = '確定', hideCancel) {
  $('#modalTitle').textContent = title;
  $('#modalBody').innerHTML = bodyHTML;
  $('#modalOk').textContent = okLabel;
  $('#modalCancel').style.display = hideCancel ? 'none' : '';
  $('#modal').hidden = false;
  const close = () => { $('#modal').hidden = true; $('#modalCancel').style.display = ''; };
  $('#modalOk').onclick = async () => { close(); if (onOk) await onOk(); };
  $('#modalCancel').onclick = close;
}

const fmt$ = n => '$' + (+n || 0).toFixed(3);
const ago = t => {
  const d = (Date.now() / 1000) - t;
  if (d < 60) return '剛剛';
  if (d < 3600) return Math.floor(d / 60) + ' 分前';
  if (d < 86400) return Math.floor(d / 3600) + ' 小時前';
  return Math.floor(d / 86400) + ' 天前';
};
const fileUrl = p => p ? `/files/${S.pid}/${p}` : '';
/* 在檔案總管開啟並選取檔案；relPath 相對於專案資料夾，abs 則是絕對路徑 */
async function reveal(relPath, opts = {}) {
  try {
    const r = await api('/api/reveal', {
      method: 'POST',
      body: opts.abs ? { abs: opts.abs } : { pid: opts.pid || S.pid, path: relPath },
    });
    toast('已開啟 ' + (r.is_dir ? '資料夾' : '檔案位置'));
  } catch (e) { toast(e.message, 'err'); }
}

/* 綁右鍵：在素材縮圖上按右鍵＝開啟該檔位置 */
function bindReveal(sel, getPath) {
  $$(sel).forEach(el => el.oncontextmenu = e => {
    const p = getPath(el);
    if (!p) return;
    e.preventDefault();
    reveal(p);
  });
}

const numOr = (el, d) => { const v = parseFloat($(el).value); return isNaN(v) ? d : v; };

/* ---------------------------------------------------------------- 工作佇列 */

function trackJob(id, onUpdate) {
  return new Promise((resolve, reject) => {
    const tick = async () => {
      let j;
      try { j = await api('/api/jobs/' + id); } catch (e) { return reject(e); }
      S.jobs[id] = j;
      renderJobs();
      if (onUpdate) onUpdate(j);
      if (j.status === 'done') return resolve(j);
      if (j.status === 'error') return reject(new Error(j.error || '工作失敗'));
      setTimeout(tick, 1100);
    };
    tick();
  });
}

function renderJobs() {
  const list = Object.values(S.jobs).sort((a, b) => b.created - a.created).slice(0, 12);
  const running = list.filter(j => j.status === 'running' || j.status === 'queued').length;
  const badge = $('#jobBadge');
  badge.textContent = running;
  badge.classList.toggle('on', running > 0);
  $('#jobsList').innerHTML = list.length ? list.map(j => `
    <div class="job ${j.status}">
      <b>${esc(j.label)}</b>
      <div class="msg">${esc(j.message || j.status)}</div>
      <div class="pbar"><i style="width:${j.status === 'done' ? 100 : (j.progress || 0)}%"></i></div>
    </div>`).join('') : '<div class="empty">沒有進行中的工作</div>';
}

/* ---------------------------------------------------------------- 啟動 */

async function boot() {
  S.cfg = await api('/api/config');
  S.settings = S.cfg.settings;
  S.fal.key = S.settings.fal || {};
  try { S.spec = await api('/api/spec'); } catch (e) { S.spec = { required: [], norms: {}, skins: [] }; }
  try { S.workflows = await api('/api/local/workflows'); } catch (e) { S.workflows = []; }
  bindGlobal();
  fillModelSelects();
  await refreshProjects();
  setView(location.hash.slice(1) || 'character');
  renderKeyDot();
  loadFalCatalog();            // 背景載入線上模型清單，載到再重畫選單
  verifyKey().catch(() => { });
  setInterval(() => { if (Object.values(S.jobs).some(j => j.status === 'running')) renderJobs(); }, 2000);
}

/* ---------------------------------------------------------------- fal.ai 目錄與金鑰 */

/* 向後端要 fal.ai 的線上模型清單（後端有 12 小時快取；refresh 才真的重打網路） */
async function loadFalCatalog(refresh) {
  if (S.settings.fal && S.settings.fal.catalog === false) {
    S.fal.models = { image: [], video: [] };
    fillModelSelects();
    return;
  }
  const q = refresh ? '&refresh=1' : '';
  const errs = [];
  for (const kind of ['image', 'video']) {
    try {
      const r = await api(`/api/fal/models?kind=${kind}${q}`);
      S.fal.models[kind] = r.items || [];
      (r.items || []).forEach(m => S.fal.index[m.id] = m);
      if (!r.ok && r.error) errs.push(r.error);
    } catch (e) { errs.push(e.message); }
  }
  S.fal.error = errs.join(' / ');
  fillModelSelects();
  if (S.proj) renderCharGen();         // 清單到齊後才選得到文生圖模型
  if (S.view === 'settings') renderFalSettings();
  if (refresh) {
    const n = S.fal.models.image.length + S.fal.models.video.length;
    toast(n ? `fal.ai 線上模型 ${n} 個` : ('抓不到線上清單：' + S.fal.error), n ? 'ok' : 'err');
  }
}

/* 某個模型吃哪些參數。回 null＝拿不到（本機模型或離線），呼叫端要自己有後備 */
async function modelSchema(kind, key) {
  if (!key || key.startsWith('local')) return null;
  const ck = kind + ':' + key;
  if (ck in S.fal.schemas) return S.fal.schemas[ck];
  let sch = null;
  try {
    const r = await api(`/api/fal/schema?kind=${kind}&id=${encodeURIComponent(key)}`);
    sch = r.ok ? r.schema : null;
  } catch (e) { sch = null; }
  S.fal.schemas[ck] = sch;
  return sch;
}

/* 選單值 → {label, price, note, local}；內建短名與 fal endpoint id 都吃 */
function modelInfo(kind, key) {
  const table = S.cfg[kind + '_models'] || {};
  if (table[key]) return { ...table[key], key };
  // price 可能是 null＝fal 沒公布單次價格（例如「按百萬像素計費」），別當成 0
  const m = S.fal.index[key];
  if (m) {
    return {
      key, label: m.label + (m.deprecated ? '（已淘汰）' : ''), price: m.price,
      note: [m.note, m.price_text].filter(Boolean).join(' · '), local: false,
    };
  }
  return { key, label: key || '—', price: 0, note: '', local: /^local/.test(key || '') };
}

/* 價格未知時不要印 $0.000——那看起來像免費 */
function costText(m, n) {
  if (m.local) return '本機生成 · 不計費';
  if (m.price == null) return '價格未知（fal 沒公布單次價）';
  return `約 ${fmt$(m.price * (n || 1))}`;
}

function renderKeyDot() {
  const dot = $('#keyDot');
  const k = S.fal.key || {};
  dot.className = 'keydot' + (k.verified ? ' ok' : (k.has_key ? '' : ' err'));
  $('#btnFalKey').title = k.has_key
    ? `fal.ai 金鑰來源：${{ settings: '設定頁', env: '環境變數 FAL_KEY', secrets: 'secrets.json' }[k.source] || k.source}`
    : '還沒有 fal.ai 金鑰——雲端生成會失敗';
}

async function verifyKey(key) {
  const r = await api('/api/fal/verify', { method: 'POST', body: { key: key || '' } });
  if (!key) { S.fal.key.verified = !!r.ok; renderKeyDot(); }
  return r;
}

function showKeyModal() {
  const k = S.fal.key || {};
  showModal('fal.ai API 金鑰', `
    <p class="note" style="margin-top:0">目前：${k.has_key
      ? `<b style="color:var(--ok)">已設定</b>（${{ settings: '設定頁', env: '環境變數 FAL_KEY', secrets: 'secrets.json' }[k.source] || k.source}）${k.masked ? ' <span class="mono dim">' + esc(k.masked) + '</span>' : ''}`
      : '<b style="color:var(--err)">沒有金鑰</b>，雲端生成會直接失敗'}</p>
    <label class="f block">金鑰（<code>id:secret</code>，留空＝不變更）
      <input id="mdKey" type="password" placeholder="貼上 fal.ai 金鑰"></label>
    <div class="ping" id="mdKeyPing">存進 sprite_studio/settings.json，優先於環境變數與舊的 secrets.json。</div>
    <div class="row"><button class="btn xs" id="mdKeyTest">測試這把金鑰</button>
      <button class="btn xs danger" id="mdKeyClear">清除已存金鑰</button></div>`,
    async () => {
      const v = $('#mdKey').value.trim();
      if (!v) return;
      S.fal.key = await api('/api/fal/key', { method: 'POST', body: { key: v } });
      await verifyKey();
      renderKeyDot();
      if (S.view === 'settings') renderFalSettings();
      toast(S.fal.key.verified ? '金鑰已儲存並驗證通過' : '金鑰已儲存，但驗證沒過', S.fal.key.verified ? 'ok' : 'err');
    }, '儲存');
  $('#mdKeyTest').onclick = async () => {
    const el = $('#mdKeyPing');
    el.className = 'ping'; el.textContent = '驗證中…';
    const r = await verifyKey($('#mdKey').value.trim());
    el.className = 'ping ' + (r.ok ? 'ok' : 'err');
    el.textContent = r.ok ? '這把金鑰可以用' : ('不能用：' + (r.error || '').slice(0, 140));
  };
  $('#mdKeyClear').onclick = async () => {
    S.fal.key = await api('/api/fal/key', { method: 'POST', body: { key: '' } });
    await verifyKey();
    $('#modal').hidden = true;
    if (S.view === 'settings') renderFalSettings();
    toast('已清除設定頁金鑰' + (S.fal.key.has_key ? `，改用${S.fal.key.source === 'env' ? '環境變數' : 'secrets.json'}` : ''), 'ok');
  };
}

function setView(v) {
  if (!$(`.view[data-view="${v}"]`)) v = 'character';
  S.view = v;
  location.hash = v;
  $$('.view').forEach(e => e.classList.toggle('on', e.dataset.view === v));
  $$('.step').forEach(e => e.classList.toggle('on', e.dataset.view === v));
  if (v === 'poses') renderPoses();
  if (v === 'anim') renderAnim();
  if (v === 'export') renderExport();
  if (v === 'library') renderLibrary();
  if (v === 'settings') renderSettings();
}

/* 選單分三組：內建（實測過的配方）、fal.ai 線上清單、本機模型 */
function modelOptions(kind) {
  const table = S.cfg[kind + '_models'] || {};
  const builtinIds = new Set(Object.values(table).map(m => m.id));
  const opt = (v, label, title) =>
    `<option value="${esc(v)}" title="${esc(title || '')}">${esc(label)}</option>`;
  const builtin = Object.entries(table).filter(([, m]) => !m.local)
    .map(([k, m]) => opt(k, m.label, m.note)).join('');
  const local = Object.entries(table).filter(([, m]) => m.local)
    .map(([k, m]) => opt(k, m.label, m.note)).join('');
  const online = (S.fal.models[kind] || []).filter(m => !builtinIds.has(m.id))
    .map(m => opt(m.id, `${m.label}${m.price ? ` · $${m.price}` : ''}`,
      `${m.id}\n${m.note || ''}`)).join('');
  return `<optgroup label="內建配方">${builtin}</optgroup>` +
    (online ? `<optgroup label="fal.ai 線上（${(S.fal.models[kind] || []).length}）">${online}</optgroup>` : '') +
    (local ? `<optgroup label="本機">${local}</optgroup>` : '');
}

function fillModelSelects() {
  const img = modelOptions('image');
  const vid = modelOptions('video');
  // 換過選項後 value 會被清掉，先記下來再放回去
  const keep = {};
  const all = ['#poseModel', '#setImgModel', '#charModel', '#clipModel', '#setVidModel'];
  all.forEach(s => keep[s] = $(s) && $(s).value);
  ['#poseModel', '#setImgModel', '#charModel'].forEach(s => $(s).innerHTML = img);
  ['#clipModel', '#setVidModel'].forEach(s => $(s).innerHTML = vid);
  all.forEach(s => { if (keep[s] && $(s).querySelector(`option[value="${CSS.escape(keep[s])}"]`)) $(s).value = keep[s]; });
  const wfOpts = kinds => '<option value="">（未指定）</option>' + S.workflows
    .filter(w => !kinds || kinds.includes(w.kind || 'image'))
    .map(w => `<option value="${esc(w.file)}">${esc(w.label)}</option>`).join('');
  $('#locImgWf').innerHTML = wfOpts(['image']);
  // 姿勢頁同時會用到整張重繪與局部替換兩種
  $('#poseLocalWf').innerHTML = wfOpts(['image', 'inpaint']);
  $('#locVidWf').innerHTML = wfOpts(['video']);
  $('#clipLocalWf').innerHTML = wfOpts(['video']);
}

function bindGlobal() {
  $$('.step').forEach(b => b.onclick = () => setView(b.dataset.view));
  $('#btnJobs').onclick = () => { $('#jobsPanel').hidden = !$('#jobsPanel').hidden; renderJobs(); };
  $('#btnCloseJobs').onclick = () => $('#jobsPanel').hidden = true;
  // 分頁列可以用滾輪橫向捲
  $('#projTabs').addEventListener('wheel', e => {
    if (!e.deltaY) return;
    e.preventDefault();
    $('#projTabs').scrollLeft += e.deltaY;
  }, { passive: false });
  $('#btnHelp').onclick = showHelp;
  $('#btnFalKey').onclick = showKeyModal;

  /* --- 角色生成：組合式提示詞 --- */
  ['#partStyle', '#partAngle', '#partPose'].forEach(sel =>
    $(sel).onchange = () => applyCompose());
  // 這裡的「面向」就是專案的朝向（動畫全靠它），改完直接存回專案
  $('#partFacing').onchange = () => { applyCompose(); saveCharFields(); };
  $('#charExtra').oninput = () => applyCompose();
  $('#charDesc').onchange = saveCharFields;
  $('#btnRecompose').onclick = () => applyCompose(true);
  $('#charPrompt').oninput = () => { S.promptDirty = true; updateComposeState(); };
  $('#charNegPrompt').oninput = () => { S.promptDirty = true; updateComposeState(); };
  $('#btnAddPose').onclick = addCustomPose;
  $('#btnDelPose').onclick = delCustomPose;
  $('#charModel').onchange = () => { S.charModelTouched = true; syncCharModel(); };
  $('#charN').oninput = updateCharCost;
  $('#btnCharGen').onclick = genChar;
  $('#styleSlotImg').onclick = () => $('#styleFile').click();
  $('#styleFile').onchange = e => addStyleFiles([...e.target.files]);
  dropZone('#slotStyle', files => addStyleFiles(files));
  $('#btnRevealCharCand').onclick = () =>
    reveal(S.charCand ? 'char_candidates/' + S.charCand.id : 'char_candidates');
  $('#charHistory').onchange = () => {
    S.charCand = (S.proj.char_cands || []).find(c => c.id === $('#charHistory').value) || null;
    renderCharCandidates();
  };

  /* --- 角色（結果預覽：只能由候選圖產生，不吃外部圖） --- */
  $('#btnRegreen').onclick = async () => {
    try {
      const r = await api(`/api/projects/${S.pid}/source/regreen`,
        { method: 'POST', body: { tol: numOr('#greenTol', 26) } });
      await loadProject(S.pid);
      toast(`已重轉，綠幕佔比 ${Math.round((r.green_ratio || 0) * 100)}%`, 'ok');
    } catch (e) { toast(e.message, 'err'); }
  };
  $('#btnRevealSrc').onclick = () => reveal((S.proj.source || {}).file || 'source');
  $('#btnRevealCand').onclick = () => reveal(S.cand ? 'candidates/' + S.cand.id : 'candidates');
  $('#btnRevealPoses').onclick = () => reveal('poses');
  $('#btnRevealFrames').onclick = () => {
    const t = curTake();
    reveal(t ? t.frames_dir : 'frames');
  };
  $('#btnRevealOut').onclick = () => reveal('out');
  $('#btnRevealWf').onclick = async () => {
    try {
      const r = await api('/api/reveal', { method: 'POST', body: { path: 'workflows' } });
      toast('已開啟 workflows 資料夾');
    } catch (e) { toast(e.message, 'err'); }
  };
  /* --- 姿勢 --- */
  $('#posePreset').onchange = () => {
    const p = S.cfg.pose_presets.find(x => x.key === $('#posePreset').value);
    if (p) { $('#posePrompt').value = p.prompt; if (p.key !== 'custom') $('#poseKey').value = p.key; }
  };
  $('#poseModel').onchange = () => { updatePoseCost(); syncPoseLocal(); };
  $('#poseN').oninput = updatePoseCost;
  $('#btnPoseGen').onclick = genPoses;
  bindMask();
  $('#btnRefClear').onclick = () => { S.refs.clear(); renderPoses(); };
  $('#btnRefUpload').onclick = () => $('#refFile').click();
  $('#refFile').onchange = e => addRefFiles([...e.target.files]);
  $('#charBase').onchange = () => { S.base = $('#charBase').value; renderSlots(); };
  $('#refSlotImg').onclick = () => $('#refFile').click();
  dropZone('#slotRef', files => addRefFiles(files));
  $('#btnPoseUpload').onclick = () => $('#poseUploadFile').click();
  $('#poseUploadFile').onchange = async e => {
    const f = e.target.files[0]; if (!f) return;
    const key = prompt('存成哪個姿勢名？', $('#poseKey').value || 'stand');
    if (!key) return;
    const fd = new FormData(); fd.append('file', f); fd.append('key', key);
    await api(`/api/projects/${S.pid}/pose_upload`, { method: 'POST', body: fd });
    await loadProject(S.pid); renderPoses(); toast('已加入姿勢圖', 'ok');
  };
  $('#candHistory').onchange = () => {
    S.cand = (S.proj.candidates || []).find(c => c.id === $('#candHistory').value) || null;
    renderCandidates();
  };

  /* --- 動作 --- */
  $('#btnAddClip').onclick = () => { $('#presetAdd').hidden = !$('#presetAdd').hidden; };
  $('#btnCancelClip').onclick = () => $('#presetAdd').hidden = true;
  $('#btnDelClip').onclick = async () => {
    if (!S.clip || !confirm(`刪除動作「${S.clip}」？`)) return;
    await api(`/api/projects/${S.pid}/clips/${S.clip}`, { method: 'DELETE' });
    S.clip = null; await loadProject(S.pid); renderAnim();
  };
  $('#presetSelect').onchange = () => {
    const p = S.cfg.anim_presets.find(x => x.key === $('#presetSelect').value);
    if (p) $('#newClipName').value = p.key === 'custom' ? '' : p.key;
  };
  $('#btnCreateClip').onclick = createClip;
  $('#btnScaffold2').onclick = doScaffold;
  $('#btnGenVideo').onclick = genVideo;
  $('#btnImportVideo').onclick = () => $('#clipImport').click();
  $('#clipImport').onchange = async e => {
    const f = e.target.files[0]; if (!f || !S.clip) return;
    const fd = new FormData(); fd.append('file', f);
    const { job } = await api(`/api/projects/${S.pid}/clips/${S.clip}/import`, { method: 'POST', body: fd });
    toast('匯入中…');
    try { await trackJob(job); } catch (err) { return toast(err.message, 'err'); }
    await loadProject(S.pid); renderAnim(); toast('匯入完成', 'ok');
  };
  $('#clipModel').onchange = () => { syncModelUI(); saveClip(); };
  $('#clipPose').onchange = () => { renderPoseThumb(); saveClip(); };
  $('#btnClipPoseUpload').onclick = () => {
    if (!S.clip) { toast('先選一個動作', 'err'); return; }
    $('#clipPoseFile').value = '';
    $('#clipPoseFile').click();
  };
  $('#clipPoseFile').onchange = e => e.target.files[0] && uploadClipPose(e.target.files[0]);
  dropZone('#clipPoseThumb', files => {
    if (!S.clip) { toast('先選一個動作', 'err'); return; }
    uploadClipPose(files[0]);
  });
  ['clipPrompt', 'clipNeg', 'clipDuration', 'clipRes', 'clipAspect', 'clipSeed',
    'clipCfg', 'clipCamFixed', 'clipLocalWf', 'clipFrames', 'clipGenFps', 'clipSteps',
    'clipW', 'clipH', 'clipXalign', 'clipDespeckle', 'clipClearWarm'].forEach(
      id => $('#' + id).onchange = saveClip);
  ['fpsInput', 'loopInput', 'alignInput', 'scaleInput'].forEach(id => $('#' + id).onchange = saveClip);
  $('#btnPlay').onclick = () => Player.toggle();
  $('#bgSelect').onchange = () => Player.draw(true);
  ['flipInput', 'guideInput', 'onionInput'].forEach(id => $('#' + id).onchange = () => Player.draw(true));
  $('#refInput').onchange = () => { Player.loadRef(); Player.draw(true); };
  $('#zoomSelect').onchange = () => Player.resize();
  $('#btnRange').onclick = applyRange;
  $('#btnInvert').onclick = () => {
    const t = curTake(); if (!t) return;
    const sel = new Set(curClip().frames || []);
    setFrames([...Array(t.count).keys()].filter(i => !sel.has(i)));
  };
  $('#btnClearSel').onclick = () => setFrames([]);
  $('#btnLoopDetect').onclick = loopDetect;
  $('#btnSegments').onclick = jumpSegments;
  $('#btnSaveSeg').onclick = saveSelectionAsClip;
  $('#btnReextract').onclick = reextract;
  $('#thumbSize').oninput = () => {
    document.documentElement.style.setProperty('--thumb', $('#thumbSize').value + 'px');
    $$('#strip .fr').forEach(el => el.style.width = $('#thumbSize').value + 'px');
  };

  /* --- 輸出 --- */
  $('#btnPack').onclick = doPack;
  $('#btnExportDir').onclick = doExportDir;
  $('#btnPackAll').onclick = () => $$('#packList input:not([disabled])').forEach(i => i.checked = true);
  $('#btnPackNone').onclick = () => $$('#packList input').forEach(i => i.checked = false);
  $('#pvClip').onchange = () => Preview.select();
  $('#pvZoom').onchange = () => Preview.resize();
  $('#pvPlay').onclick = () => Preview.toggle();
  ['pvBg', 'pvFlip', 'pvFps', 'pvLoop'].forEach(id => $('#' + id).onchange = () => Preview.draw());

  /* --- 設定 --- */
  $('#btnFalSave').onclick = async () => {
    const v = $('#falKey').value.trim();
    if (!v) { toast('請先貼上金鑰', 'err'); return; }
    S.fal.key = await api('/api/fal/key', { method: 'POST', body: { key: v } });
    $('#falKey').value = '';
    await verifyKey();
    renderFalSettings();
    toast(S.fal.key.verified ? '金鑰已儲存並驗證通過' : '金鑰已儲存，但驗證沒過',
      S.fal.key.verified ? 'ok' : 'err');
  };
  $('#btnFalTest').onclick = async () => {
    const el = $('#falPing');
    el.className = 'ping'; el.textContent = '驗證中…';
    const r = await verifyKey($('#falKey').value.trim());
    el.className = 'ping ' + (r.ok ? 'ok' : 'err');
    el.textContent = r.ok ? '金鑰可以用' : ('不能用：' + (r.error || '').slice(0, 140));
  };
  $('#btnFalClear').onclick = async () => {
    S.fal.key = await api('/api/fal/key', { method: 'POST', body: { key: '' } });
    await verifyKey();
    renderFalSettings();
    toast('已清除設定頁金鑰', 'ok');
  };
  $('#btnFalReload').onclick = () => loadFalCatalog(true);
  $('#falCatalog').onchange = async () => {
    S.settings = await api('/api/settings',
      { method: 'PATCH', body: { fal: { catalog: $('#falCatalog').checked } } });
    await loadFalCatalog();
  };
  $('#btnPingComfy').onclick = () => pingLocal('comfyui');
  $('#btnPingA1111').onclick = () => pingLocal('a1111');
  $('#btnSaveSettings').onclick = saveSettings;
  $('#btnReloadModels').onclick = loadLocalModels;
  $('#locImgKind').onchange = () => { loadLocalModels(); };
  $('#btnWfUpload').onclick = () => $('#wfFile').click();
  $('#wfFile').onchange = async e => {
    const f = e.target.files[0]; if (!f) return;
    const fd = new FormData(); fd.append('file', f);
    const r = await api('/api/local/workflows', { method: 'POST', body: fd });
    S.workflows = r.workflows; fillModelSelects(); renderSettings();
    toast('已匯入 ' + r.name, 'ok');
  };
  $('#btnWfPaste').onclick = () => showModal('貼上 workflow（API 格式 JSON）', `
    <label class="f block">檔名<input id="wfName" value="my_workflow.json"></label>
    <label class="f block">內容<textarea id="wfBody" rows="10" placeholder='{"1":{"class_type":...}}'></textarea></label>`,
    async () => {
      try {
        const r = await api('/api/local/workflows', {
          method: 'POST', body: { name: $('#wfName').value, content: $('#wfBody').value }
        });
        S.workflows = r.workflows; fillModelSelects(); renderSettings();
        toast('已存成 ' + r.name, 'ok');
      } catch (e) { toast(e.message, 'err'); }
    }, '儲存');

  window.addEventListener('keydown', e => {
    if (['INPUT', 'TEXTAREA', 'SELECT'].includes(e.target.tagName)) return;
    if (e.key === '?' || (e.shiftKey && e.key === '/')) { showHelp(); return; }
    if (S.view !== 'anim' || !curTake()) return;
    const step = e.shiftKey ? 10 : 1;
    if (e.code === 'Space') { e.preventDefault(); Player.toggle(); }
    else if (e.key === 'ArrowRight') { e.preventDefault(); moveCursor(step); }
    else if (e.key === 'ArrowLeft') { e.preventDefault(); moveCursor(-step); }
    else if (e.key.toLowerCase() === 'a') {
      const cur = new Set(curClip().frames || []);
      cur.has(S.cursor) ? cur.delete(S.cursor) : cur.add(S.cursor);
      setFrames([...cur]);
    }
    else if (e.key === '[') { $('#rngStart').value = S.cursor; toast('區間起點 = ' + S.cursor); }
    else if (e.key === ']') { $('#rngEnd').value = S.cursor; applyRange(); }
    else if (e.key.toLowerCase() === 'o') { $('#onionInput').checked = !$('#onionInput').checked; Player.draw(true); }
    else if (e.key.toLowerCase() === 'f') { $('#flipInput').checked = !$('#flipInput').checked; Player.draw(true); }
  });
}

/* 讓一塊區域接受從檔案總管拖進來的圖片 */
function dropZone(sel, onFiles) {
  const el = $(sel); if (!el) return;
  ['dragenter', 'dragover'].forEach(ev => el.addEventListener(ev, e => {
    e.preventDefault(); e.stopPropagation(); el.classList.add('over');
  }));
  ['dragleave', 'drop'].forEach(ev => el.addEventListener(ev, e => {
    e.preventDefault(); e.stopPropagation();
    if (ev === 'dragleave' && el.contains(e.relatedTarget)) return;
    el.classList.remove('over');
    if (ev === 'drop') {
      const files = [...(e.dataTransfer.files || [])].filter(f => f.type.startsWith('image/'));
      if (files.length) onFiles(files);
    }
  }));
}

/* 更換角色：拖進來的圖直接變成專案的角色原圖（自動轉綠幕） */
/* 加入姿勢參考圖，並自動勾選 */
async function addRefFiles(files) {
  if (!files || !files.length) return;
  try {
    for (const f of files) {
      const fd = new FormData(); fd.append('file', f);
      const r = await api(`/api/projects/${S.pid}/poseref`, { method: 'POST', body: fd });
      S.refs.add(r.file);
    }
    await loadProject(S.pid); renderPoses();
    toast(`已加入 ${files.length} 張參考姿勢`, 'ok');
  } catch (e) { toast(e.message, 'err'); }
}

/* 兩格預覽：左＝角色原圖、右＝第一張參考 */
function renderSlots() {
  const p = S.proj; if (!p) return;
  const src = p.source || {};
  const opts = [['', '專案角色原圖']].concat(
    Object.entries(p.poses || {}).map(([k, v]) => [v.file, '姿勢 ' + k]));
  const sel = $('#charBase');
  sel.innerHTML = opts.map(([v, l]) => `<option value="${esc(v)}">${esc(l)}</option>`).join('');
  if (S.base && opts.some(o => o[0] === S.base)) sel.value = S.base; else S.base = '';
  const baseFile = S.base || src.green || src.file;
  $('#charSlotImg').innerHTML = baseFile
    ? `<img src="${fileUrl(baseFile)}?v=${(p.updated | 0)}">`
    : '<span class="ph-hint">還沒有角色圖——先去「角色生成」選一張</span>';

  const first = [...S.refs][0];
  const url = !first ? null : (first.startsWith('/poselib/') ? first : fileUrl(first));
  $('#refSlotImg').innerHTML = url ? `<img src="${url}">`
    : '<span class="ph-hint">拖姿勢圖進來</span>';
  $('#refCount').textContent = S.refs.size ? `已選 ${S.refs.size} 張` : '';
}

function showHelp() {
  showModal('快捷鍵', `<div class="help-grid">
    <div><span class="kbd">空白</span>播放 / 暫停</div>
    <div><span class="kbd">←</span><span class="kbd">→</span>逐幀移動（<span class="kbd">Shift</span> 一次 10 幀）</div>
    <div><span class="kbd">A</span>切換目前幀的選取</div>
    <div><span class="kbd">[</span><span class="kbd">]</span>設定區間起點 / 迄點並套用</div>
    <div><span class="kbd">O</span>洋蔥皮　<span class="kbd">F</span>水平翻轉</div>
    <div style="color:var(--dim2);margin-top:4px">膠片：點擊切換、Shift＋點擊連選、按住拖曳刷選一段。</div>
    <div style="color:var(--dim2)">在候選圖／姿勢圖／膠片幀上按 <span class="kbd">右鍵</span> ＝ 在檔案總管開啟該檔。</div>
  </div>`, null, '關閉', true);
}

/* ---------------------------------------------------------------- 專案 */

async function refreshProjects(keep) {
  const list = await api('/api/projects');
  S.projects = list;
  if (list.length) {
    if (!S.pid || !list.some(p => p.id === S.pid)) S.pid = list[0].id;
    if (!keep || !S.proj) await loadProject(S.pid);
  } else { S.pid = null; S.proj = null; }
  renderProjTabs();
}

/* 專案分頁列：一個專案一個 tab，右鍵選單可複製／改名／刪除 */
function renderProjTabs() {
  const list = S.projects || [];
  const el = $('#projTabs');
  el.innerHTML = list.map(p => `
    <button class="ptab ${p.id === S.pid ? 'on' : ''} ${p.clips ? 'ready' : ''}"
            data-id="${p.id}" title="${esc(p.name)}｜${p.clips} 動作 · ${p.poses} 姿勢 · ${fmt$(p.spend)}">
      <span class="dot"></span><span class="nm">${esc(p.name)}</span>
      ${p.clips ? `<span class="n">${p.clips}</span>` : ''}
    </button>`).join('') +
    '<div class="sepv"></div>' +
    '<button class="ptab-add" id="ptabAdd" title="新專案">＋</button>' +
    (list.length ? '<button class="ptab-add" id="ptabDup" title="複製目前專案">⧉</button>' : '');
  $$('#projTabs .ptab').forEach(b => {
    b.onclick = () => { if (b.dataset.id !== S.pid) selectProject(b.dataset.id); };
    b.oncontextmenu = e => { e.preventDefault(); projTabMenu(b.dataset.id); };
  });
  $('#ptabAdd').onclick = newProject;
  if ($('#ptabDup')) $('#ptabDup').onclick = async () => {
    const p = await api(`/api/projects/${S.pid}/duplicate`, { method: 'POST' });
    await refreshProjects(true); await selectProject(p.id);
    toast('已複製為「' + p.name + '」', 'ok');
  };
  const on = $('#projTabs .ptab.on');
  if (on) on.scrollIntoView({ block: 'nearest', inline: 'nearest' });
}

function projTabMenu(pid) {
  const p = (S.projects || []).find(x => x.id === pid);
  if (!p) return;
  showModal(`專案「${p.name}」`, `<div class="help-grid">
      <div>${p.clips} 個動作 · ${p.poses} 張姿勢 · 花費 ${fmt$(p.spend)}</div>
      <div class="mono dim">${esc(p.id)}</div>
    </div>
    <label class="f block mt">改名<input id="ptabName" value="${esc(p.name)}"></label>
    <div class="row">
      <button class="btn xs" id="ptabReveal">${ic('folder')}開啟資料夾</button>
      <button class="btn xs danger" id="ptabDel">${ic('trash')}刪除專案</button>
    </div>`, async () => {
    const name = $('#ptabName') && $('#ptabName').value.trim();
    if (name && name !== p.name) {
      await api(`/api/projects/${pid}`, { method: 'PATCH', body: { name } });
      await refreshProjects(true);
      if (pid === S.pid) await loadProject(S.pid);
      renderProjTabs();
      toast('已改名', 'ok');
    }
  }, '儲存');
  $('#ptabReveal').onclick = () => { $('#modal').hidden = true; reveal('', { pid }); };
  $('#ptabDel').onclick = async () => {
    if (!confirm(`刪除專案「${p.name}」？所有影片與幀都會消失。`)) return;
    $('#modal').hidden = true;
    await api('/api/projects/' + pid, { method: 'DELETE' });
    if (pid === S.pid) { S.pid = null; S.proj = null; S.clip = null; }
    await refreshProjects();
    setView(S.view);
    toast('已刪除', 'ok');
  };
}

async function selectProject(pid) {
  S.pid = pid; localStorage.setItem('ss_pid', pid);
  S.clip = null; S.cand = null; S.refs.clear();
  S.charCand = null; S.styles.clear();
  await loadProject(pid);
  renderProjTabs();
  setView(S.view);
}

async function loadProject(pid) {
  S.proj = await api('/api/projects/' + pid);
  S.pid = pid;
  localStorage.setItem('ss_pid', pid);
  $('#spend').textContent = fmt$(S.proj.spend);
  renderCharacter();
  renderProjTabs();
  return S.proj;
}

async function newProject() {
  const name = prompt('新專案名稱', '新角色');
  if (!name) return;
  const p = await api('/api/projects', { method: 'POST', body: { name } });
  await refreshProjects(true);
  await selectProject(p.id);
  setView('character');
}

/* 專案面板拿掉後，角色描述／面向／目前組合都在這裡靜靜存回專案（沒有儲存鈕） */
async function saveCharFields(extra) {
  if (!S.pid) return;
  try {
    S.proj = await api(`/api/projects/${S.pid}`, {
      method: 'PATCH',
      body: {
        character: $('#charDesc').value,
        facing: $('#partFacing').value,
        char_combo: comboNow(),
        ...(extra || {}),
      }
    });
  } catch (e) { toast(e.message, 'err'); }
}

/* ---------------------------------------------------------------- 1 角色生成 */

/* --- 組合式提示詞：風格 + 角度 + 面向 + 姿勢 ------------------------------ */

const PART_KINDS = { style: '#partStyle', angle: '#partAngle', facing: '#partFacing', pose: '#partPose' };

function partList(kind) { return ((S.cfg && S.cfg.char_parts) || {})[kind] || []; }

function partBy(kind, key) {
  const list = partList(kind);
  return list.find(x => x.key === key) || list[0] || null;
}

/* 選單重建（自訂姿勢增刪後也走這裡）——保留目前選到的值 */
function buildPartSelects() {
  Object.entries(PART_KINDS).forEach(([kind, sel]) => {
    const el = $(sel), keep = el.value;
    const list = partList(kind);
    if (!list.length) return;
    el.innerHTML = list.map(x =>
      `<option value="${esc(x.key)}">${esc(x.label)}${x.custom ? ' ·自訂' : ''}</option>`).join('');
    if (keep && list.some(x => x.key === keep)) el.value = keep;
  });
}

/* 四段接成一句：{風格開頭}, {角度}, {面向}, {姿勢}. {風格收尾} {額外} {共用尾綴} */
function composeChar() {
  if (!partList('style').length) return null;
  const P = S.cfg.char_parts;
  const st = partBy('style', $('#partStyle').value);
  const an = partBy('angle', $('#partAngle').value);
  const fa = partBy('facing', $('#partFacing').value);
  const po = partBy('pose', $('#partPose').value);
  const useFacing = !an.skip_facing;
  const extra = $('#charExtra').value.trim().replace(/[.,\s]+$/, '');
  const mid = [an.en, useFacing ? fa.en : '', po.en, extra].filter(Boolean).join(', ');
  const head = (st.head || '') + (mid ? ', ' + mid : '') + '.';
  const prompt = [head, st.tail, P.tail].filter(Boolean).join(' ');
  const neg = [P.neg, st.neg, an.neg, useFacing ? fa.neg : '', po.neg]
    .filter(Boolean).join(', ');
  return { prompt, neg, style: st, angle: an, facing: fa, pose: po, useFacing };
}

/* force＝按「重新組合」，會蓋掉手改過的內容 */
function applyCompose(force) {
  const c = composeChar(); if (!c) return;
  if (!S.promptDirty || force) {
    $('#charPrompt').value = c.prompt;
    $('#charNegPrompt').value = c.neg;
    S.promptDirty = false;
  }
  updateComposeState(c);
}

function updateComposeState(c) {
  c = c || composeChar(); if (!c) return;
  $('#partFacing').disabled = !c.useFacing;
  $('#partFacingWrap').classList.toggle('dim', !c.useFacing);
  $('#btnDelPose').disabled = !c.pose.custom;
  $('#composeState').textContent = S.promptDirty
    ? '下面那句已經手動改過，選單不會再覆蓋它（按「重新組合」還原）'
    : '選單一動就會重組下面那句';
  const tips = [];
  if (c.angle.tip) tips.push(c.angle.tip);
  if (c.style.needs_ref && !S.styles.size) tips.push('這個風格要有風格參考圖，記得在上面放一張並選起來。');
  if (!c.useFacing) tips.push('這個角度用不到「面向」，已經停用。');
  $('#charTip').textContent = tips.join('  ');
}

async function addCustomPose() {
  showModal('新增自訂姿勢', `
    <label class="f block">名稱（自己看的）
      <input id="npLabel" placeholder="例如：靠牆站"></label>
    <label class="f block">英文提示詞片段（模型只看這段，接在角度／面向後面）
      <textarea id="npEn" rows="3" placeholder="leaning against a wall with one shoulder, arms crossed"></textarea></label>
    <label class="f block">負面詞（可留空）
      <input id="npNeg" placeholder="standing upright"></label>
    <p class="note">存進 settings.json，所有專案共用。</p>`, async () => {
    const label = $('#npLabel').value.trim(), en = $('#npEn').value.trim();
    try {
      const r = await api('/api/char_parts/pose',
        { method: 'POST', body: { label, en, neg: $('#npNeg').value.trim() } });
      S.cfg.char_parts = r.parts;
      buildPartSelects();
      $('#partPose').value = r.key;
      applyCompose();
      toast('已新增姿勢「' + label + '」', 'ok');
    } catch (e) { toast(e.message, 'err'); }
  }, '新增');
}

async function delCustomPose() {
  const key = $('#partPose').value;
  const po = partBy('pose', key);
  if (!po || !po.custom) { toast('內建姿勢不能刪', 'err'); return; }
  if (!confirm(`刪除自訂姿勢「${po.label}」？`)) return;
  try {
    const r = await api('/api/char_parts/pose', { method: 'DELETE', body: { key } });
    S.cfg.char_parts = r.parts;
    buildPartSelects();
    applyCompose();
    toast('已刪除', 'ok');
  } catch (e) { toast(e.message, 'err'); }
}

/* 換專案時把上次選的組合叫回來 */
function restoreCombo(p) {
  if (S.comboPid === p.id || !$('#partStyle').options.length) return;   // 選單還沒建好就別記帳
  S.comboPid = p.id;
  S.promptDirty = false;
  const c = p.char_combo || {};
  Object.entries(PART_KINDS).forEach(([kind, sel]) => {
    const want = c[kind];
    if (want && partList(kind).some(x => x.key === want)) $(sel).value = want;
  });
  if (!c.facing) $('#partFacing').value = p.facing || 'right';
  $('#charExtra').value = c.extra || '';
  applyCompose(true);
}

function comboNow() {
  return {
    style: $('#partStyle').value, angle: $('#partAngle').value,
    facing: $('#partFacing').value, pose: $('#partPose').value,
    extra: $('#charExtra').value.trim(),
  };
}

/* 生成頁：風格參考槽、組合式提示詞、候選圖 */
function renderCharGen() {
  const p = S.proj; if (!p) return;
  if (!$('#partStyle').options.length) {
    buildPartSelects();
    const d = (p.defaults || {}).image_model || (S.settings.defaults || {}).image_model;
    if (d && $('#charModel').querySelector(`option[value="${CSS.escape(d)}"]`)) $('#charModel').value = d;
  }
  restoreCombo(p);
  // 專案裡沒有的風格圖就從勾選中移除（換專案時會發生）
  const known = new Set((p.style_refs || []).map(r => r.file));
  [...S.styles].forEach(f => { if (!known.has(f)) S.styles.delete(f); });

  $('#styleGrid').innerHTML = (p.style_refs || []).length
    ? p.style_refs.map(r => `
      <div class="ref ${S.styles.has(r.file) ? 'on' : ''}" data-key="${esc(r.file)}">
        <img src="${fileUrl(r.file)}" loading="lazy">
        <button class="del" data-f="${esc(r.file)}" title="移除">✕</button>
        <span>${esc(r.label || '')}</span></div>`).join('')
    : '<div class="empty">沒有風格參考（可留空＝純文字生成）</div>';
  $$('#styleGrid .ref').forEach(el => el.onclick = e => {
    if (e.target.classList.contains('del')) return;
    const k = el.dataset.key;
    S.styles.has(k) ? S.styles.delete(k) : S.styles.add(k);
    renderCharGen();
  });
  $$('#styleGrid .del').forEach(b => b.onclick = async e => {
    e.stopPropagation();
    await api(`/api/projects/${S.pid}/styleref`, { method: 'DELETE', body: { file: b.dataset.f } });
    S.styles.delete(b.dataset.f);
    await loadProject(S.pid);
  });

  const first = [...S.styles][0] || ((p.style_refs || [])[0] || {}).file;
  $('#styleSlotImg').innerHTML = first ? `<img src="${fileUrl(first)}">`
    : '<span class="ph-hint">拖風格圖進來</span>';
  $('#styleCount').textContent = S.styles.size ? `已選 ${S.styles.size} 張` : '未選＝純文字生成';

  const hist = p.char_cands || [];
  $('#charHistory').innerHTML = hist.length
    ? hist.map(c => `<option value="${c.id}">${esc(c.preset || '')} ${esc(c.model)} · ${ago(c.created)}</option>`).join('')
    : '<option>（無紀錄）</option>';
  if ((!S.charCand || !hist.some(c => c.id === S.charCand.id)) && hist.length) S.charCand = hist[0];
  if (!hist.length) S.charCand = null;
  if (S.charCand) $('#charHistory').value = S.charCand.id;
  renderCharCandidates();
  updateComposeState();
  syncCharModel();
}

async function addStyleFiles(files) {
  if (!files || !files.length) return;
  if (!S.pid) { toast('請先建立專案', 'err'); return; }
  try {
    for (const f of files) {
      const fd = new FormData(); fd.append('file', f);
      const r = await api(`/api/projects/${S.pid}/styleref`, { method: 'POST', body: fd });
      S.styles.add(r.file);
    }
    await loadProject(S.pid);
    toast(`已加入 ${files.length} 張風格參考`, 'ok');
  } catch (e) { toast(e.message, 'err'); }
}

/* 很多圖片模型根本沒有 negative_prompt 這格，送了會 422 所以我們不送——要講出來，
   不然使用者會以為負面詞有生效 */
async function markNegSupport(kind, key, hintSel) {
  const el = $(hintSel); if (!el) return;
  const sch = await modelSchema(kind, key);
  if (!sch) { el.textContent = ''; return; }
  el.textContent = ('negative_prompt' in (sch.fields || {})) ? '' : '（這個模型不吃負面提示詞，會被忽略）';
}

/* 不打網路的快速判斷：這個模型要不要吃輸入圖（text-to-image 才不用） */
function modelWantsImage(key) {
  const m = S.fal.index[key];
  if (m) return m.category !== 'text-to-image';
  return true;                 // 內建的三支都是 edit 模型
}

/* 沒放風格參考就自動挑文生圖、放了就挑圖生圖——使用者自己選過就不再插手 */
function autoPickCharModel() {
  if (S.charModelTouched) return;
  const wantImage = S.styles.size > 0;
  const sel = $('#charModel');
  if (!sel.value || modelWantsImage(sel.value) === wantImage) return;
  const has = v => sel.querySelector(`option[value="${CSS.escape(v)}"]`);
  const prefer = wantImage
    ? ['nano-banana', 'fal-ai/nano-banana/edit', 'fal-ai/nano-banana-2/edit', 'seedream4']
    : ['fal-ai/nano-banana-2', 'fal-ai/nano-banana', 'fal-ai/flux/schnell'];
  let pick = prefer.find(has);
  if (!pick) {
    pick = (S.fal.models.image || [])
      .filter(m => (m.category === 'text-to-image') === !wantImage)
      .map(m => m.id).find(has);
  }
  if (pick) sel.value = pick;
}

/* 依模型的 schema 決定比例選單有哪些值、以及還能不能純文字生成 */
async function syncCharModel() {
  autoPickCharModel();
  updateCharCost();
  const key = $('#charModel').value;
  markNegSupport('image', key, '#charNegHint');
  const sch = await modelSchema('image', key);
  const wrap = $('#charAspectWrap');
  const fields = (sch && sch.fields) || null;
  const ar = fields && (fields.aspect_ratio || fields.image_size);
  if (!fields) {
    wrap.hidden = false;
    if (!$('#charAspect').options.length) {
      $('#charAspect').innerHTML = ['1:1', '3:4', '9:16', '4:3']
        .map(v => `<option value="${v}">${v}</option>`).join('');
    }
  } else if (ar && ar.enum) {
    wrap.hidden = false;
    const cur = $('#charAspect').value;
    $('#charAspect').innerHTML = ar.enum.map(v => `<option value="${v}">${v}</option>`).join('');
    $('#charAspect').value = ar.enum.includes(cur) ? cur
      : (ar.default != null && ar.enum.includes(ar.default) ? ar.default : ar.enum[0]);
  } else {
    wrap.hidden = !ar;
    if (ar) {
      $('#charAspect').innerHTML = ['1:1', '3:4', '9:16', '4:3']
        .map(v => `<option value="${v}">${v}</option>`).join('');
    }
  }
  // 模型硬性要圖卻沒放參考＝送出去一定 422，這裡直接擋住並說原因
  const needsImg = sch && (sch.required || []).some(k => k === 'image_url' || k === 'image_urls');
  const takesImg = !sch || Object.keys(sch.fields || {}).some(k => k === 'image_url' || k === 'image_urls');
  const blocked = needsImg && !S.styles.size;
  $('#btnCharGen').disabled = blocked;
  $('#charGenHint').textContent = blocked
    ? `「${modelInfo('image', key).label}」一定要有輸入圖：放一張風格參考，或換成文生圖模型`
    : (!takesImg && S.styles.size ? '這個模型不吃輸入圖，風格參考會被忽略'
      : '立繪只能從這裡生——生完點候選圖設為專案立繪');
}

function updateCharCost() {
  const m = modelInfo('image', $('#charModel').value);
  $('#charCost').textContent = costText(m, +$('#charN').value || 4);
  $('#charModelNote').textContent = (m.note || '').slice(0, 90);
}

async function genChar() {
  if (!S.pid) { toast('請先建立專案', 'err'); return; }
  const prompt = $('#charPrompt').value.trim();
  if (!prompt) { toast('提示詞是空的', 'err'); return; }
  const n = +$('#charN').value || 4;
  const combo = comboNow();
  await saveCharFields();      // 角色描述／面向／這次的組合都記回專案
  const body = {
    model: $('#charModel').value, n, prompt,
    neg: $('#charNegPrompt').value,
    refs: [...S.styles],
    preset: [combo.style, combo.angle, combo.facing, combo.pose].join('·'),
    seed: $('#charSeed').value ? +$('#charSeed').value : null,
    aspect_ratio: $('#charAspectWrap').hidden ? null : $('#charAspect').value,
  };
  $('#btnCharGen').disabled = true;
  try {
    const { job } = await api(`/api/projects/${S.pid}/char_gen`, { method: 'POST', body });
    renderCharCandidates([...Array(n)].map((_, i) => ({ index: i, status: 'queued' })));
    await trackJob(job, j => renderCharCandidates(j.items));
    await loadProject(S.pid);
    S.charCand = (S.proj.char_cands || [])[0];
    renderCharGen();
    toast('候選完成——點一張設成角色立繪', 'ok');
  } catch (e) { toast(e.message, 'err'); }
  finally { $('#btnCharGen').disabled = false; }
}

function renderCharCandidates(liveItems) {
  const grid = $('#charCandGrid');
  const card = (url, file) => `<div class="cand" ${file ? `data-file="${esc(file)}"` : ''}>
      <img src="${url}" loading="lazy"><div class="pick">設為角色立繪</div>
      ${file ? `<button class="reveal" title="在檔案總管開啟">${ic('folder')}</button>` : ''}</div>`;
  if (liveItems) {
    grid.innerHTML = liveItems.map(it =>
      it.status === 'done' ? card(it.url)
        : it.status === 'error' ? `<div class="cand err">失敗<br><small>${esc((it.error || '').slice(0, 90))}</small></div>`
          : '<div class="cand loading"></div>').join('');
  } else if (S.charCand && (S.charCand.images || []).length) {
    grid.innerHTML = S.charCand.images.map(f => card(fileUrl(f), f)).join('');
  } else {
    grid.innerHTML = '<div class="empty">還沒有生成過角色立繪</div>';
    return;
  }
  $$('#charCandGrid .cand[data-file]').forEach(el => el.onclick = e => {
    if (e.target.classList.contains('reveal')) { e.stopPropagation(); return reveal(el.dataset.file); }
    pickChar(el.dataset.file);
  });
  bindReveal('#charCandGrid .cand[data-file]', el => el.dataset.file);
}

async function pickChar(file) {
  if (S.proj.source && S.proj.source.file &&
    !confirm('要用這張換掉目前的角色立繪嗎？（已生成的姿勢與動畫不受影響）')) return;
  try {
    await api(`/api/projects/${S.pid}/char_pick`, { method: 'POST', body: { image: file } });
    await loadProject(S.pid);
    toast('已設為角色立繪（已轉綠幕）——接著去「姿勢修改」', 'ok');
  } catch (e) { toast(e.message, 'err'); }
}

function renderCharacter() {
  const p = S.proj; if (!p) return;
  if (document.activeElement !== $('#charDesc')) $('#charDesc').value = p.character || '';
  const has = p.source && p.source.file;
  $('#srcPreview').hidden = !has;
  $('#srcTools').hidden = !has;
  $('#srcEmpty').hidden = !!has;
  if (has) {
    const v = (p.updated | 0);
    $('#srcImg').src = fileUrl(p.source.file) + '?v=' + v;
    $('#srcGreen').src = fileUrl(p.source.green) + '?v=' + v;
    $('#greenTol').value = p.source.tol || 26;
    $('#greenInfo').textContent = `綠幕佔比 ${Math.round((p.source.green_ratio || 0) * 100)}%`;
  }
  renderCharGen();
}

/* ---------------------------------------------------------------- 2 姿勢 */

function renderPoses() {
  if (!S.proj) return;
  if (!$('#posePreset').options.length) {
    $('#posePreset').innerHTML = S.cfg.pose_presets
      .map(p => `<option value="${p.key}">${esc(p.label)}</option>`).join('');
    $('#posePrompt').value = S.cfg.pose_presets[0].prompt;
    $('#poseKey').value = S.cfg.pose_presets[0].key;
    const d = (S.proj.defaults || {}).image_model || (S.settings.defaults || {}).image_model;
    if (d && $('#poseModel').querySelector(`option[value="${CSS.escape(d)}"]`)) $('#poseModel').value = d;
    $('#poseN').value = (S.settings.defaults || {}).pose_n || 5;
  }
  updatePoseCost(); syncPoseLocal();
  if (!$('#poseNeg').value && S.proj.neg) $('#poseNeg').value = S.proj.neg;

  const items = [
    ...S.cfg.poselib.map(r => ({ url: r.url, key: r.url, label: r.label })),
    ...((S.proj.refs) || []).map(r => ({ url: fileUrl(r.file), key: r.file, label: r.label })),
    ...Object.entries(S.proj.poses || {}).map(([k, v]) => ({ url: fileUrl(v.file), key: v.file, label: k })),
  ];
  $('#refGrid').innerHTML = items.map(r => `
    <div class="ref ${S.refs.has(r.key) ? 'on' : ''}" data-key="${esc(r.key)}">
      <img src="${r.url}" loading="lazy"><span>${esc(r.label)}</span></div>`).join('') ||
    '<div class="empty">沒有參考圖</div>';
  $$('#refGrid .ref').forEach(el => el.onclick = () => {
    const k = el.dataset.key;
    S.refs.has(k) ? S.refs.delete(k) : S.refs.add(k);
    el.classList.toggle('on');
    renderSlots();
  });

  const hist = S.proj.candidates || [];
  $('#candHistory').innerHTML = hist.length
    ? hist.map(c => `<option value="${c.id}">${esc(c.pose_key || '')} ${esc(c.model)} · ${ago(c.created)}</option>`).join('')
    : '<option>（無紀錄）</option>';
  if (!S.cand && hist.length) S.cand = hist[0];
  if (S.cand) $('#candHistory').value = S.cand.id;
  renderCandidates();
  renderPoseGrid();
  renderSlots();
}

function syncPoseLocal() {
  const isLocal = $('#poseModel').value === 'local';
  $('#poseLocalRow').hidden = !isLocal;
  if (isLocal) {
    const li = S.settings.local.image;
    const maskMode = $('.mode-mask') && !$('.mode-mask').hidden;
    $('#poseLocalWf').value = maskMode ? inpaintWorkflow() : (li.workflow || '');
    $('#poseSteps').value = li.steps; $('#poseCfg').value = li.cfg;
    $('#poseLocalVae').value = li.vae || '';
    $('#poseDenoise').value = li.denoise; $('#poseW').value = li.width; $('#poseH').value = li.height;
    loadLocalModels('#poseLocalModel');
  }
}

function updatePoseCost() {
  markNegSupport('image', $('#poseModel').value, '#poseNegHint');
  const m = modelInfo('image', $('#poseModel').value);
  $('#poseCost').textContent = costText(m, +$('#poseN').value || 5);
  $('#poseModelNote').textContent = (m.note || '').slice(0, 110);
}

function renderCandidates(liveItems) {
  const grid = $('#candGrid');
  const card = (url, file) => `<div class="cand" ${file ? `data-file="${esc(file)}"` : ''}>
      <img src="${url}" loading="lazy"><div class="pick">選這張</div>
      ${file ? `<button class="reveal" title="在檔案總管開啟">${ic('folder')}</button>` : ''}</div>`;
  if (liveItems) {
    grid.innerHTML = liveItems.map(it =>
      it.status === 'done' ? card(it.url)
        : it.status === 'error' ? `<div class="cand err">失敗<br><small>${esc((it.error || '').slice(0, 90))}</small></div>`
          : '<div class="cand loading"></div>').join('');
  } else if (S.cand && (S.cand.images || []).length) {
    grid.innerHTML = S.cand.images.map(f => card(fileUrl(f), f)).join('');
  } else {
    grid.innerHTML = '<div class="empty">還沒有候選圖</div>';
    return;
  }
  $$('#candGrid .cand[data-file]').forEach(el => el.onclick = e => {
    if (e.target.classList.contains('reveal')) { e.stopPropagation(); return reveal(el.dataset.file); }
    pickPose(el.dataset.file);
  });
  bindReveal('#candGrid .cand[data-file]', el => el.dataset.file);
}

async function pickPose(file) {
  const key = prompt('存成哪個姿勢名？', $('#poseKey').value || 'stand');
  if (!key) return;
  try {
    await api(`/api/projects/${S.pid}/pose_pick`, { method: 'POST', body: { image: file, key } });
    await loadProject(S.pid); renderPoses();
    toast(`已存成「${key}」，可按卡片上的「做動作」接下一步`, 'ok');
  } catch (e) { toast(e.message, 'err'); }
}

/* 拿某張姿勢圖去做動作：切到動作頁，套用該姿勢的建議動作 */
function toAnim(poseKey) {
  const clips = (S.proj && S.proj.clips) || {};
  const preset = S.cfg.anim_presets.find(p => p.pose === poseKey && clips[p.key]);
  const existing = Object.entries(clips).find(([, c]) => c.pose === poseKey);
  setView('anim');
  if (preset) S.clip = preset.key;
  else if (existing) S.clip = existing[0];
  renderAnim();
  if (preset || existing) {
    toast(`動作「${S.clip}」已使用姿勢 ${poseKey}`);
    return;
  }
  // 還沒有用這張姿勢的動作 → 開新增面板，預選建議的動作
  const sug = S.cfg.anim_presets.find(p => p.pose === poseKey) || S.cfg.anim_presets[0];
  $('#presetAdd').hidden = false;
  $('#presetSelect').value = sug.key;
  $('#newClipName').value = sug.key === 'custom' ? poseKey : sug.key;
  S.newClipPose = poseKey;
  toast(`用姿勢 ${poseKey} 建立動作：選一個動作按「建立」`);
}

function renderPoseGrid() {
  const poses = S.proj.poses || {};
  $('#poseGrid').innerHTML = Object.keys(poses).length ? Object.entries(poses).map(([k, v]) => `
    <div class="pose-card" data-file="${esc(v.file)}">
      <button class="del" data-k="${esc(k)}">✕</button>
      <button class="reveal" title="在檔案總管開啟">${ic('folder')}</button>
      <img src="${fileUrl(v.file)}?v=${(v.created | 0)}">
      <b>${esc(k)}</b>
      <div class="pose-acts">
        <button class="btn xs" data-act="base" data-k="${esc(k)}" title="拿這張當角色原圖再生成別的姿勢">${ic('image')}當原圖</button>
        <button class="btn xs" data-act="anim" data-k="${esc(k)}" title="拿這張去做動作動畫">${ic('wand')}做動作</button>
      </div></div>`).join('')
    : '<div class="empty">還沒有姿勢圖</div>';
  $$('#poseGrid [data-act=base]').forEach(b => b.onclick = e => {
    e.stopPropagation();
    S.base = (S.proj.poses[b.dataset.k] || {}).file || '';
    renderSlots();
    toast(`已把「${b.dataset.k}」設為角色原圖`, 'ok');
  });
  $$('#poseGrid [data-act=anim]').forEach(b => b.onclick = e => {
    e.stopPropagation();
    toAnim(b.dataset.k);
  });
  $$('#poseGrid .reveal').forEach(b => b.onclick = e => {
    e.stopPropagation(); reveal(b.closest('.pose-card').dataset.file);
  });
  bindReveal('#poseGrid .pose-card', el => el.dataset.file);
  $$('#poseGrid .del').forEach(b => b.onclick = async e => {
    e.stopPropagation();
    await api(`/api/projects/${S.pid}/poses/${b.dataset.k}`, { method: 'DELETE' });
    await loadProject(S.pid); renderPoses();
  });
}

/* ---------------- 局部替換：遮罩繪製 ---------------- */

const Mask = {
  base: null, nat: [0, 0],
  imgs() { return { img: $('#maskImg'), paint: $('#maskPaint') }; },
  options() {
    const p = S.proj, out = [];
    if (p.source && p.source.green) out.push([p.source.green, '角色源圖（綠幕）']);
    Object.entries(p.poses || {}).forEach(([k, v]) => out.push([v.file, '姿勢 ' + k]));
    (p.candidates || []).slice(0, 3).forEach(c =>
      (c.images || []).forEach((f, i) => out.push([f, `候選 ${c.pose_key || c.model} #${i}`])));
    return out;
  },
  fill() {
    const opts = this.options();
    const sel = $('#maskBase');
    const keep = sel.value;
    sel.innerHTML = opts.map(([f, l]) => `<option value="${esc(f)}">${esc(l)}</option>`).join('')
      || '<option value="">（沒有可用的圖）</option>';
    if (opts.some(o => o[0] === keep)) sel.value = keep;
    this.load();
  },
  load() {
    const f = $('#maskBase').value;
    if (!f) return;
    this.base = f;
    const im = new Image();
    im.onload = () => {
      this.nat = [im.naturalWidth, im.naturalHeight];
      const disp = Math.min(430, im.naturalWidth);
      const h = Math.round(disp * im.naturalHeight / im.naturalWidth);
      const { img, paint } = this.imgs();
      [img, paint].forEach(c => {
        c.width = disp; c.height = h;
        c.style.width = disp + 'px'; c.style.height = h + 'px';
      });
      img.getContext('2d').drawImage(im, 0, 0, disp, h);
      paint.getContext('2d').clearRect(0, 0, disp, h);
      this.info();
    };
    im.src = fileUrl(f) + '?v=' + Date.now();
  },
  paintAt(x, y, erase) {
    const g = this.imgs().paint.getContext('2d');
    const r = (+$('#brushSize').value || 40) / 2;
    g.globalCompositeOperation = erase ? 'destination-out' : 'source-over';
    g.fillStyle = 'rgba(230,70,70,.55)';
    g.beginPath(); g.arc(x, y, r, 0, Math.PI * 2); g.fill();
    g.globalCompositeOperation = 'source-over';
  },
  clear() { const c = this.imgs().paint; c.getContext('2d').clearRect(0, 0, c.width, c.height); this.info(); },
  invert() {
    const c = this.imgs().paint, g = c.getContext('2d');
    const d = g.getImageData(0, 0, c.width, c.height);
    const px = d.data;
    for (let i = 0; i < px.length; i += 4) {
      const on = px[i + 3] > 8;
      px[i] = 230; px[i + 1] = 70; px[i + 2] = 70; px[i + 3] = on ? 0 : 140;
    }
    g.putImageData(d, 0, 0);
    this.info();
  },
  coverage() {
    const c = this.imgs().paint;
    const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
    let n = 0;
    for (let i = 3; i < d.length; i += 4) if (d[i] > 8) n++;
    return n / (c.width * c.height);
  },
  info() {
    const cov = this.coverage();
    $('#maskInfo').textContent = cov ? `遮罩覆蓋 ${(cov * 100).toFixed(1)}%` : '還沒塗遮罩';
  },
  /* 匯出成原圖尺寸的黑白遮罩：白＝重畫 */
  export() {
    const [w, h] = this.nat;
    const c = document.createElement('canvas');
    c.width = w; c.height = h;
    const g = c.getContext('2d');
    g.fillStyle = '#000'; g.fillRect(0, 0, w, h);
    g.drawImage(this.imgs().paint, 0, 0, w, h);
    const d = g.getImageData(0, 0, w, h), px = d.data;
    for (let i = 0; i < px.length; i += 4) {
      const on = px[i + 3] > 8 && px[i] > 100;
      px[i] = px[i + 1] = px[i + 2] = on ? 255 : 0;
      px[i + 3] = 255;
    }
    g.putImageData(d, 0, 0);
    return c.toDataURL('image/png');
  },
};

function bindMask() {
  $$('.ph.tabs .tab').forEach(b => b.onclick = () => {
    $$('.ph.tabs .tab').forEach(x => x.classList.toggle('on', x === b));
    const mask = b.dataset.mode === 'mask';
    $('.mode-mask').hidden = !mask;
    $('.mode-full').hidden = mask;
    $('#btnPoseGen').textContent = mask ? '局部重繪' : '生成候選';
    // 兩種模式要用不同 workflow，切分頁時自動挑一個對的
    const want = S.workflows.filter(w => (w.kind || 'image') === (mask ? 'inpaint' : 'image'));
    const cur = S.workflows.find(w => w.file === $('#poseLocalWf').value);
    if (want.length && (!cur || (cur.kind || 'image') !== (mask ? 'inpaint' : 'image')))
      $('#poseLocalWf').value = want[0].file;
    $('#poseDenoise').closest('.f').hidden = mask;   // inpaint 的 denoise 固定 1.0
    $('#poseBlend').closest('.f').hidden = mask;
    if (mask) Mask.fill();
  });
  $('#maskBase').onchange = () => Mask.load();
  $('#btnMaskClear').onclick = () => Mask.clear();
  $('#btnMaskInvert').onclick = () => Mask.invert();
  $('#brushSize').oninput = () => $('#brushVal').textContent = $('#brushSize').value;

  const stage = $('#maskPaint');
  let painting = false;
  const pos = e => {
    const r = stage.getBoundingClientRect();
    return [(e.clientX - r.left) * stage.width / r.width, (e.clientY - r.top) * stage.height / r.height];
  };
  const erase = () => $('input[name=brushMode]:checked').value === 'erase';
  stage.onpointerdown = e => {
    painting = true; stage.setPointerCapture(e.pointerId);
    Mask.paintAt(...pos(e), erase());
  };
  stage.onpointermove = e => { if (painting) Mask.paintAt(...pos(e), erase()); };
  stage.onpointerup = stage.onpointercancel = () => { painting = false; Mask.info(); };
}

function inpaintWorkflow() {
  const sel = S.workflows.find(w => w.file === $('#poseLocalWf').value);
  if (sel && sel.kind === 'inpaint') return sel.file;
  const any = S.workflows.find(w => w.kind === 'inpaint');
  return any ? any.file : 'comfy_inpaint.json';
}

async function genInpaint() {
  const cov = Mask.coverage();
  if (!cov) { toast('先在圖上塗要重畫的區域', 'err'); return; }
  const body = {
    base: Mask.base, mask: Mask.export(),
    prompt: $('#posePrompt').value, neg: $('#poseNeg').value,
    n: +$('#poseN').value || 2, pose_key: $('#poseKey').value,
    seed: $('#poseSeed').value ? +$('#poseSeed').value : null,
    local: {
      workflow: inpaintWorkflow(),
      model: $('#poseLocalModel').value, vae: $('#poseLocalVae').value,
      steps: numOr('#poseSteps', 28), cfg: numOr('#poseCfg', 6.5),
      width: numOr('#poseW', 1024), height: numOr('#poseH', 1024),
    },
  };
  $('#btnPoseGen').disabled = true;
  try {
    const { job } = await api(`/api/projects/${S.pid}/inpaint`, { method: 'POST', body });
    renderCandidates([...Array(body.n)].map((_, i) => ({ index: i, status: 'queued' })));
    await trackJob(job, j => renderCandidates(j.items));
    await loadProject(S.pid);
    S.cand = (S.proj.candidates || [])[0];
    renderPoses();
    toast('局部重繪完成', 'ok');
  } catch (e) { toast(e.message, 'err'); }
  finally { $('#btnPoseGen').disabled = false; }
}

async function genPoses() {
  if ($('.mode-mask') && !$('.mode-mask').hidden) return genInpaint();
  if (!S.proj.source || !S.proj.source.file) { toast('請先在「角色」上傳立繪', 'err'); return; }
  const model = $('#poseModel').value;
  const body = {
    model, n: +$('#poseN').value || 5,
    prompt: $('#posePrompt').value,
    neg: $('#poseNeg').value,
    refs: [...S.refs],
    pose_key: $('#poseKey').value,
    seed: $('#poseSeed').value ? +$('#poseSeed').value : null,
    aspect_ratio: $('#poseAspect').value,
    base: S.base || null,
  };
  if (model === 'local') {
    body.local = {
      workflow: $('#poseLocalWf').value, model: $('#poseLocalModel').value,
      steps: numOr('#poseSteps', 28), cfg: numOr('#poseCfg', 6.5),
      denoise: numOr('#poseDenoise', 0.62), blend: numOr('#poseBlend', 0.4),
      vae: $('#poseLocalVae').value,
      width: numOr('#poseW', 1024), height: numOr('#poseH', 1024),
    };
  }
  $('#btnPoseGen').disabled = true;
  try {
    const { job } = await api(`/api/projects/${S.pid}/pose_gen`, { method: 'POST', body });
    renderCandidates([...Array(body.n)].map((_, i) => ({ index: i, status: 'queued' })));
    await trackJob(job, j => renderCandidates(j.items));
    await loadProject(S.pid);
    S.cand = (S.proj.candidates || [])[0];
    renderPoses();
    toast('候選完成', 'ok');
  } catch (e) { toast(e.message, 'err'); }
  finally { $('#btnPoseGen').disabled = false; }
}

/* ---------------------------------------------------------------- 3 動作 */

const curClip = () => (S.proj && S.clip) ? S.proj.clips[S.clip] : null;
function curTake() {
  const c = curClip(); if (!c || !(c.takes || []).length) return null;
  return c.takes.find(t => t.id === c.selected_take) || c.takes[0];
}

function renderAnim() {
  if (!S.proj) return;
  if (!$('#presetSelect').options.length) {
    $('#presetSelect').innerHTML = S.cfg.anim_presets
      .map(p => `<option value="${p.key}">${esc(p.label)}</option>`).join('');
    $('#newClipName').value = S.cfg.anim_presets[0].key;
  }
  const clips = S.proj.clips || {};
  const names = Object.keys(clips);
  if (S.clip && !clips[S.clip]) S.clip = null;
  if (!S.clip && names.length) S.clip = names[0];

  const req = (S.spec && S.spec.required) || [];
  const ordered = [...req.filter(n => names.includes(n)), ...names.filter(n => !req.includes(n))];
  const missing = req.filter(n => !names.includes(n));
  $('#clipList').innerHTML = (ordered.map(n => {
    const c = clips[n];
    const nT = (c.takes || []).length, nS = (c.frames || []).length;
    return `<div class="clip ${nS ? 'ready' : (nT ? 'has-video' : '')} ${n === S.clip ? 'on' : ''}" data-n="${esc(n)}">
      <span class="dot"></span><span class="nm">${esc(n)}</span>
      <span class="st">${nT ? nS + '/' + (c.takes[0].count || '?') : '—'}</span></div>`;
  }).join('') + missing.map(n =>
    `<div class="clip ghost" data-new="${esc(n)}"><span class="dot"></span>
      <span class="nm">${esc(n)}</span><span class="st">未建立</span></div>`).join(''))
    || '<div class="empty">按「補齊」或「新增」</div>';
  $$('#clipList .clip[data-n]').forEach(el => el.onclick = () => { S.clip = el.dataset.n; renderAnim(); });
  $$('#clipList .clip[data-new]').forEach(el => el.onclick = async () => {
    await doScaffold(); S.clip = el.dataset.new; renderAnim();
  });

  renderInspector();
  renderTakes();
  renderStrip();
}

async function createClip() {
  const preset = S.cfg.anim_presets.find(p => p.key === $('#presetSelect').value);
  const name = ($('#newClipName').value || preset.key).trim().replace(/[^\w-]/g, '_');
  if (!name) { toast('請填動作名', 'err'); return; }
  await api(`/api/projects/${S.pid}/clips/${name}`, {
    method: 'PUT',
    body: {
      label: preset.label, pose: S.newClipPose || preset.pose, model: preset.model, prompt: preset.prompt,
      neg: preset.neg, fps: preset.fps, loop: preset.loop, align: preset.align,
      preset: preset.key, duration: preset.model === 'kling25' ? '5' : '4',
      resolution: '720p', scale: 1, xalign: 'median', frames: [],
    }
  });
  await loadProject(S.pid);
  S.clip = name;
  S.newClipPose = null;
  $('#presetAdd').hidden = true;
  renderAnim();
}

async function doScaffold() {
  const r = await api(`/api/projects/${S.pid}/scaffold`, { method: 'POST', body: {} });
  await loadProject(S.pid);
  renderAnim();
  toast(r.created.length ? `已建立 ${r.created.join('、')}` : '動作都在了', 'ok');
}

function renderInspector() {
  const c = curClip();
  const poses = Object.keys(S.proj.poses || {});
  $('#clipPose').innerHTML = poses.map(k => `<option value="${k}">${esc(k)}</option>`).join('')
    || '<option value="">（沒有姿勢圖）</option>';
  if (!c) {
    $('#stageTitle').textContent = '—';
    ['clipPrompt', 'clipNeg'].forEach(i => $('#' + i).value = '');
    $('#clipTip').textContent = ''; $('#clipCost').textContent = '';
    $('#clipNorm').hidden = true;
    return;
  }
  $('#stageTitle').textContent = c.label ? `${S.clip} · ${c.label}` : S.clip;
  $('#clipModel').value = c.model || 'kling25';
  if (poses.includes(c.pose)) $('#clipPose').value = c.pose;
  $('#clipPrompt').value = c.prompt || '';
  $('#clipNeg').value = c.neg || '';
  $('#clipRes').value = c.resolution || '720p';
  $('#clipAspect').value = c.aspect_ratio || '1:1';
  $('#clipSeed').value = c.seed || '';
  $('#clipCfg').value = c.cfg_scale ?? 0.5;
  $('#clipCamFixed').checked = c.camera_fixed !== false;
  $('#fpsInput').value = c.fps || 12;
  $('#loopInput').checked = !!c.loop;
  $('#alignInput').checked = !!c.align;
  $('#scaleInput').value = c.scale || 1;
  $('#clipXalign').value = c.xalign || 'median';
  $('#clipDespeckle').checked = !!c.despeckle;
  $('#clipClearWarm').checked = !!c.clear_warm;
  const preset = S.cfg.anim_presets.find(p => p.key === (c.preset || S.clip));
  $('#clipTip').textContent = preset ? preset.tip : '';
  syncModelUI();
  $('#clipDuration').value = String(c.duration || (c.model === 'kling25' ? '5' : '4'));
  renderPoseThumb();
  renderNorm(c);
  const t = curTake();
  const ko = (t && t.key_opts) || S.settings.extract;
  $('#keyHueLo').value = ko.hue_lo ?? 35; $('#keyHueHi').value = ko.hue_hi ?? 90;
  $('#keyDom').value = ko.dom ?? 25; $('#keySat').value = ko.sat ?? 60; $('#keyVal').value = ko.val ?? 40;
}

/* 影片模型的參數格：有 schema 就照 schema（新模型自動支援），沒有才用內建的假設 */
function syncModelUI() {
  const key = $('#clipModel').value;
  const m = modelInfo('video', key);
  const local = !!m.local;
  const c = curClip() || {};
  $('#clipDuration').innerHTML = (m.durations || ['5']).map(d => `<option value="${d}">${d}</option>`).join('');
  $('#negWrap').hidden = local ? false : !(m.negative ?? true);
  $('#clipRes').closest('.f').hidden = local || key.startsWith('kling');
  $('#clipDuration').closest('.f').hidden = local;
  $('#clipAspect').closest('.f').hidden = local || key.startsWith('kling');
  $('#clipCfg').closest('.f').hidden = local || !key.startsWith('kling');
  $('#clipCamFixed').closest('.f').hidden = local || key.startsWith('kling');
  $('#clipLocalRow').hidden = !local;
  if (!local) applyVideoSchema(key, c);
  if (local) {
    const lv = S.settings.local.video;
    const c = curClip() || {};
    $('#clipLocalWf').value = c.local_workflow || lv.workflow || '';
    $('#clipFrames').value = c.local_frames || lv.frames;
    $('#clipGenFps').value = c.local_fps || lv.fps;
    $('#clipSteps').value = c.local_steps || lv.steps;
    $('#clipW').value = c.local_w || lv.width;
    $('#clipH').value = c.local_h || lv.height;
  }
  $('#clipCost').textContent = local ? '本機生成 · 不計費'
    : (m.price == null ? '價格未知（fal 沒公布單次價）' : `每支約 ${fmt$(m.price)}`);
}

/* 拿到 schema 之後再修正欄位：選單值換成模型真正接受的 enum，不支援的整格藏起來 */
async function applyVideoSchema(key, c) {
  const sch = await modelSchema('video', key);
  if (!sch || $('#clipModel').value !== key) return;   // 期間換過模型就別覆蓋
  const f = sch.fields || {};
  const setEnum = (sel, name, cur) => {
    const fd = f[name];
    const box = $(sel).closest('.f');
    if (!fd) { box.hidden = true; return; }
    box.hidden = false;
    if (!fd.enum) return;
    $(sel).innerHTML = fd.enum.map(v => `<option value="${v}">${v}</option>`).join('');
    const want = [cur, fd.default].map(x => x == null ? '' : String(x))
      .find(x => fd.enum.some(e => String(e) === x));
    $(sel).value = want != null ? want : String(fd.enum[0]);
  };
  setEnum('#clipDuration', 'duration', c.duration);
  setEnum('#clipRes', 'resolution', c.resolution);
  setEnum('#clipAspect', 'aspect_ratio', c.aspect_ratio);
  $('#clipCfg').closest('.f').hidden = !('cfg_scale' in f);
  $('#clipCamFixed').closest('.f').hidden = !('camera_fixed' in f);
  $('#clipSeed').closest('.f').hidden = !('seed' in f);
  $('#negWrap').hidden = !('negative_prompt' in f);
}

function renderNorm(c) {
  const n = (S.spec && S.spec.norms || {})[S.clip];
  const box = $('#clipNorm');
  if (!n) { box.hidden = true; return; }
  box.hidden = false;
  const nSel = (c.frames || []).length;
  const bad = nSel && !(n.count[0] - 2 <= nSel && nSel <= n.count[1] + 4);
  box.innerHTML = `<span>既有素材慣例：<b>${n.count[0]}–${n.count[1]}</b> 幀 @ <b>${n.fps_common}</b>fps
    ・${n.loop ? '循環' : '單次'}${n.align ? '・逐幀對齊' : ''}
    ${nSel ? `（目前 <b style="color:${bad ? 'var(--warn)' : 'var(--ok)'}">${nSel}</b>）` : ''}</span>
    <span class="grow"></span><button class="btn xs" id="btnApplyNorm">${ic('check')}套用</button>`;
  $('#btnApplyNorm').onclick = async () => {
    $('#fpsInput').value = n.fps_common;
    $('#loopInput').checked = !!n.loop;
    $('#alignInput').checked = !!n.align;
    await saveClip(); renderAnim();
  };
}

function renderPoseThumb() {
  const pose = (S.proj.poses || {})[$('#clipPose').value];
  const el = $('#clipPoseThumb');
  el.classList.toggle('empty-thumb', !pose);
  el.innerHTML = pose ? `<img src="${fileUrl(pose.file)}?v=${pose.created | 0}">`
    : '先到「姿勢」做一張，或按上面「上傳外部圖片」';
}

/* 動作頁直接塞一張外部圖當姿勢源圖——不必先在這個專案生成過任何東西。
   存成專案的姿勢（自動轉綠幕），順手把目前這個動作指過去。 */
async function uploadClipPose(file) {
  if (!file || !S.pid || !S.clip) return;
  const suggest = ((S.proj.clips || {})[S.clip] || {}).pose || S.clip;
  const key = prompt('存成哪個姿勢名？（同名會覆蓋）', suggest);
  if (!key) return;
  const fd = new FormData();
  fd.append('file', file);
  fd.append('key', key);
  toast('上傳並轉綠幕…');
  try {
    const pose = await api(`/api/projects/${S.pid}/pose_upload`, { method: 'POST', body: fd });
    await loadProject(S.pid);
    const real = Object.keys(S.proj.poses || {}).find(k => S.proj.poses[k].file === pose.file);
    if (real) {
      await api(`/api/projects/${S.pid}/clips/${S.clip}`, { method: 'PUT', body: { pose: real } });
      await loadProject(S.pid);
    }
    renderAnim();
    toast(`已設為「${S.clip}」的姿勢源圖`, 'ok');
  } catch (e) { toast(e.message, 'err'); }
}

async function saveClip() {
  const c = curClip(); if (!c) return;
  const body = {
    pose: $('#clipPose').value, model: $('#clipModel').value,
    prompt: $('#clipPrompt').value, neg: $('#clipNeg').value,
    duration: $('#clipDuration').value, resolution: $('#clipRes').value,
    aspect_ratio: $('#clipAspect').value,
    seed: $('#clipSeed').value ? +$('#clipSeed').value : null,
    cfg_scale: numOr('#clipCfg', 0.5), camera_fixed: $('#clipCamFixed').checked,
    fps: +$('#fpsInput').value || 12, loop: $('#loopInput').checked,
    align: $('#alignInput').checked, scale: numOr('#scaleInput', 1),
    xalign: $('#clipXalign').value, despeckle: $('#clipDespeckle').checked,
    clear_warm: $('#clipClearWarm').checked,
    local_workflow: $('#clipLocalWf').value, local_frames: +$('#clipFrames').value,
    local_fps: +$('#clipGenFps').value, local_steps: +$('#clipSteps').value,
    local_w: +$('#clipW').value, local_h: +$('#clipH').value,
  };
  Object.assign(c, body);
  Player.fps = body.fps;
  await api(`/api/projects/${S.pid}/clips/${S.clip}`, { method: 'PUT', body });
}

async function genVideo() {
  const c = curClip(); if (!c) { toast('先選一個動作', 'err'); return; }
  if (!$('#clipPose').value) { toast('這個動作還沒有姿勢源圖', 'err'); return; }
  await saveClip();
  const m = modelInfo('video', c.model);
  const body = {};
  if (m.local) {
    body.local = {
      workflow: $('#clipLocalWf').value, frames: +$('#clipFrames').value,
      fps: +$('#clipGenFps').value, steps: +$('#clipSteps').value,
      width: +$('#clipW').value, height: +$('#clipH').value,
      seed: $('#clipSeed').value ? +$('#clipSeed').value : null,
    };
  } else {
    body.cfg_scale = numOr('#clipCfg', 0.5);
    body.aspect_ratio = $('#clipAspect').value;
    body.camera_fixed = $('#clipCamFixed').checked;
    if ($('#clipSeed').value) body.seed = +$('#clipSeed').value;
  }
  $('#btnGenVideo').disabled = true;
  try {
    const { job } = await api(`/api/projects/${S.pid}/clips/${S.clip}/generate`, { method: 'POST', body });
    toast(m.local ? '已送進本機佇列'
      : (m.price == null ? '已送出（價格未知）' : `已送出（約 ${fmt$(m.price)}）`));
    await trackJob(job);
    await loadProject(S.pid);
    renderAnim();
    toast('影片完成，已抽幀', 'ok');
  } catch (e) { toast(e.message, 'err'); }
  finally { $('#btnGenVideo').disabled = false; }
}

function renderTakes() {
  const c = curClip();
  const takes = (c && c.takes) || [];
  $('#takeCount').textContent = takes.length ? takes.length + ' 支' : '';
  $('#takeList').innerHTML = takes.length ? takes.map(t => `
    <div class="take ${t.id === c.selected_take ? 'on' : ''}" data-id="${t.id}">
      <div class="meta"><b>${esc(t.model || '?')} · ${t.count} 幀</b>
        <small>${ago(t.created)} · ${t.src_w}×${t.src_h}${t.cost ? ' · ' + fmt$(t.cost) : ''}</small></div>
      <button class="rv" data-v="${esc(t.video)}" title="開啟影片位置">位置</button>
      <button class="del" data-id="${t.id}" title="刪除">✕</button>
    </div>`).join('') : '<div class="empty">還沒有影片</div>';
  $$('#takeList .take').forEach(el => el.onclick = async () => {
    curClip().selected_take = el.dataset.id;
    await api(`/api/projects/${S.pid}/clips/${S.clip}`, { method: 'PUT', body: { selected_take: el.dataset.id } });
    renderAnim();
  });
  $$('#takeList .rv').forEach(b => b.onclick = e => { e.stopPropagation(); reveal(b.dataset.v); });
  $$('#takeList .del').forEach(b => b.onclick = async e => {
    e.stopPropagation();
    if (!confirm('刪除這支影片與它的幀？')) return;
    await api(`/api/projects/${S.pid}/clips/${S.clip}/takes/${b.dataset.id}`, { method: 'DELETE' });
    await loadProject(S.pid); renderAnim();
  });
}

/* ---------------- 幀選擇 ---------------- */

const frameUrl = (take, i) => `/files/${S.pid}/${take.frames_dir}/f${String(i).padStart(3, '0')}.png?v=${take.count}`;

function renderStrip() {
  const c = curClip(), t = curTake();
  const strip = $('#strip');
  if (!t) {
    strip.innerHTML = '<div class="empty">這個動作還沒有影片</div>';
    $('#stripHint').textContent = '';
    $('#stageEmpty').hidden = false;
    $('#takeInfo').textContent = '';
    Player.load(null);
    return;
  }
  const sel = new Set(c.frames || []);
  const feet = t.stats.map(s => s.feet);
  const fmin = Math.min(...feet), fmax = Math.max(...feet);
  const w = $('#thumbSize').value;
  strip.innerHTML = t.stats.map((s, i) => {
    const h = fmax > fmin ? (1 - (s.feet - fmin) / (fmax - fmin)) : 0;
    return `<div class="fr ${sel.has(i) ? 'on' : ''}" data-i="${i}" style="width:${w}px">
      <span class="n">${i}</span><img loading="lazy" src="${frameUrl(t, i)}">
      ${s.warm > 40 ? '<span class="flag" title="偵測到亮暖色，可能是模型多加的火光特效"></span>' : ''}
      <i class="bar" style="width:${(h * 100).toFixed(0)}%"></i></div>`;
  }).join('');
  bindStripSelection();
  $('#strip').oncontextmenu = e => {
    const fr = e.target.closest && e.target.closest('.fr');
    if (!fr) return;
    e.preventDefault();
    reveal(`${t.frames_dir}/f${String(fr.dataset.i).padStart(3, '0')}.png`);
  };
  $('#rngEnd').value = t.count - 1;
  $('#rngStart').max = $('#rngEnd').max = t.count - 1;
  $('#stripHint').textContent =
    `共 ${t.count} 幀（原始 ${t.src_fps}fps）· 已選 ${sel.size} 幀 · 藍條＝腳底高度 · 點擊切換、Shift 連選、拖曳刷選`;
  $('#takeInfo').textContent = `${t.id} · ${t.model} · ${t.src_w}×${t.src_h}`;
  $('#stageEmpty').hidden = true;
  Player.load(t);
  markCursor();
}

function bindStripSelection() {
  let drag = null;
  const strip = $('#strip');
  const idxOf = el => { const fr = el && el.closest ? el.closest('.fr') : null; return fr ? +fr.dataset.i : null; };
  const apply = to => {
    if (!drag) return;
    const [a, b] = [drag.from, to].sort((x, y) => x - y);
    const cur = new Set(drag.base);
    for (let k = a; k <= b; k++) drag.mode === 'add' ? cur.add(k) : cur.delete(k);
    $$('#strip .fr').forEach(el => el.classList.toggle('on', cur.has(+el.dataset.i)));
    drag.result = cur;
  };
  // 不用 setPointerCapture：在可橫向捲動的容器上拖曳會被判成 pan 手勢而發出 pointercancel
  const onMove = e => {
    if (!drag) return;
    e.preventDefault();
    const i = idxOf(document.elementFromPoint(e.clientX, e.clientY));
    if (i != null) apply(i);
    const r = strip.getBoundingClientRect();
    if (e.clientX > r.right - 60) strip.scrollLeft += 18;
    else if (e.clientX < r.left + 60) strip.scrollLeft -= 18;
  };
  const onUp = e => {
    document.removeEventListener('pointermove', onMove, true);
    document.removeEventListener('pointerup', onUp, true);
    document.removeEventListener('pointercancel', onUp, true);
    if (!drag) return;
    const i = idxOf(document.elementFromPoint(e.clientX, e.clientY));
    if (i != null) apply(i);
    if (drag.result) setFrames([...drag.result]);
    drag = null;
  };
  strip.onpointerdown = e => {
    const i = idxOf(e.target);
    if (i == null) return;
    e.preventDefault();
    const cur = new Set(curClip().frames || []);
    S.cursor = i; markCursor();
    Player.stop(); Player.showFrame(i);
    if (e.shiftKey && S.lastFrameClick != null) {
      const [a, b] = [S.lastFrameClick, i].sort((x, y) => x - y);
      for (let k = a; k <= b; k++) cur.add(k);
      return setFrames([...cur]);
    }
    drag = { mode: cur.has(i) ? 'del' : 'add', from: i, base: cur, result: null };
    S.lastFrameClick = i;
    apply(i);
    document.addEventListener('pointermove', onMove, true);
    document.addEventListener('pointerup', onUp, true);
    document.addEventListener('pointercancel', onUp, true);
  };
}

function markCursor() {
  $$('#strip .fr.marked').forEach(e => e.classList.remove('marked'));
  const el = $(`#strip .fr[data-i="${S.cursor}"]`);
  if (el) el.classList.add('marked');
}

function moveCursor(d) {
  const t = curTake(); if (!t) return;
  S.cursor = Math.max(0, Math.min(t.count - 1, (S.cursor || 0) + d));
  Player.stop(); Player.showFrame(S.cursor);
  const el = $(`#strip .fr[data-i="${S.cursor}"]`);
  if (el) el.scrollIntoView({ block: 'nearest', inline: 'center' });
  markCursor();
}

async function setFrames(list) {
  const c = curClip(); if (!c) return;
  c.frames = [...new Set(list)].sort((a, b) => a - b);
  renderStrip();
  renderNorm(c);
  await api(`/api/projects/${S.pid}/clips/${S.clip}`, { method: 'PUT', body: { frames: c.frames } });
}

function applyRange() {
  const t = curTake(); if (!t) return;
  const a = Math.max(0, +$('#rngStart').value | 0);
  const b = Math.min(t.count - 1, +$('#rngEnd').value | 0);
  const st = Math.max(1, +$('#rngStep').value | 0);
  const out = [];
  for (let i = a; i <= b; i += st) out.push(i);
  setFrames(out);
}

async function loopDetect() {
  const t = curTake(); if (!t) return;
  const start = (curClip().frames || [])[0] ?? (+$('#rngStart').value | 0);
  const res = await api(`/api/projects/${S.pid}/clips/${S.clip}/loop?start=${start}&take=${t.id}`);
  if (!res.length) { toast('分析不出來', 'err'); return; }
  showModal('循環偵測', `<p class="note">從第 <b>${start}</b> 幀起算，差異值越小越接近無縫循環：</p>
    <div class="row">${res.map(r => `<button class="btn xs loopbtn" data-p="${r.period}">
      ${r.period} 幀 <span class="dim">Δ${r.diff}</span></button>`).join('')}</div>`, null, '關閉', true);
  $$('#modalBody .loopbtn').forEach(b => b.onclick = () => {
    const p = +b.dataset.p;
    $('#rngStart').value = start; $('#rngEnd').value = start + p - 1;
    applyRange();
    $('#modal').hidden = true;
    toast(`套用週期 ${p} 幀`, 'ok');
  });
}

/* 把「現在膠片上框好的那段」另存成另一個動作：共用同一支影片，不重生成也不花錢。
   一次生完整段跳躍，再切成 rise / fall / land 就是走這裡。 */
async function saveSelectionAsClip() {
  const t = curTake(), c = curClip();
  if (!t || !c) { toast('先選一個動作', 'err'); return; }
  const frames = c.frames || [];
  if (!frames.length) { toast('先在膠片上框選要的幀', 'err'); return; }
  const span = `${frames[0]}–${frames[frames.length - 1]}，共 ${frames.length} 幀`;
  const used = Object.keys(S.proj.clips || {});
  showModal('選取的幀另存為動作', `
    <p class="note">來源動作 <b>${esc(S.clip)}</b>，目前選取 <b>${span}</b>。
      新動作共用同一支影片與同一批已抽好的幀——<b>不會重新生成，不花錢</b>。</p>
    <label class="f block">新動作名稱
      <input id="segName" placeholder="rise / fall / land / attack…" list="segNameList">
      <datalist id="segNameList">${['rise', 'fall', 'land', 'attack', 'crouch_attack', 'hurt']
      .filter(n => !used.includes(n)).map(n => `<option value="${n}">`).join('')}</datalist></label>
    <div class="row">
      <label class="f">FPS <input type="number" id="segFps" class="w64" value="${c.fps || 12}" min="1" max="60"></label>
      <label class="f"><input type="checkbox" id="segLoop" ${c.loop ? 'checked' : ''}>循環</label>
      <label class="f"><input type="checkbox" id="segAlign" checked>逐幀腳底對齊</label>
      <label class="f"><input type="checkbox" id="segGo" checked>建立後切過去</label>
    </div>
    <p class="note">單次動作（rise／fall／land／attack）建議關循環、開腳底對齊；
      循環動作（idle／run）反過來。已存在的名稱會被覆蓋，會先問你。</p>`,
    async () => {
      const name = ($('#segName').value || '').trim().replace(/[^\w-]/g, '_');
      if (!name) { toast('要給新動作一個名字', 'err'); return; }
      if (used.includes(name) &&
        !confirm(`「${name}」已經存在，要用這段覆蓋它嗎？（原本的幀選擇會沒了）`)) return;
      const align = $('#segAlign').checked;
      try {
        await api(`/api/projects/${S.pid}/clips/${S.clip}/split`, {
          method: 'POST',
          body: {
            take: t.id,
            segments: [{
              name, frames, label: `${S.clip} 第 ${frames[0]}–${frames[frames.length - 1]} 幀`,
              fps: +$('#segFps').value || c.fps || 12,
              loop: $('#segLoop').checked, align,
              xalign: align ? 'first' : (c.xalign || 'median'),
            }],
          },
        });
        const go = $('#segGo').checked;
        await loadProject(S.pid);
        if (go) S.clip = name;
        renderAnim();
        toast(`已另存為「${name}」（${frames.length} 幀）`, 'ok');
      } catch (e) { toast(e.message, 'err'); }
    }, '另存');
}

async function jumpSegments() {
  const t = curTake(); if (!t) return;
  const seg = t.segments || {};
  if (!seg.rise) { toast('偵測不到滯空段', 'err'); return; }
  showModal('跳躍分段', `<p class="note">起跳 <b>${seg.rise[0]}</b> → 頂點 <b>${seg.apex}</b> → 落地 <b>${seg.fall[1]}</b>。
      建立 rise / fall / land 三個動作（共用這支影片，逐幀腳底對齊）。</p>
    <label class="f block">命名前綴<input id="segPrefix" placeholder="留空＝直接叫 rise/fall/land"></label>`,
    async () => {
      const pre = ($('#segPrefix') && $('#segPrefix').value || '').trim();
      const mk = (n, a, b, fps) => ({ name: (pre ? pre + '_' : '') + n, frames: rangeOf(a, b), fps });
      const segs = [mk('rise', seg.rise[0], seg.rise[1], 20), mk('fall', seg.fall[0], seg.fall[1], 12),
      mk('land', seg.land[0], seg.land[1], 20)];
      await api(`/api/projects/${S.pid}/clips/${S.clip}/split`, { method: 'POST', body: { take: t.id, segments: segs } });
      await loadProject(S.pid); renderAnim();
      toast('已建立三段', 'ok');
    }, '建立');
}

const rangeOf = (a, b, step = 1) => { const o = []; for (let i = a; i <= b; i += step) o.push(i); return o; };

async function reextract() {
  const t = curTake(); if (!t) { toast('沒有 take', 'err'); return; }
  const body = {
    hue_lo: +$('#keyHueLo').value, hue_hi: +$('#keyHueHi').value,
    dom: +$('#keyDom').value, sat: +$('#keySat').value, val: +$('#keyVal').value,
  };
  try {
    const { job } = await api(`/api/projects/${S.pid}/clips/${S.clip}/takes/${t.id}/reextract`,
      { method: 'POST', body });
    toast('重新抽幀中…');
    await trackJob(job);
    await loadProject(S.pid); renderAnim();
    toast('已用新參數重抽', 'ok');
  } catch (e) { toast(e.message, 'err'); }
}

/* ---------------- 播放器 ---------------- */

const Player = {
  take: null, imgs: [], playing: true, fps: 12, idx: 0, acc: 0, last: 0, raf: null,
  ref: null, hold: null,
  load(take) {
    this.take = take; this.imgs = []; this.idx = 0; this.hold = null;
    if (!take) { this.stop(); this.clear(); return; }
    this.fps = (curClip() || {}).fps || 12;
    for (let i = 0; i < take.count; i++) {
      const im = new Image(); im.src = frameUrl(take, i); this.imgs.push(im);
    }
    this.loadRef();
    this.resize();
    if (!this.raf) this.tick();
  },
  loadRef() {
    this.ref = null;
    if (!$('#refInput').checked || !S.spec) return;
    const pool = (S.spec.skins || []).filter(s => s.anims && s.anims[S.clip]);
    const skin = pool.find(s => s.id === 'default') || pool[0];
    if (!skin) return;
    const a = skin.anims[S.clip];
    const img = new Image();
    img.src = `/game/${skin.dir}/${S.clip}.png`;
    this.ref = {
      img, fw: skin.fw, fh: a.frameH || skin.fh, count: a.count, fps: a.fps,
      feet: skin.feetRatio, anchor: skin.anchorX, label: skin.label,
      bodyRatio: (skin.bodyPx && skin.fh) ? skin.bodyPx / skin.fh : 0.9,
    };
  },
  clear() { const cv = $('#playCanvas'); cv.getContext('2d').clearRect(0, 0, cv.width, cv.height); },
  frames() {
    const c = curClip();
    const f = (c && c.frames && c.frames.length) ? c.frames : null;
    return f || (this.take ? [...Array(this.take.count).keys()] : []);
  },
  showFrame(i) { this.hold = i; this.draw(true); },
  toggle() {
    this.playing = !this.playing;
    if (this.playing) this.hold = null;
    $('#btnPlay').innerHTML = this.playing ? ic('pause') + '暫停' : ic('play') + '播放';
  },
  stop() { this.playing = false; $('#btnPlay').innerHTML = ic('play') + '播放'; },
  resize() {
    if (!this.take) return;
    const cv = $('#playCanvas'), wrap = $('#canvasWrap');
    const z = +$('#zoomSelect').value;
    const aspect = this.take.preview_h / this.take.preview_w;
    let w;
    if (z > 0) w = this.take.preview_w * z;
    else w = Math.min(wrap.clientWidth - 12, (wrap.clientHeight - 12) / aspect);
    cv.width = this.take.preview_w; cv.height = this.take.preview_h;
    cv.style.width = Math.max(80, Math.round(w)) + 'px';
    cv.style.height = Math.max(80, Math.round(w * aspect)) + 'px';
    this.draw(true);
  },
  tick() {
    const now = performance.now();
    const dt = this.last ? (now - this.last) : 0;
    this.last = now;
    if (this.playing && this.take) {
      this.acc += dt;
      const spf = 1000 / Math.max(1, this.fps);
      while (this.acc >= spf) { this.acc -= spf; this.idx++; }
      const n = this.frames().length;
      if (n) {
        const c = curClip();
        if (c && c.loop === false && this.idx >= n) this.idx = n - 1;
        else this.idx %= n;
      }
      this.draw();
    }
    this.raf = requestAnimationFrame(() => this.tick());
  },
  draw() {
    const cv = $('#playCanvas'); if (!cv || !this.take) return;
    const g = cv.getContext('2d');
    const W = cv.width, H = cv.height;
    const bg = $('#bgSelect').value;
    g.setTransform(1, 0, 0, 1, 0, 0);
    g.clearRect(0, 0, W, H);
    if (bg === 'checker') {
      const s = 12;
      for (let y = 0; y < H; y += s) for (let x = 0; x < W; x += s) {
        g.fillStyle = ((x / s + y / s) % 2) ? '#262626' : '#1d1d1d';
        g.fillRect(x, y, s, s);
      }
    } else {
      g.fillStyle = { dark: '#131313', light: '#c8c8c8', magenta: '#ff00ff' }[bg] || '#131313';
      g.fillRect(0, 0, W, H);
    }
    const fr = this.frames();
    if (!fr.length) return;
    const i = (this.hold != null && !this.playing) ? this.hold : fr[Math.min(this.idx, fr.length - 1)];
    const st = this.take.stats[i];
    g.imageSmoothingEnabled = false;
    if ($('#flipInput').checked) { g.translate(W, 0); g.scale(-1, 1); }

    if (this.ref && this.ref.img.complete && this.ref.img.naturalWidth && st) {
      const r = this.ref;
      const bodyH = Math.max(4, (st.feet - st.top) * H);
      const dh = bodyH / r.bodyRatio, dw = r.fw * (dh / r.fh);
      const fi = Math.floor(performance.now() / (1000 / (r.fps || 12))) % r.count;
      g.globalAlpha = 0.32;
      g.drawImage(r.img, fi * r.fw, 0, r.fw, r.fh,
        st.cx * W - r.anchor * dw, st.feet * H - r.feet * dh, dw, dh);
      g.globalAlpha = 1;
    }
    if ($('#onionInput').checked) {
      const pos = fr.indexOf(i);
      const prev = fr[(pos - 1 + fr.length) % fr.length], next = fr[(pos + 1) % fr.length];
      g.globalAlpha = 0.26;
      [[prev, 'hue-rotate(150deg)'], [next, 'hue-rotate(-60deg)']].forEach(([k, f]) => {
        const im2 = this.imgs[k];
        if (im2 && im2.complete && im2.naturalWidth) { g.filter = f; g.drawImage(im2, 0, 0, W, H); }
      });
      g.filter = 'none'; g.globalAlpha = 1;
    }
    const im = this.imgs[i];
    if (im && im.complete && im.naturalWidth) g.drawImage(im, 0, 0, W, H);

    if ($('#guideInput').checked && st) {
      g.setTransform(1, 0, 0, 1, 0, 0);
      g.strokeStyle = '#3f6ea8'; g.lineWidth = 1;
      const y = Math.round(st.feet * H) + .5;
      g.beginPath(); g.moveTo(0, y); g.lineTo(W, y); g.stroke();
      g.strokeStyle = '#c8863c88';
      const x = Math.round(st.cx * W) + .5;
      g.beginPath(); g.moveTo(x, 0); g.lineTo(x, H); g.stroke();
    }
    $('#playInfo').textContent = (this.playing ? '' : '暫停 ') +
      `幀 ${i} · 選 ${fr.length} 幀 @ ${this.fps}fps · ${(fr.length / this.fps).toFixed(2)}s` +
      (this.ref ? ` · 參考 ${this.ref.label}` : '');
  },
};
window.addEventListener('resize', () => Player.resize());

/* ---------------------------------------------------------------- 4 輸出 */

function renderExport() {
  if (!S.proj) return;
  const rows = Object.entries(S.proj.clips || {});
  $('#packList').innerHTML = rows.length ? rows.map(([n, c]) => `
    <label class="pack-row">
      <input type="checkbox" ${(c.frames || []).length ? 'checked' : 'disabled'} value="${esc(n)}">
      <b>${esc(n)}</b><span class="grow"></span>
      <span class="n">${(c.frames || []).length} 幀 · ${c.fps || 12}fps · ${c.loop ? 'loop' : 'once'}${c.align ? ' · 逐幀對齊' : ''}</span>
    </label>`).join('') : '<div class="empty">還沒有動作</div>';
  const pk = S.proj.pack || {};
  $('#packW').value = pk.frame_w || (S.settings.defaults || {}).frame_w || 240;
  $('#packDark').value = pk.outline_dark ?? 0.32;
  $('#packOutline').value = pk.outline_px || 0;
  $('#packPad').value = pk.pad ?? 6;
  $('#exportDir').value = S.proj.export_dir || '';
  $('#btnDownload').href = `/api/projects/${S.pid}/pack/download`;
  if (S.proj.last_pack) renderPackResult(S.proj.last_pack.meta);
  else $('#packResult').innerHTML = '<div class="empty">還沒有輸出</div>';
  Preview.load();
}

async function doPack() {
  const names = $$('#packList input:checked').map(i => i.value);
  if (!names.length) { toast('至少勾一個動作', 'err'); return; }
  $('#btnPack').disabled = true;
  try {
    const { job } = await api(`/api/projects/${S.pid}/pack`, {
      method: 'POST',
      body: {
        clips: names, frame_w: +$('#packW').value, outline_dark: numOr('#packDark', .32),
        outline_px: +$('#packOutline').value, pad: +$('#packPad').value,
      }
    });
    const j = await trackJob(job);
    await loadProject(S.pid);
    renderPackResult(j.result.meta);
    Preview.load();
    toast('打包完成', 'ok');
  } catch (e) { toast(e.message, 'err'); }
  finally { $('#btnPack').disabled = false; }
}

function renderPackResult(meta) {
  if (!meta) return;
  const v = Date.now();
  const strips = Object.entries(meta).filter(([k]) => !k.startsWith('_')).map(([k, m]) => `
    <div class="pack-strip"><div class="t">${esc(k)} — ${m.count} 幀 · ${m.frameW}×${m.frameH} · ${m.fps}fps${m.loop ? ' · loop' : ''}</div>
      <img src="/files/${S.pid}/out/${m.file}?v=${v}"></div>`).join('');
  $('#packResult').innerHTML = strips + `<pre>${esc(JSON.stringify(meta, null, 1))}</pre>`;
}

/* ---------------- 打包結果播放器：直接播 strip，就是遊戲會看到的樣子 ---------------- */

const Preview = {
  img: null, meta: null, idx: 0, acc: 0, last: 0, raf: null, playing: true,
  load() {
    const meta = S.proj && S.proj.last_pack && S.proj.last_pack.meta;
    const sel = $('#pvClip');
    if (!meta) {
      sel.innerHTML = '<option value="">（還沒打包）</option>';
      this.meta = null; this.draw(); return;
    }
    const names = Object.keys(meta).filter(k => !k.startsWith('_'));
    const keep = sel.value;
    sel.innerHTML = names.map(n => `<option value="${n}">${esc(n)}</option>`).join('');
    sel.value = names.includes(keep) ? keep : (names.includes('idle') ? 'idle' : names[0]);
    this.select();
    if (!this.raf) this.tick();
  },
  select() {
    const meta = S.proj.last_pack.meta;
    const name = $('#pvClip').value;
    const m = meta[name];
    if (!m) { this.meta = null; return; }
    this.meta = { ...m, name };
    $('#pvFps').value = m.fps;
    $('#pvLoop').checked = !!m.loop;
    this.idx = 0;
    const im = new Image();
    im.onload = () => { this.img = im; this.resize(); };
    im.src = `/files/${S.pid}/out/${m.file}?v=${(S.proj.last_pack.at | 0)}`;
  },
  resize() {
    const m = this.meta; if (!m) return;
    const z = +$('#pvZoom').value || 2;
    const cv = $('#pvCanvas');
    cv.width = m.frameW; cv.height = m.frameH;
    cv.style.width = m.frameW * z + 'px';
    cv.style.height = m.frameH * z + 'px';
    this.draw();
  },
  toggle() {
    this.playing = !this.playing;
    $('#pvPlay').innerHTML = this.playing ? ic('pause') + '暫停' : ic('play') + '播放';
  },
  tick() {
    const now = performance.now();
    const dt = this.last ? now - this.last : 0;
    this.last = now;
    if (this.playing && this.meta) {
      this.acc += dt;
      const spf = 1000 / Math.max(1, +$('#pvFps').value || 12);
      while (this.acc >= spf) {
        this.acc -= spf; this.idx++;
        if (this.idx >= this.meta.count) this.idx = $('#pvLoop').checked ? 0 : this.meta.count - 1;
      }
      this.draw();
    }
    this.raf = requestAnimationFrame(() => this.tick());
  },
  draw() {
    const cv = $('#pvCanvas'); if (!cv) return;
    const g = cv.getContext('2d');
    const W = cv.width, H = cv.height;
    g.setTransform(1, 0, 0, 1, 0, 0);
    g.clearRect(0, 0, W, H);
    const bg = $('#pvBg').value;
    if (bg === 'checker') {
      const s = 12;
      for (let y = 0; y < H; y += s) for (let x = 0; x < W; x += s) {
        g.fillStyle = ((x / s + y / s) % 2) ? '#262626' : '#1d1d1d';
        g.fillRect(x, y, s, s);
      }
    } else {
      g.fillStyle = { dark: '#131313', light: '#c8c8c8', magenta: '#ff00ff' }[bg] || '#131313';
      g.fillRect(0, 0, W, H);
    }
    const m = this.meta;
    if (!m || !this.img) { $('#pvInfo').textContent = '打包後這裡會播放'; return; }
    g.imageSmoothingEnabled = false;
    if ($('#pvFlip').checked) { g.translate(W, 0); g.scale(-1, 1); }
    g.drawImage(this.img, this.idx * m.frameW, 0, m.frameW, m.frameH, 0, 0, m.frameW, m.frameH);
    $('#pvInfo').textContent =
      `${m.name} · 幀 ${this.idx + 1}/${m.count} · ${m.frameW}×${m.frameH} @ ${$('#pvFps').value}fps` +
      ` · ${(m.count / (+$('#pvFps').value || 12)).toFixed(2)}s`;
  },
};

async function doExportDir() {
  const dir = $('#exportDir').value.trim();
  if (!dir) { toast('填一個資料夾路徑', 'err'); return; }
  try {
    const r = await api(`/api/projects/${S.pid}/export`, { method: 'POST', body: { dir } });
    toast(`已複製 ${r.copied.length} 個檔案`, 'ok');
  } catch (e) { toast(e.message, 'err'); }
}

/* ---------------------------------------------------------------- 5 素材庫 */

async function renderLibrary() {
  const list = await api('/api/projects');
  $('#libGrid').innerHTML = list.map(p => `
    <div class="lib-card" data-id="${p.id}">
      <button class="del" data-id="${p.id}">✕</button>
      <button class="reveal" data-id="${p.id}" title="開啟專案資料夾">${ic('folder')}</button>
      ${p.thumb ? `<img src="${p.thumb}">` : '<div class="thumbph">無圖</div>'}
      <div class="info"><b>${esc(p.name)}</b>
        <small>${p.clips} 動作 · ${p.poses} 姿勢 · ${fmt$(p.spend)} · ${ago(p.updated)}</small></div>
    </div>`).join('') || '<div class="empty">還沒有專案</div>';
  $$('#libGrid .lib-card').forEach(el => el.onclick = async () => {
    await selectProject(el.dataset.id); setView('anim');
  });
  $$('#libGrid .reveal').forEach(b => b.onclick = e => {
    e.stopPropagation(); reveal('', { pid: b.dataset.id });
  });
  $$('#libGrid .del').forEach(b => b.onclick = async e => {
    e.stopPropagation();
    if (!confirm('刪除整個專案？')) return;
    await api('/api/projects/' + b.dataset.id, { method: 'DELETE' });
    S.pid = null; await refreshProjects(); renderLibrary();
  });

  const takes = [];
  Object.entries((S.proj && S.proj.clips) || {}).forEach(([n, c]) =>
    (c.takes || []).forEach(t => takes.push({ clip: n, t })));
  takes.sort((a, b) => b.t.created - a.t.created);
  $('#libTakes').innerHTML = takes.length ? takes.map(({ clip, t }) => `
    <div class="lib-take">
      <video src="${fileUrl(t.video)}" muted loop playsinline
             onmouseover="this.play()" onmouseout="this.pause()"></video>
      <div class="info"><b>${esc(clip)}</b>
        <small>${esc(t.model || '')} · ${t.count} 幀 · ${ago(t.created)}</small></div>
    </div>`).join('') : '<div class="empty">還沒有影片</div>';
}

/* ---------------------------------------------------------------- 6 設定 */

function renderFalSettings() {
  const k = S.fal.key || {};
  const el = $('#falPing');
  const src = { settings: '設定頁', env: '環境變數 FAL_KEY', secrets: 'secrets.json' }[k.source] || k.source;
  el.className = 'ping' + (k.has_key ? (k.verified ? ' ok' : '') : ' err');
  el.textContent = k.has_key
    ? `金鑰來源：${src}${k.masked ? '（' + k.masked + '）' : ''}${k.verified ? ' · 驗證通過' : ' · 尚未驗證'}`
    : '沒有金鑰——雲端生成會失敗，貼上後按「儲存金鑰」';
  $('#falCatalog').checked = (S.settings.fal || {}).catalog !== false;
  const n = S.fal.models.image.length + S.fal.models.video.length;
  $('#falCatalogInfo').textContent = n
    ? `線上清單：圖片 ${S.fal.models.image.length}、影片 ${S.fal.models.video.length}`
    : (S.fal.error ? '抓不到：' + S.fal.error.slice(0, 80) : '尚未載入');
}

function renderSettings() {
  const s = S.settings;
  renderFalSettings();
  $('#comfyUrl').value = s.local.comfy_url;
  $('#a1111Url').value = s.local.a1111_url;
  const li = s.local.image, lv = s.local.video;
  $('#locImgKind').value = li.kind;
  $('#locImgWf').value = li.workflow || '';
  $('#locImgSteps').value = li.steps; $('#locImgCfg').value = li.cfg;
  $('#locImgDenoise').value = li.denoise; $('#locImgW').value = li.width;
  $('#locImgH').value = li.height; $('#locImgSampler').value = li.sampler || 'DPM++ 2M';
  $('#locVidWf').value = lv.workflow || '';
  $('#locVidFrames').value = lv.frames; $('#locVidFps').value = lv.fps;
  $('#locVidSteps').value = lv.steps; $('#locVidCfg').value = lv.cfg;
  $('#locVidW').value = lv.width; $('#locVidH').value = lv.height;
  const e = s.extract;
  $('#setPreviewW').value = e.preview_w; $('#setHueLo').value = e.hue_lo;
  $('#setHueHi').value = e.hue_hi; $('#setDom').value = e.dom;
  $('#setSat').value = e.sat; $('#setVal').value = e.val;
  const d = s.defaults;
  $('#setImgModel').value = d.image_model; $('#setVidModel').value = d.video_model;
  $('#setPoseN').value = d.pose_n; $('#setFrameW').value = d.frame_w;
  $('#settingsPath').textContent = '存到 sprite_studio/settings.json';
  renderWorkflows();
  loadLocalModels();
}

function renderWorkflows() {
  $('#wfList').innerHTML = S.workflows.length ? S.workflows.map(w => `
    <div class="wf">
      <div class="meta"><b>${esc(w.label)}</b>
        <small>${esc(w.file)} · ${w.nodes || 0} 節點 · ${w.kind === 'video' ? '影片' : '圖片'}</small></div>
      <span class="tk" title="${esc((w.placeholders || []).join(' '))}">${esc((w.placeholders || []).join(' ')) || '（沒有佔位符）'}</span>
      <button class="del" data-f="${esc(w.file)}">✕</button>
    </div>`).join('') : '<div class="empty">還沒有 workflow</div>';
  $$('#wfList .del').forEach(b => b.onclick = async () => {
    if (!confirm('刪除 ' + b.dataset.f + '？')) return;
    const r = await api('/api/local/workflows/' + encodeURIComponent(b.dataset.f), { method: 'DELETE' });
    S.workflows = r.workflows; fillModelSelects(); renderWorkflows();
  });
}

async function pingLocal(kind) {
  const el = $(kind === 'comfyui' ? '#comfyPing' : '#a1111Ping');
  const url = (kind === 'comfyui' ? $('#comfyUrl') : $('#a1111Url')).value.trim();
  el.className = 'ping'; el.textContent = '連線中…';
  const r = await api(`/api/local/ping?kind=${kind}&url=${encodeURIComponent(url)}`);
  if (r.ok) {
    el.className = 'ping ok';
    el.textContent = kind === 'comfyui'
      ? `連上了 · ComfyUI ${r.version} · ${r.device} · ${r.vram_gb}GB`
      : `連上了 · 目前模型 ${r.current} · 共 ${(r.models || []).length} 個`;
    loadLocalModels();
  } else {
    el.className = 'ping err';
    el.textContent = '連不上：' + (r.error || '').slice(0, 120);
  }
}

async function loadLocalModels(target) {
  const kind = $('#locImgKind').value;
  if (kind === 'comfyui') {
    try {
      const vaes = await api('/api/local/models?node=VAELoader');
      if (Array.isArray(vaes)) {
        const o = '<option value="">（用 checkpoint 內建）</option>' +
          vaes.map(v => `<option value="${esc(v)}">${esc(v)}</option>`).join('');
        ['#locImgVae', '#poseLocalVae'].forEach(sel => {
          const el = $(sel); if (!el) return;
          const keep = el.value; el.innerHTML = o; if (keep) el.value = keep;
        });
        if (S.settings.local.image.vae) {
          $('#locImgVae').value = S.settings.local.image.vae;
          $('#poseLocalVae').value = S.settings.local.image.vae;
        }
      }
    } catch (e) { /* 沒開就算了 */ }
  }
  try {
    const list = await api('/api/local/models?kind=' + kind);
    if (!Array.isArray(list)) return;
    const opts = '<option value="">（workflow 內建）</option>' +
      list.map(m => `<option value="${esc(m)}">${esc(m)}</option>`).join('');
    ['#locImgModel', '#locVidModel', '#poseLocalModel'].forEach(sel => {
      const el = $(sel); if (!el) return;
      const keep = el.value; el.innerHTML = opts;
      if (keep) el.value = keep;
    });
    if (S.settings.local.image.model) $('#locImgModel').value = S.settings.local.image.model;
    if (S.settings.local.video.model) $('#locVidModel').value = S.settings.local.video.model;
  } catch (e) { /* 服務沒開就算了 */ }
}

async function saveSettings() {
  const body = {
    local: {
      comfy_url: $('#comfyUrl').value.trim(),
      a1111_url: $('#a1111Url').value.trim(),
      image: {
        kind: $('#locImgKind').value, workflow: $('#locImgWf').value,
        model: $('#locImgModel').value, vae: $('#locImgVae').value,
        steps: +$('#locImgSteps').value,
        cfg: numOr('#locImgCfg', 6.5), denoise: numOr('#locImgDenoise', .62),
        width: +$('#locImgW').value, height: +$('#locImgH').value,
        sampler: $('#locImgSampler').value,
      },
      video: {
        kind: 'comfyui', workflow: $('#locVidWf').value, model: $('#locVidModel').value,
        frames: +$('#locVidFrames').value, fps: +$('#locVidFps').value,
        steps: +$('#locVidSteps').value, cfg: numOr('#locVidCfg', 5),
        width: +$('#locVidW').value, height: +$('#locVidH').value,
      },
    },
    extract: {
      preview_w: +$('#setPreviewW').value, hue_lo: +$('#setHueLo').value,
      hue_hi: +$('#setHueHi').value, dom: +$('#setDom').value,
      sat: +$('#setSat').value, val: +$('#setVal').value,
    },
    defaults: {
      image_model: $('#setImgModel').value, video_model: $('#setVidModel').value,
      pose_n: +$('#setPoseN').value, frame_w: +$('#setFrameW').value,
    },
  };
  S.settings = await api('/api/settings', { method: 'PATCH', body });
  S.fal.key = { ...S.settings.fal, verified: S.fal.key.verified };
  S.cfg = await api('/api/config');
  fillModelSelects();
  renderKeyDot();
  toast('設定已儲存', 'ok');
}

boot().catch(e => { console.error(e); toast('啟動失敗：' + e.message, 'err'); });
