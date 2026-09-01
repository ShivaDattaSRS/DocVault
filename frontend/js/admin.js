/* Admin dashboard: platform stats, users, all files and the activity log. */
(() => {
  if (!UI.requireAuth()) return;

  const el = (id) => document.getElementById(id);
  const state = { filePage: 1, filePageSize: 25, fileTotal: 0 };

  const KIND_LABEL = { pdf: 'PDF documents', image: 'Images', csv: 'CSV files', docx: 'Word documents', other: 'Other' };

  /* --------------------------------------------------------------- stats */
  async function loadStats() {
    const s = await API.get('/api/admin/stats');

    el('stats').innerHTML = [
      ['Users', s.total_users, `${s.active_users} active`],
      ['Files stored', s.total_files, `${UI.bytes(s.total_bytes)} logical`],
      ['Disk used', UI.bytes(s.unique_bytes), 'after de-duplication'],
      ['Saved by dedup', UI.bytes(s.bytes_saved_by_dedup), 'shared content blocks'],
      ['Processing failures', s.processing_failures, s.processing_failures ? 'needs attention' : 'all healthy'],
    ].map(([label, value, sub]) => `
      <div class="stat">
        <div class="value">${UI.escape(value)}</div>
        <div class="label">${UI.escape(label)}</div>
        <div class="small muted">${UI.escape(sub)}</div>
      </div>`).join('');

    const max = Math.max(1, ...s.uploads_last_7_days.map((d) => d.count));
    el('chart').innerHTML = s.uploads_last_7_days.map((d) => {
      const height = Math.round((d.count / max) * 88);
      const day = new Date(`${d.date}T00:00:00`).toLocaleDateString(undefined, { weekday: 'short' });
      return `
        <div class="bar ${d.count ? 'has-data' : ''}" title="${d.date}: ${d.count} uploads (${UI.bytes(d.bytes)})">
          <span style="height:${Math.max(3, height)}px"></span>
          <span class="lbl">${day}<br>${d.count}</span>
        </div>`;
    }).join('');

    const types = Object.entries(s.files_by_type);
    const totalTyped = types.reduce((sum, [, n]) => sum + n, 0) || 1;
    el('type-breakdown').innerHTML = types.length
      ? types.map(([kind, count]) => `
          <div style="margin-bottom:11px">
            <div class="row spread small">
              <span>${UI.escape(KIND_LABEL[kind] || kind)}</span>
              <span class="muted">${count}</span>
            </div>
            <div class="meter" style="margin-top:5px"><span style="width:${(count / totalTyped) * 100}%"></span></div>
          </div>`).join('')
      : '<p class="small muted">No files uploaded yet.</p>';

    try {
      const queue = await API.get('/api/admin/queue');
      el('queue-info').textContent = `Job backend: ${queue.backend}${queue.depth !== null ? ` · ${queue.depth} queued` : ''}`;
    } catch { el('queue-info').textContent = ''; }
  }

  /* --------------------------------------------------------------- users */
  async function loadUsers(q = '') {
    const users = await API.get(`/api/admin/users${q ? `?q=${encodeURIComponent(q)}` : ''}`);
    el('user-rows').innerHTML = users.length ? users.map((u) => {
      const pct = u.quota_bytes ? Math.min(100, (u.used_bytes / u.quota_bytes) * 100) : 0;
      return `
        <tr data-id="${u.id}">
          <td>
            <div style="font-weight:600">${UI.escape(u.full_name || '—')} ${u.is_admin ? '<span class="badge">admin</span>' : ''}</div>
            <div class="small muted">${UI.escape(u.email)}</div>
          </td>
          <td style="min-width:160px">
            <div class="small">${UI.bytes(u.used_bytes)} / ${UI.bytes(u.quota_bytes)}</div>
            <div class="meter ${pct >= 95 ? 'full' : pct >= 75 ? 'warn' : ''}" style="margin-top:5px">
              <span style="width:${pct}%"></span>
            </div>
          </td>
          <td><span class="badge ${u.is_active ? 'ready' : 'failed'}">${u.is_active ? 'Active' : 'Deactivated'}</span></td>
          <td class="small muted">${UI.date(u.created_at, false)}</td>
          <td class="row" style="gap:5px;border-bottom:1px solid var(--border)">
            <button class="secondary small" data-quota>Quota</button>
            <button class="secondary small" data-toggle>${u.is_active ? 'Deactivate' : 'Activate'}</button>
          </td>
        </tr>`;
    }).join('') : '<tr><td colspan="5" class="muted">No users found.</td></tr>';

    el('user-rows').querySelectorAll('tr[data-id]').forEach((row) => {
      const id = row.dataset.id;
      const user = users.find((u) => u.id === id);
      const quotaBtn = row.querySelector('[data-quota]');
      const toggleBtn = row.querySelector('[data-toggle]');
      if (quotaBtn) quotaBtn.onclick = () => editQuota(user);
      if (toggleBtn) toggleBtn.onclick = async () => {
        try {
          await API.patch(`/api/admin/users/${id}/status`, { is_active: !user.is_active });
          UI.toast('User updated', 'success');
          loadUsers(el('user-search').value.trim());
        } catch (err) { UI.toast(err.message, 'error'); }
      };
    });
  }

  function editQuota(user) {
    const currentMb = Math.round(user.quota_bytes / (1024 * 1024));
    const modal = UI.modal(`
      <h2>Storage quota</h2>
      <p class="small muted" style="margin:6px 0 16px">${UI.escape(user.email)} — currently using ${UI.bytes(user.used_bytes)}</p>
      <div class="field">
        <label for="quota">Quota (MB)</label>
        <input id="quota" type="number" min="1" value="${currentMb}">
      </div>
      <div class="row" style="justify-content:flex-end">
        <button class="secondary" data-close>Cancel</button>
        <button data-save>Save</button>
      </div>`);
    modal.querySelector('[data-save]').onclick = async () => {
      try {
        await API.patch(`/api/admin/users/${user.id}/quota`, { quota_mb: Number(modal.querySelector('#quota').value) });
        modal.close();
        UI.toast('Quota updated', 'success');
        loadUsers(el('user-search').value.trim());
        loadStats();
      } catch (err) { UI.toast(err.message, 'error'); }
    };
  }

  /* --------------------------------------------------------------- files */
  async function loadFiles() {
    const params = new URLSearchParams({ page: state.filePage, page_size: state.filePageSize });
    const q = el('file-search').value.trim();
    const owner = el('owner-search').value.trim();
    const status = el('status-filter').value;
    if (q) params.set('q', q);
    if (owner) params.set('owner', owner);
    if (status) params.set('status', status);

    const res = await API.get(`/api/admin/files?${params}`);
    state.fileTotal = res.total;

    el('file-rows').innerHTML = res.items.length ? res.items.map((f) => `
      <tr data-id="${f.id}">
        <td>
          <div style="font-weight:600">${UI.icon(f.extension)} ${UI.escape(f.filename)}</div>
          <div class="small muted">${UI.escape(f.content_type)}${f.is_duplicate ? ' · duplicate' : ''}</div>
        </td>
        <td class="small">${UI.escape(f.owner_email || '—')}</td>
        <td class="small">${UI.bytes(f.size_bytes)}</td>
        <td>
          <span class="badge ${f.status}">${f.status}</span>
          ${f.visibility === 'public' ? '<span class="badge public">public</span>' : ''}
        </td>
        <td class="small muted">${UI.date(f.created_at)}</td>
        <td><button class="danger small" data-delete>Delete</button></td>
      </tr>`).join('') : '<tr><td colspan="6" class="muted">No files found.</td></tr>';

    el('file-rows').querySelectorAll('tr[data-id]').forEach((row) => {
      const file = res.items.find((f) => f.id === row.dataset.id);
      row.querySelector('[data-delete]').onclick = async () => {
        if (!await UI.confirm(`Permanently delete "${file.filename}" owned by ${file.owner_email}?`,
          { title: 'Delete file?', confirmLabel: 'Delete' })) return;
        try {
          await API.del(`/api/admin/files/${file.id}`);
          UI.toast('File deleted', 'success');
          loadFiles();
          loadStats();
        } catch (err) { UI.toast(err.message, 'error'); }
      };
    });

    const pages = Math.ceil(state.fileTotal / state.filePageSize);
    el('file-pager').innerHTML = pages > 1 ? `
      <button class="secondary small" ${state.filePage <= 1 ? 'disabled' : ''} data-prev>← Prev</button>
      <span class="small muted">Page ${state.filePage} of ${pages} · ${state.fileTotal} files</span>
      <button class="secondary small" ${state.filePage >= pages ? 'disabled' : ''} data-next>Next →</button>` : '';
    const prev = el('file-pager').querySelector('[data-prev]');
    const next = el('file-pager').querySelector('[data-next]');
    if (prev) prev.onclick = () => { state.filePage -= 1; loadFiles(); };
    if (next) next.onclick = () => { state.filePage += 1; loadFiles(); };
  }

  /* ---------------------------------------------------------------- logs */
  const ACTION_STYLE = {
    login: 'ready', upload: 'ready', download: 'public', public_download: 'public',
    delete: 'failed', admin_delete: 'failed', login_failed: 'failed',
    upload_rejected: 'failed', otp_failed: 'failed',
  };

  async function loadLogs() {
    const action = el('log-action').value;
    const logs = await API.get(`/api/admin/logs?limit=150${action ? `&action=${action}` : ''}`);
    el('log-rows').innerHTML = logs.length ? logs.map((l) => `
      <tr>
        <td class="small muted" style="white-space:nowrap">${UI.date(l.created_at)}</td>
        <td><span class="badge ${ACTION_STYLE[l.action] || ''}">${UI.escape(l.action)}</span></td>
        <td class="small">${UI.escape(l.user_email || 'anonymous')}</td>
        <td class="small">${UI.escape(l.detail || '—')}</td>
        <td class="small muted">${UI.escape(l.ip_address || '—')}</td>
      </tr>`).join('') : '<tr><td colspan="5" class="muted">No activity recorded yet.</td></tr>';
  }

  /* -------------------------------------------------------------- events */
  let userTimer = null;
  el('user-search').addEventListener('input', (e) => {
    clearTimeout(userTimer);
    userTimer = setTimeout(() => loadUsers(e.target.value.trim()), 300);
  });

  let fileTimer = null;
  const refileSoon = () => { clearTimeout(fileTimer); fileTimer = setTimeout(() => { state.filePage = 1; loadFiles(); }, 300); };
  el('file-search').addEventListener('input', refileSoon);
  el('owner-search').addEventListener('input', refileSoon);
  el('status-filter').addEventListener('change', () => { state.filePage = 1; loadFiles(); });
  el('log-action').addEventListener('change', loadLogs);
  el('log-refresh').addEventListener('click', () => { loadLogs(); loadStats(); });
  el('logout').addEventListener('click', async () => {
    try { await API.post('/api/auth/logout'); } catch { /* ignore */ }
    API.clearSession();
    window.location.href = '/';
  });

  /* ---------------------------------------------------------------- boot */
  (async function init() {
    let me;
    try {
      me = await API.get('/api/auth/me');
    } catch (err) {
      UI.toast(err.message, 'error');
      return;
    }
    if (!me.is_admin) {
      UI.toast('Administrator access required', 'error');
      setTimeout(() => { window.location.href = '/dashboard'; }, 1200);
      return;
    }
    el('user-email').textContent = me.email;
    el('avatar').textContent = (me.full_name || me.email).charAt(0).toUpperCase();

    try {
      await Promise.all([loadStats(), loadUsers(), loadFiles(), loadLogs()]);
    } catch (err) { UI.toast(err.message, 'error'); }
  })();
})();
