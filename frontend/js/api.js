/* Shared API client, session storage and small UI helpers. */
const API = (() => {
  const TOKEN_KEY = 'docvault_token';
  const USER_KEY = 'docvault_user';

  const getToken = () => localStorage.getItem(TOKEN_KEY);
  const getUser = () => { try { return JSON.parse(localStorage.getItem(USER_KEY)); } catch { return null; } };
  const setSession = (token, user) => {
    localStorage.setItem(TOKEN_KEY, token);
    localStorage.setItem(USER_KEY, JSON.stringify(user));
  };
  const clearSession = () => { localStorage.removeItem(TOKEN_KEY); localStorage.removeItem(USER_KEY); };

  function authHeaders(extra = {}) {
    const token = getToken();
    return token ? { ...extra, Authorization: `Bearer ${token}` } : extra;
  }

  function errorText(detail) {
    if (typeof detail === 'string') return detail;
    if (detail && typeof detail === 'object') return detail.message || JSON.stringify(detail);
    return 'Something went wrong';
  }

  async function request(path, { method = 'GET', body, raw = false } = {}) {
    const options = { method, headers: authHeaders() };
    if (body !== undefined) {
      options.headers['Content-Type'] = 'application/json';
      options.body = JSON.stringify(body);
    }
    const res = await fetch(path, options);

    if (res.status === 401 && !path.includes('/auth/')) {
      clearSession();
      window.location.href = '/';
      throw new Error('Session expired');
    }
    if (raw) return res;

    let data = null;
    if (res.status !== 204) {
      try { data = await res.json(); } catch { data = null; }
    }
    if (!res.ok) {
      const err = new Error(errorText(data && data.detail));
      err.status = res.status;
      err.detail = data && data.detail;
      throw err;
    }
    return data;
  }

  /* XHR upload so we get real progress events. */
  function upload(file, { folderId, visibility = 'private', allowDuplicate = false, onProgress } = {}) {
    return new Promise((resolve, reject) => {
      const form = new FormData();
      form.append('upload', file);
      if (folderId) form.append('folder_id', folderId);
      form.append('visibility', visibility);
      form.append('allow_duplicate', allowDuplicate ? 'true' : 'false');

      const xhr = new XMLHttpRequest();
      xhr.open('POST', '/api/files/upload');
      const token = getToken();
      if (token) xhr.setRequestHeader('Authorization', `Bearer ${token}`);

      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable && onProgress) onProgress(Math.round((e.loaded / e.total) * 100));
      };
      xhr.onload = () => {
        let data = null;
        try { data = JSON.parse(xhr.responseText); } catch { /* non-JSON error page */ }
        if (xhr.status >= 200 && xhr.status < 300) return resolve(data);
        const err = new Error(errorText(data && data.detail));
        err.status = xhr.status;
        err.detail = data && data.detail;
        reject(err);
      };
      xhr.onerror = () => reject(new Error('Network error during upload'));
      xhr.onabort = () => reject(Object.assign(new Error('Upload cancelled'), { aborted: true }));
      xhr.send(form);

      if (onProgress) onProgress(0);
    });
  }

  return {
    getToken, getUser, setSession, clearSession, authHeaders, request, upload,
    get: (p) => request(p),
    post: (p, body) => request(p, { method: 'POST', body }),
    patch: (p, body) => request(p, { method: 'PATCH', body }),
    del: (p) => request(p, { method: 'DELETE' }),
  };
})();

/* ------------------------------------------------------------- UI helpers */
const UI = {
  toast(message, type = '') {
    let stack = document.querySelector('.toast-stack');
    if (!stack) {
      stack = document.createElement('div');
      stack.className = 'toast-stack';
      document.body.appendChild(stack);
    }
    const el = document.createElement('div');
    el.className = `toast ${type}`;
    el.textContent = message;
    stack.appendChild(el);
    setTimeout(() => el.remove(), 3800);
  },

  bytes(n) {
    if (n === null || n === undefined) return '—';
    const units = ['B', 'KB', 'MB', 'GB', 'TB'];
    let value = Number(n), i = 0;
    while (value >= 1024 && i < units.length - 1) { value /= 1024; i += 1; }
    return `${i === 0 ? value : value.toFixed(1)} ${units[i]}`;
  },

  date(iso, withTime = true) {
    if (!iso) return '—';
    const d = new Date(iso);
    const opts = { year: 'numeric', month: 'short', day: 'numeric' };
    if (withTime) { opts.hour = '2-digit'; opts.minute = '2-digit'; }
    return d.toLocaleDateString(undefined, opts);
  },

  icon(ext) {
    const map = {
      '.pdf': '📕', '.csv': '📊', '.docx': '📄',
      '.png': '🖼️', '.jpg': '🖼️', '.jpeg': '🖼️', '.gif': '🖼️', '.webp': '🖼️', '.bmp': '🖼️',
    };
    return map[ext] || '📎';
  },

  escape(text) {
    const div = document.createElement('div');
    div.textContent = text === null || text === undefined ? '' : String(text);
    return div.innerHTML;
  },

  /* Simple promise-based modal. Returns the <div class="modal"> element. */
  modal(html, { onClose } = {}) {
    const backdrop = document.createElement('div');
    backdrop.className = 'modal-backdrop';
    backdrop.innerHTML = `<div class="card modal">${html}</div>`;
    backdrop.addEventListener('click', (e) => { if (e.target === backdrop) close(); });
    const onKey = (e) => { if (e.key === 'Escape') close(); };
    document.addEventListener('keydown', onKey);
    function close() {
      document.removeEventListener('keydown', onKey);
      backdrop.remove();
      if (onClose) onClose();
    }
    document.body.appendChild(backdrop);
    const modal = backdrop.querySelector('.modal');
    modal.close = close;
    modal.querySelectorAll('[data-close]').forEach((b) => b.addEventListener('click', close));
    return modal;
  },

  confirm(message, { title = 'Are you sure?', danger = true, confirmLabel = 'Confirm' } = {}) {
    return new Promise((resolve) => {
      let answered = false;
      const modal = UI.modal(`
        <h2>${UI.escape(title)}</h2>
        <p class="muted" style="margin:8px 0 20px">${UI.escape(message)}</p>
        <div class="row" style="justify-content:flex-end">
          <button class="secondary" data-no>Cancel</button>
          <button class="${danger ? 'danger' : ''}" data-yes>${UI.escape(confirmLabel)}</button>
        </div>`, { onClose: () => { if (!answered) resolve(false); } });
      modal.querySelector('[data-no]').onclick = () => { answered = true; modal.close(); resolve(false); };
      modal.querySelector('[data-yes]').onclick = () => { answered = true; modal.close(); resolve(true); };
    });
  },

  requireAuth() {
    if (!API.getToken()) { window.location.href = '/'; return false; }
    return true;
  },
};
