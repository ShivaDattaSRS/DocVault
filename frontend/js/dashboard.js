/* User dashboard: upload with progress, browse, manage and share files. */
(() => {
  if (!UI.requireAuth()) return;

  const el = (id) => document.getElementById(id);
  const state = {
    user: API.getUser(),
    folders: [],
    folderId: 'root',      // 'root' = files with no folder, '' = every file
    kind: '',
    q: '',
    sort: 'newest',
    page: 1,
    pageSize: 24,
    total: 0,
    items: [],
    pollTimer: null,
  };

  /* ------------------------------------------------------------- header */
  function paintUser() {
    const user = state.user;
    if (!user) return;
    el('user-name').textContent = user.full_name || user.email;
    el('user-email').textContent = user.email;
    el('avatar').textContent = (user.full_name || user.email).trim().charAt(0).toUpperCase();
    el('admin-link').classList.toggle('hidden', !user.is_admin);
  }

  async function loadStats() {
    const stats = await API.get('/api/files/stats');
    const pct = stats.quota_bytes ? Math.min(100, (stats.used_bytes / stats.quota_bytes) * 100) : 0;
    const meter = el('quota-meter');
    meter.querySelector('span').style.width = `${pct}%`;
    meter.className = `meter${pct >= 95 ? ' full' : pct >= 75 ? ' warn' : ''}`;
    el('quota-pct').textContent = `${pct.toFixed(0)}%`;
    el('quota-text').textContent = `${UI.bytes(stats.used_bytes)} of ${UI.bytes(stats.quota_bytes)}`;
    el('file-count').textContent = `${stats.file_count} file${stats.file_count === 1 ? '' : 's'}`;
  }

  /* ------------------------------------------------------------ folders */
  async function loadFolders() {
    state.folders = await API.get('/api/folders');
    renderFolders();
  }

  function renderFolders() {
    const list = el('folder-list');
    const byParent = new Map();
    state.folders.forEach((f) => {
      const key = f.parent_id || 'root';
      if (!byParent.has(key)) byParent.set(key, []);
      byParent.get(key).push(f);
    });

    const rows = [
      `<div class="nav-item ${state.folderId === 'root' ? 'active' : ''}" data-folder="root">
         <span>📁 My files</span></div>`,
      `<div class="nav-item ${state.folderId === '' ? 'active' : ''}" data-folder="">
         <span>🗂️ All files</span></div>`,
    ];

    const walk = (parent, depth) => {
      (byParent.get(parent) || []).forEach((folder) => {
        rows.push(`
          <div class="nav-item ${state.folderId === folder.id ? 'active' : ''}" data-folder="${folder.id}"
               style="padding-left:${11 + depth * 12}px">
            <span>📂 ${UI.escape(folder.name)}</span>
            <span class="row" style="gap:4px">
              <span class="count">${folder.file_count}</span>
              <button class="ghost small" data-folder-menu="${folder.id}" title="Folder options">⋯</button>
            </span>
          </div>`);
        walk(folder.id, depth + 1);
      });
    };
    walk('root', 0);

    list.innerHTML = rows.join('');
    list.querySelectorAll('[data-folder]').forEach((node) => {
      node.addEventListener('click', (e) => {
        if (e.target.dataset.folderMenu) return;
        state.folderId = node.dataset.folder;
        state.page = 1;
        renderFolders();
        updateTitles();
        loadFiles();
      });
    });
    list.querySelectorAll('[data-folder-menu]').forEach((btn) => {
      btn.addEventListener('click', (e) => { e.stopPropagation(); folderMenu(btn.dataset.folderMenu); });
    });
  }

  function currentFolderName() {
    if (state.folderId === 'root') return 'My files';
    if (state.folderId === '') return 'All files';
    const folder = state.folders.find((f) => f.id === state.folderId);
    return folder ? folder.name : 'Files';
  }

  function updateTitles() {
    el('view-title').textContent = currentFolderName();
    el('upload-target').textContent = `Upload to ${currentFolderName() === 'All files' ? 'My files' : currentFolderName()}`;
  }

  function folderMenu(folderId) {
    const folder = state.folders.find((f) => f.id === folderId);
    if (!folder) return;
    const modal = UI.modal(`
      <h2>Folder: ${UI.escape(folder.name)}</h2>
      <div class="field mt-16">
        <label for="fname">Rename folder</label>
        <input id="fname" value="${UI.escape(folder.name)}">
      </div>
      <div class="row spread mt-16">
        <button class="danger" data-delete>Delete folder</button>
        <div class="row">
          <button class="secondary" data-close>Cancel</button>
          <button data-save>Save</button>
        </div>
      </div>`);

    modal.querySelector('[data-save]').onclick = async () => {
      try {
        await API.patch(`/api/folders/${folderId}`, { name: modal.querySelector('#fname').value.trim() });
        modal.close();
        UI.toast('Folder renamed', 'success');
        await loadFolders();
      } catch (err) { UI.toast(err.message, 'error'); }
    };

    modal.querySelector('[data-delete]').onclick = async () => {
      modal.close();
      const cascade = await UI.confirm(
        `Delete "${folder.name}" and everything inside it? Choose Cancel to keep the files (they move to My files).`,
        { title: 'Delete folder and its files?', confirmLabel: 'Delete files too' },
      );
      try {
        await API.del(`/api/folders/${folderId}?cascade=${cascade}`);
        UI.toast('Folder deleted', 'success');
        if (state.folderId === folderId) state.folderId = 'root';
        await Promise.all([loadFolders(), loadFiles(), loadStats()]);
        updateTitles();
      } catch (err) { UI.toast(err.message, 'error'); }
    };
  }

  el('new-folder').addEventListener('click', () => {
    const parentOptions = state.folders
      .map((f) => `<option value="${f.id}">${UI.escape(f.name)}</option>`).join('');
    const modal = UI.modal(`
      <h2>New folder</h2>
      <div class="field mt-16">
        <label for="new-name">Name</label>
        <input id="new-name" placeholder="Invoices" autofocus>
      </div>
      <div class="field">
        <label for="new-parent">Inside</label>
        <select id="new-parent"><option value="">My files (top level)</option>${parentOptions}</select>
      </div>
      <div class="row" style="justify-content:flex-end">
        <button class="secondary" data-close>Cancel</button>
        <button data-create>Create</button>
      </div>`);

    if (state.folderId && state.folderId !== 'root' && state.folderId !== '') {
      modal.querySelector('#new-parent').value = state.folderId;
    }
    modal.querySelector('[data-create]').onclick = async () => {
      const name = modal.querySelector('#new-name').value.trim();
      if (!name) return UI.toast('Enter a folder name', 'error');
      try {
        await API.post('/api/folders', { name, parent_id: modal.querySelector('#new-parent').value || null });
        modal.close();
        UI.toast('Folder created', 'success');
        await loadFolders();
      } catch (err) { UI.toast(err.message, 'error'); }
    };
  });

  /* ------------------------------------------------------------- upload */
  const dropzone = el('dropzone');
  const fileInput = el('file-input');

  dropzone.addEventListener('click', () => fileInput.click());
  dropzone.addEventListener('dragover', (e) => { e.preventDefault(); dropzone.classList.add('drag'); });
  dropzone.addEventListener('dragleave', () => dropzone.classList.remove('drag'));
  dropzone.addEventListener('drop', (e) => {
    e.preventDefault();
    dropzone.classList.remove('drag');
    queueUploads([...e.dataTransfer.files]);
  });
  fileInput.addEventListener('change', () => { queueUploads([...fileInput.files]); fileInput.value = ''; });

  function queueUploads(files) {
    files.forEach((file) => startUpload(file));
  }

  function uploadRow(file) {
    const row = document.createElement('div');
    row.className = 'upload-item';
    row.innerHTML = `
      <div class="row spread">
        <span class="grow" style="min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">
          ${UI.icon('.' + (file.name.split('.').pop() || '').toLowerCase())} ${UI.escape(file.name)}
        </span>
        <span class="small muted">${UI.bytes(file.size)}</span>
        <span class="small" data-status style="min-width:82px;text-align:right">0%</span>
      </div>
      <div class="progress"><span></span></div>`;
    el('upload-queue').prepend(row);
    return row;
  }

  async function startUpload(file, allowDuplicate = false, existingRow = null) {
    const row = existingRow || uploadRow(file);
    const bar = row.querySelector('.progress');
    const fill = bar.querySelector('span');
    const status = row.querySelector('[data-status]');
    bar.className = 'progress';
    status.textContent = '0%';

    const folderId = state.folderId && state.folderId !== 'root' ? state.folderId : null;
    try {
      const res = await API.upload(file, {
        folderId,
        visibility: el('upload-public').checked ? 'public' : 'private',
        allowDuplicate,
        onProgress: (pct) => {
          fill.style.width = `${pct}%`;
          status.textContent = pct < 100 ? `${pct}%` : 'Processing…';
        },
      });
      bar.classList.add('done');
      fill.style.width = '100%';
      status.textContent = res.duplicate_of ? 'Duplicate saved' : 'Done';
      setTimeout(() => row.remove(), 2600);
      await Promise.all([loadFiles(), loadStats(), loadFolders()]);
    } catch (err) {
      if (err.status === 409 && err.detail && err.detail.duplicate_of) {
        bar.classList.add('error');
        status.textContent = 'Duplicate';
        const keep = await UI.confirm(
          `${err.detail.message} Upload it again anyway?`,
          { title: 'Duplicate file detected', danger: false, confirmLabel: 'Upload anyway' },
        );
        if (keep) return startUpload(file, true, row);
        row.remove();
        return;
      }
      bar.classList.add('error');
      fill.style.width = '100%';
      status.textContent = 'Failed';
      row.insertAdjacentHTML('beforeend', `<div class="small" style="color:var(--red)">${UI.escape(err.message)}</div>`);
      UI.toast(err.message, 'error');
    }
  }

  /* -------------------------------------------------------------- files */
  async function loadFiles() {
    const params = new URLSearchParams({
      page: state.page, page_size: state.pageSize, sort: state.sort,
    });
    if (state.q) params.set('q', state.q);
    if (state.folderId !== '') params.set('folder_id', state.folderId);
    if (state.kind) params.set('kind', state.kind);

    try {
      const res = await API.get(`/api/files?${params}`);
      state.items = res.items;
      state.total = res.total;
      renderFiles();
      schedulePoll();
    } catch (err) {
      el('file-area').innerHTML = `<div class="empty">${UI.escape(err.message)}</div>`;
    }
  }

  function statusBadge(file) {
    const label = { ready: 'Ready', processing: 'Processing', failed: 'Failed', uploading: 'Uploading' }[file.status];
    const spinner = file.status === 'processing' ? '<span class="spinner" style="width:10px;height:10px"></span>' : '';
    return `<span class="badge ${file.status}">${spinner}${label}</span>`;
  }

  function thumbHtml(file) {
    if (file.thumbnail_path) {
      const token = API.getToken();
      return `<img src="/api/files/${file.id}/thumbnail?token=${encodeURIComponent(token)}" alt="" loading="lazy">`;
    }
    return UI.icon(file.extension);
  }

  function renderFiles() {
    const area = el('file-area');
    if (!state.items.length) {
      area.innerHTML = `
        <div class="empty">
          <div class="icon">📂</div>
          <p>No files here yet.<br><span class="small">Drop files above to get started.</span></p>
        </div>`;
      el('pager').innerHTML = '';
      return;
    }

    area.innerHTML = `<div class="file-grid">${state.items.map((file) => `
      <article class="file-card" data-id="${file.id}">
        <div class="thumb">${thumbHtml(file)}</div>
        <div class="file-body">
          <div class="file-name" title="${UI.escape(file.filename)}">${UI.escape(file.filename)}</div>
          <div class="row wrap" style="gap:5px">
            ${statusBadge(file)}
            ${file.visibility === 'public' ? '<span class="badge public">Public</span>' : ''}
            ${file.is_duplicate ? '<span class="badge">Duplicate</span>' : ''}
          </div>
          <div class="small muted">${UI.bytes(file.size_bytes)} · ${UI.date(file.created_at, false)}</div>
        </div>
        <div class="file-actions">
          <button class="ghost small" data-act="details">Details</button>
          <button class="ghost small" data-act="download">Download</button>
          <button class="ghost small" data-act="menu">⋯</button>
        </div>
      </article>`).join('')}</div>`;

    area.querySelectorAll('.file-card').forEach((card) => {
      const file = state.items.find((f) => f.id === card.dataset.id);
      card.querySelectorAll('[data-act]').forEach((btn) => {
        btn.addEventListener('click', () => {
          if (btn.dataset.act === 'details') showDetails(file);
          if (btn.dataset.act === 'download') download(file);
          if (btn.dataset.act === 'menu') showMenu(file);
        });
      });
    });

    renderPager();
  }

  function renderPager() {
    const pages = Math.ceil(state.total / state.pageSize);
    if (pages <= 1) { el('pager').innerHTML = ''; return; }
    el('pager').innerHTML = `
      <button class="secondary small" ${state.page <= 1 ? 'disabled' : ''} data-prev>← Prev</button>
      <span class="small muted">Page ${state.page} of ${pages} · ${state.total} files</span>
      <button class="secondary small" ${state.page >= pages ? 'disabled' : ''} data-next>Next →</button>`;
    const prev = el('pager').querySelector('[data-prev]');
    const next = el('pager').querySelector('[data-next]');
    if (prev) prev.onclick = () => { state.page -= 1; loadFiles(); };
    if (next) next.onclick = () => { state.page += 1; loadFiles(); };
  }

  /* Re-fetch while anything is still processing in the background. */
  function schedulePoll() {
    clearTimeout(state.pollTimer);
    if (state.items.some((f) => f.status === 'processing')) {
      state.pollTimer = setTimeout(loadFiles, 2500);
    }
  }

  /* ------------------------------------------------------------ actions */
  async function download(file) {
    try {
      const link = await API.post(`/api/files/${file.id}/download-url?expires_in=300`);
      window.location.href = link.url;
      UI.toast('Download started');
    } catch (err) { UI.toast(err.message, 'error'); }
  }

  function csvPreview(extra) {
    if (!extra || extra.kind !== 'csv' || !extra.preview) return '';
    const head = (extra.headers || []).map((h) => `<th>${UI.escape(h)}</th>`).join('');
    const body = extra.preview.slice(0, 8)
      .map((row) => `<tr>${row.map((c) => `<td>${UI.escape(c)}</td>`).join('')}</tr>`).join('');
    return `
      <h3 class="mt-16">Preview — ${extra.row_count} rows × ${extra.columns} columns</h3>
      <div class="table-wrap" style="max-height:230px;overflow:auto;border:1px solid var(--border);border-radius:9px;margin-top:8px">
        <table class="preview-table"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>
      </div>
      ${(extra.warnings || []).map((w) => `<p class="small" style="color:var(--amber);margin:8px 0 0">⚠ ${UI.escape(w)}</p>`).join('')}`;
  }

  function extraRows(extra) {
    if (!extra) return '';
    const skip = new Set(['kind', 'preview', 'headers', 'warnings']);
    return Object.entries(extra)
      .filter(([k, v]) => !skip.has(k) && v !== null && v !== undefined && v !== '')
      .map(([k, v]) => `<dt>${UI.escape(k.replace(/_/g, ' '))}</dt><dd>${UI.escape(v)}</dd>`)
      .join('');
  }

  async function showDetails(file) {
    const shareUrl = file.share_token ? `${window.location.origin}/s/${file.share_token}` : '';
    const modal = UI.modal(`
      <div class="row spread">
        <h2 style="min-width:0;word-break:break-word">${UI.escape(file.filename)}</h2>
        <button class="ghost small" data-close>✕</button>
      </div>
      <div class="row wrap mt-16" style="gap:6px">
        ${statusBadge(file)}
        ${file.visibility === 'public' ? '<span class="badge public">Public</span>' : '<span class="badge">Private</span>'}
        ${file.is_duplicate ? '<span class="badge">Duplicate content</span>' : ''}
      </div>
      ${file.status === 'failed' && file.status_detail
        ? `<div class="alert error mt-16">${UI.escape(file.status_detail)}</div>` : ''}
      <dl class="kv mt-16">
        <dt>Type</dt><dd>${UI.escape(file.content_type)}</dd>
        <dt>Size</dt><dd>${UI.bytes(file.size_bytes)}</dd>
        <dt>Uploaded by</dt><dd>${UI.escape(file.owner_email || state.user.email)}</dd>
        <dt>Uploaded</dt><dd>${UI.date(file.created_at)}</dd>
        <dt>Original name</dt><dd>${UI.escape(file.original_filename)}</dd>
        <dt>Downloads</dt><dd>${file.download_count}</dd>
        ${extraRows(file.extra)}
      </dl>
      ${csvPreview(file.extra)}
      ${shareUrl ? `
        <h3 class="mt-16">Public link</h3>
        <div class="row mt-16" style="gap:6px">
          <input value="${UI.escape(shareUrl)}" readonly id="share-url">
          <button class="secondary small" data-copy>Copy</button>
        </div>` : ''}
      <div class="row mt-24" style="justify-content:flex-end">
        ${file.status === 'failed' ? '<button class="secondary" data-reprocess>Retry processing</button>' : ''}
        <button data-download>Download</button>
      </div>`);

    modal.querySelector('[data-download]').onclick = () => download(file);
    const copy = modal.querySelector('[data-copy]');
    if (copy) copy.onclick = async () => {
      await navigator.clipboard.writeText(shareUrl);
      UI.toast('Link copied', 'success');
    };
    const retry = modal.querySelector('[data-reprocess]');
    if (retry) retry.onclick = async () => {
      await API.post(`/api/files/${file.id}/reprocess`);
      modal.close();
      UI.toast('Re-queued for processing');
      loadFiles();
    };
  }

  function showMenu(file) {
    const folderOptions = ['<option value="">My files (no folder)</option>']
      .concat(state.folders.map((f) =>
        `<option value="${f.id}" ${f.id === file.folder_id ? 'selected' : ''}>${UI.escape(f.name)}</option>`))
      .join('');

    const modal = UI.modal(`
      <div class="row spread">
        <h2>Manage file</h2>
        <button class="ghost small" data-close>✕</button>
      </div>
      <div class="field mt-16">
        <label for="rename">File name</label>
        <input id="rename" value="${UI.escape(file.filename)}">
      </div>
      <div class="field">
        <label for="folder">Folder</label>
        <select id="folder">${folderOptions}</select>
      </div>
      <div class="field">
        <label for="visibility">Access</label>
        <select id="visibility">
          <option value="private" ${file.visibility === 'private' ? 'selected' : ''}>Private — only me</option>
          <option value="public" ${file.visibility === 'public' ? 'selected' : ''}>Public — anyone with the link</option>
        </select>
      </div>
      <div class="row spread mt-16">
        <button class="danger" data-delete>Delete file</button>
        <div class="row">
          <button class="secondary" data-close>Cancel</button>
          <button data-save>Save changes</button>
        </div>
      </div>`);

    modal.querySelector('[data-save]').onclick = async () => {
      const name = modal.querySelector('#rename').value.trim();
      const folderId = modal.querySelector('#folder').value || null;
      const visibility = modal.querySelector('#visibility').value;
      try {
        if (name && name !== file.filename) await API.patch(`/api/files/${file.id}/rename`, { filename: name });
        if (folderId !== (file.folder_id || null)) await API.patch(`/api/files/${file.id}/move`, { folder_id: folderId });
        if (visibility !== file.visibility) await API.patch(`/api/files/${file.id}/visibility`, { visibility });
        modal.close();
        UI.toast('Saved', 'success');
        await Promise.all([loadFiles(), loadFolders()]);
      } catch (err) { UI.toast(err.message, 'error'); }
    };

    modal.querySelector('[data-delete]').onclick = async () => {
      modal.close();
      if (!await UI.confirm(`"${file.filename}" will be permanently deleted.`, { title: 'Delete file?', confirmLabel: 'Delete' })) return;
      try {
        await API.del(`/api/files/${file.id}`);
        UI.toast('File deleted', 'success');
        await Promise.all([loadFiles(), loadStats(), loadFolders()]);
      } catch (err) { UI.toast(err.message, 'error'); }
    };
  }

  /* ------------------------------------------------------------ filters */
  let searchTimer = null;
  el('search').addEventListener('input', (e) => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => { state.q = e.target.value.trim(); state.page = 1; loadFiles(); }, 300);
  });
  el('sort').addEventListener('change', (e) => { state.sort = e.target.value; state.page = 1; loadFiles(); });
  el('refresh').addEventListener('click', () => { loadFiles(); loadStats(); });
  el('kind-chips').addEventListener('click', (e) => {
    if (!e.target.classList.contains('chip')) return;
    el('kind-chips').querySelectorAll('.chip').forEach((c) => c.classList.remove('active'));
    e.target.classList.add('active');
    state.kind = e.target.dataset.kind;
    state.page = 1;
    loadFiles();
  });
  el('logout').addEventListener('click', async () => {
    try { await API.post('/api/auth/logout'); } catch { /* token may already be gone */ }
    API.clearSession();
    window.location.href = '/';
  });

  /* --------------------------------------------------------------- boot */
  (async function init() {
    paintUser();
    try {
      const [config, me] = await Promise.all([API.get('/api/config'), API.get('/api/auth/me')]);
      state.user = me;
      localStorage.setItem('docvault_user', JSON.stringify(me));
      paintUser();
      fileInput.setAttribute('accept', config.accept);
      el('accept-hint').textContent =
        `${config.allowed_extensions.join(', ')} · up to ${config.max_file_size_mb} MB each`;
    } catch (err) { UI.toast(err.message, 'error'); }

    updateTitles();
    await Promise.all([loadStats(), loadFolders(), loadFiles()]);
  })();
})();
