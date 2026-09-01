/* Public share page — no sign-in required. */
(async () => {
  const token = window.location.pathname.split('/').filter(Boolean).pop();
  const card = document.getElementById('share-card');

  function fail(message) {
    card.innerHTML = `
      <div class="empty">
        <div class="icon">🔒</div>
        <p>${UI.escape(message)}</p>
        <a class="btn secondary small mt-16" href="/">Go to DocVault</a>
      </div>`;
  }

  try {
    const res = await fetch(`/api/share/${encodeURIComponent(token)}`);
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      return fail(body.detail || 'This link is invalid or has been revoked.');
    }
    const info = await res.json();

    const preview = info.has_thumbnail
      ? `<div class="thumb" style="height:180px;border-radius:10px;margin-bottom:16px">
           <img src="/api/share/${encodeURIComponent(token)}/thumbnail" alt="">
         </div>`
      : `<div class="thumb" style="height:120px;border-radius:10px;margin-bottom:16px;font-size:44px">
           ${UI.icon(info.extension)}
         </div>`;

    const csv = info.extra && info.extra.kind === 'csv'
      ? `<dt>Rows</dt><dd>${UI.escape(info.extra.row_count)} × ${UI.escape(info.extra.columns)} columns</dd>` : '';
    const pages = info.extra && info.extra.pages ? `<dt>Pages</dt><dd>${UI.escape(info.extra.pages)}</dd>` : '';

    card.innerHTML = `
      ${preview}
      <h2 style="word-break:break-word">${UI.escape(info.filename)}</h2>
      <dl class="kv mt-16">
        <dt>Size</dt><dd>${UI.escape(info.size_human)}</dd>
        <dt>Type</dt><dd>${UI.escape(info.content_type)}</dd>
        <dt>Shared by</dt><dd>${UI.escape(info.uploaded_by)}</dd>
        <dt>Uploaded</dt><dd>${UI.date(info.uploaded_at)}</dd>
        <dt>Downloads</dt><dd>${UI.escape(info.download_count)}</dd>
        ${pages}${csv}
      </dl>
      <a class="btn mt-24" style="display:block;text-align:center"
         href="/api/share/${encodeURIComponent(token)}/download">Download file</a>`;
  } catch (err) {
    fail('Could not load this file. Please try again.');
  }
})();
