/**
 * DriveX Dashboard — Client-Side Logic
 * Handles S3 navigation, file operations, search, upload, UI state.
 */

// ── State ──────────────────────────────────────────────────────────────────
const state = {
  bucket: null,
  prefix: '',
  view: 'list',
  items: [],
  ctxTarget: null,  // currently right-clicked item
};

const csrf = () => document.getElementById('csrfToken').value;

// ── Initialisation ─────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  // Auto-select first bucket
  const firstBtn = document.querySelector('.bucket-item');
  if (firstBtn) {
    selectBucket(firstBtn.dataset.bucket);
  }

  // Upload drop zone
  const dropZone = document.getElementById('uploadDropZone');
  if (dropZone) {
    dropZone.addEventListener('dragover', e => {
      e.preventDefault();
      dropZone.classList.add('drag-over');
    });
    dropZone.addEventListener('dragleave', () => dropZone.classList.remove('drag-over'));
    dropZone.addEventListener('drop', e => {
      e.preventDefault();
      dropZone.classList.remove('drag-over');
      handleFileSelect(e.dataTransfer.files);
    });
  }

  // Close context menu on outside click
  document.addEventListener('click', () => hideContextMenu());
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape') {
      hideContextMenu();
      closeConfirmModal();
      closeUploadModal();
      closeFolderModal();
      closeRenameModal();
      closePresignModal();
      closeInfoModal();
      closeEditMetaModal();
      closeEmConfirm();
    }
  });
});

// ── Bucket Selection ───────────────────────────────────────────────────────
function selectBucket(bucket) {
  state.bucket = bucket;
  state.prefix = '';

  // Update sidebar active state
  document.querySelectorAll('.bucket-item').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.bucket === bucket);
  });

  // Update breadcrumb bucket name
  document.getElementById('breadcrumbBucketName').textContent = bucket;

  // Show action bar
  document.getElementById('actionBar').style.display = 'flex';

  loadObjects();
}

// ── Navigate (breadcrumb / folder click) ──────────────────────────────────
function navigateTo(prefix) {
  state.prefix = prefix;
  loadObjects();
  updateBreadcrumb();
}

function updateBreadcrumb() {
  const bc = document.getElementById('breadcrumb');
  const parts = state.prefix ? state.prefix.split('/').filter(Boolean) : [];
  let html = `
    <button class="breadcrumb-item" onclick="navigateTo('')">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>
      <span id="breadcrumbBucketName">${state.bucket}</span>
    </button>`;

  let built = '';
  parts.forEach((part, i) => {
    built += part + '/';
    const isLast = i === parts.length - 1;
    html += `<span class="breadcrumb-sep">›</span>`;
    const prefix = built;
    if (isLast) {
      html += `<button class="breadcrumb-item active">${part}</button>`;
    } else {
      html += `<button class="breadcrumb-item" onclick="navigateTo('${prefix}')">${part}</button>`;
    }
  });
  bc.innerHTML = html;
}

// ── Load Objects ───────────────────────────────────────────────────────────
async function loadObjects() {
  if (!state.bucket) return;

  showLoading('Loading files...');
  try {
    const res = await fetch(
      `/api/s3/list?bucket=${encodeURIComponent(state.bucket)}&prefix=${encodeURIComponent(state.prefix)}`
    );
    if (!res.ok) {
      const err = await res.json();
      showToast(err.detail || 'Failed to load files', 'error');
      return;
    }
    const data = await res.json();
    state.items = [...data.folders, ...data.files];
    renderItems(data.folders, data.files);
    document.getElementById('itemCount').textContent =
      `${data.folders.length} folder${data.folders.length !== 1 ? 's' : ''}, ${data.files.length} file${data.files.length !== 1 ? 's' : ''}`;

    // Hide search results, show file view
    document.getElementById('searchResults').style.display = 'none';
    document.getElementById('searchInput').value = '';
    document.getElementById('clearSearch').style.display = 'none';
    showFileView();
    updateBreadcrumb();
  } catch (e) {
    showToast('Network error loading files', 'error');
  } finally {
    hideLoading();
  }
}

function showFileView() {
  document.getElementById('emptyState').style.display = 'none';
  if (state.view === 'list') {
    document.getElementById('listView').style.display = 'block';
    document.getElementById('gridView').style.display = 'none';
  } else {
    document.getElementById('listView').style.display = 'none';
    document.getElementById('gridView').style.display = 'grid';
  }
}

// ── Render ─────────────────────────────────────────────────────────────────
function renderItems(folders, files) {
  renderListView(folders, files);
  renderGridView(folders, files);
}

function getIconClass(type) {
  const map = {
    folder: 'icon-folder', images: 'icon-images', video: 'icon-video',
    audio: 'icon-audio', document: 'icon-document', text: 'icon-text',
    code: 'icon-code', archive: 'icon-archive', file: 'icon-file'
  };
  return map[type] || 'icon-file';
}

function getIconSvg(type) {
  const icons = {
    folder: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>',
    images: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/></svg>',
    video: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="23 7 16 12 23 17 23 7"/><rect x="1" y="5" width="15" height="14" rx="2" ry="2"/></svg>',
    audio: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/></svg>',
    document: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>',
    text: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><line x1="8" y1="13" x2="16" y2="13"/><line x1="8" y1="17" x2="16" y2="17"/></svg>',
    code: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/></svg>',
    archive: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="21 8 21 21 3 21 3 8"/><rect x="1" y="3" width="22" height="5"/><line x1="10" y1="12" x2="14" y2="12"/></svg>',
    file: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M13 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><polyline points="13 2 13 9 20 9"/></svg>',
  };
  return icons[type] || icons.file;
}

function renderListView(folders, files) {
  const tbody = document.getElementById('fileTableBody');
  if (!folders.length && !files.length) {
    tbody.innerHTML = `<tr><td colspan="5" style="text-align:center;padding:48px;color:var(--text-muted);">This folder is empty</td></tr>`;
    return;
  }

  const rows = [...folders, ...files].map(item => {
    const iconCls = getIconClass(item.type);
    const icon = getIconSvg(item.type);
    const isFolder = item.type === 'folder';
    const onClick = isFolder
      ? `navigateTo('${escHtml(item.key)}')`
      : '';

    return `
      <tr oncontextmenu="showContextMenu(event, ${JSON.stringify(JSON.stringify(item))})">
        <td>
          <div class="file-name-cell" ${onClick ? `onclick="${onClick}"` : ''} ${isFolder ? 'style="cursor:pointer;"' : ''}>
            <div class="file-icon ${iconCls}">${icon}</div>
            <span class="file-name-text" title="${escHtml(item.name)}">${escHtml(item.name)}</span>
          </div>
        </td>
        <td class="file-size">${item.size_human || '-'}</td>
        <td class="file-date">${item.last_modified_human || '-'}</td>
        <td><span class="file-type-badge">${item.type}</span></td>
        <td>
          <div class="row-actions">
            ${isFolder ? '' : `
              <button class="row-action-btn" onclick="downloadFile('${escHtml(item.key)}')" title="Download">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
              </button>
              <button class="row-action-btn" onclick="openPresignModal('${escHtml(item.key)}')" title="Copy URL">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg>
              </button>
              <button class="row-action-btn" onclick="openInfoModal('${escHtml(item.key)}')" title="View Info">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/></svg>
              </button>
              <button class="row-action-btn" onclick="openEditMetaModal('${escHtml(item.key)}')" title="Edit Metadata">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M20.59 13.41l-7.17 7.17a2 2 0 0 1-2.83 0L2 12V2h10l8.59 8.59a2 2 0 0 1 0 2.82z"/><line x1="7" y1="7" x2="7.01" y2="7"/></svg>
              </button>
            `}
            <button class="row-action-btn" onclick="openRenameModal('${escHtml(item.key)}', '${escHtml(item.name)}')" title="Rename">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>
            </button>
            <button class="row-action-btn danger" onclick="confirmDelete('${escHtml(item.key)}', ${isFolder})" title="Delete">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/></svg>
            </button>
          </div>
        </td>
      </tr>`;
  });

  tbody.innerHTML = rows.join('');
}

function renderGridView(folders, files) {
  const grid = document.getElementById('gridView');
  const items = [...folders, ...files];
  if (!items.length) { grid.innerHTML = ''; return; }

  grid.innerHTML = items.map(item => {
    const iconCls = getIconClass(item.type);
    const icon = getIconSvg(item.type);
    const isFolder = item.type === 'folder';
    const onClick = isFolder ? `navigateTo('${escHtml(item.key)}')` : `downloadFile('${escHtml(item.key)}')`;

    return `
      <div class="grid-item" onclick="${onClick}">
        <div class="grid-actions" onclick="event.stopPropagation()">
          <button class="icon-btn" style="padding:4px" onclick="openRenameModal('${escHtml(item.key)}','${escHtml(item.name)}')" title="Rename">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>
          </button>
          <button class="icon-btn" style="padding:4px;color:var(--error)" onclick="confirmDelete('${escHtml(item.key)}',${isFolder})" title="Delete">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/></svg>
          </button>
        </div>
        <div class="grid-icon ${iconCls}">${icon}</div>
        <div class="grid-name" title="${escHtml(item.name)}">${escHtml(item.name)}</div>
        <div class="grid-meta">${item.size_human || (isFolder ? 'Folder' : '-')}</div>
      </div>`;
  }).join('');
}

// ── View Toggle ────────────────────────────────────────────────────────────
function setView(v) {
  state.view = v;
  document.getElementById('listViewBtn').classList.toggle('active', v === 'list');
  document.getElementById('gridViewBtn').classList.toggle('active', v === 'grid');
  if (state.bucket) showFileView();
}

// ── Refresh ────────────────────────────────────────────────────────────────
function refreshView() {
  loadObjects();
}

// ── Search ─────────────────────────────────────────────────────────────────
let searchTimer = null;

function debounceSearch(query) {
  clearTimeout(searchTimer);
  const clearBtn = document.getElementById('clearSearch');
  clearBtn.style.display = query ? 'flex' : 'none';

  if (!query) {
    loadObjects();
    return;
  }
  searchTimer = setTimeout(() => runSearch(query), 500);
}

async function runSearch(query) {
  if (!state.bucket || !query) return;
  showLoading('Searching...');
  try {
    const res = await fetch(
      `/api/s3/search?bucket=${encodeURIComponent(state.bucket)}&query=${encodeURIComponent(query)}&prefix=${encodeURIComponent(state.prefix)}`
    );
    const data = await res.json();

    // Show search results panel
    document.getElementById('listView').style.display = 'none';
    document.getElementById('gridView').style.display = 'none';
    document.getElementById('emptyState').style.display = 'none';
    const panel = document.getElementById('searchResults');
    panel.style.display = 'block';
    document.getElementById('searchResultCount').textContent = `${data.count} result${data.count !== 1 ? 's' : ''}`;

    const tbody = document.getElementById('searchTableBody');
    if (!data.results.length) {
      tbody.innerHTML = `<tr><td colspan="5" style="text-align:center;padding:32px;color:var(--text-muted)">No files found matching "${escHtml(query)}"</td></tr>`;
      return;
    }
    tbody.innerHTML = data.results.map(r => `
      <tr>
        <td>
          <div class="file-name-cell">
            <div class="file-icon ${getIconClass(r.type)}">${getIconSvg(r.type)}</div>
            <span class="file-name-text" title="${escHtml(r.key)}">${escHtml(r.name)}</span>
          </div>
        </td>
        <td><span class="file-type-badge">${r.bucket}</span></td>
        <td class="file-size">${r.size_human}</td>
        <td class="file-date">${r.last_modified_human}</td>
        <td>
          <div class="row-actions" style="opacity:1">
            <button class="row-action-btn" onclick="downloadFile('${escHtml(r.key)}')">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
              Download
            </button>
          </div>
        </td>
      </tr>`).join('');
  } catch (e) {
    showToast('Search failed', 'error');
  } finally {
    hideLoading();
  }
}

function clearSearch() {
  document.getElementById('searchInput').value = '';
  document.getElementById('clearSearch').style.display = 'none';
  loadObjects();
}

// ── File Operations ────────────────────────────────────────────────────────

function downloadFile(key) {
  const url = `/api/s3/download?bucket=${encodeURIComponent(state.bucket)}&key=${encodeURIComponent(key)}`;
  const a = document.createElement('a');
  a.href = url;
  a.style.display = 'none';
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  showToast('Download started', 'success');
}

async function confirmDelete(key, isFolder) {
  const name = key.split('/').filter(Boolean).pop() || key;
  const type = isFolder ? 'folder' : 'file';
  showConfirmModal(
    `Delete ${type}`,
    `Permanently delete "${name}"? ${isFolder ? 'All contents will be removed.' : 'This cannot be undone.'}`,
    async () => {
      showLoading('Deleting...');
      try {
        const res = await fetch(
          `/api/s3/delete?bucket=${encodeURIComponent(state.bucket)}&key=${encodeURIComponent(key)}&is_folder=${isFolder}`,
          {
            method: 'DELETE',
            headers: { 'X-CSRF-Token': csrf() },
          }
        );
        const data = await res.json();
        if (res.ok) {
          showToast(data.message || 'Deleted', 'success');
          loadObjects();
        } else {
          showToast(data.detail || 'Delete failed', 'error');
        }
      } catch (e) {
        showToast('Delete failed', 'error');
      } finally {
        hideLoading();
      }
    }
  );
}

// ── Upload ─────────────────────────────────────────────────────────────────
let uploadFiles = [];

function openUploadModal() {
  uploadFiles = [];
  document.getElementById('uploadQueue').style.display = 'none';
  document.getElementById('uploadQueue').innerHTML = '';
  document.getElementById('uploadStartBtn').disabled = true;
  document.getElementById('fileInput').value = '';

  // Show upload target path in subtitle
  const path = state.bucket + (state.prefix ? '/' + state.prefix : '/');
  document.getElementById('uploadTargetPath').textContent = path;

  // Reset metadata panel
  resetMetadataPanel();

  document.getElementById('uploadModal').style.display = 'flex';
}

function closeUploadModal() {
  document.getElementById('uploadModal').style.display = 'none';
  uploadFiles = [];
}

function handleFileSelect(files) {
  uploadFiles = Array.from(files);
  const queue = document.getElementById('uploadQueue');
  queue.style.display = 'block';
  queue.innerHTML = uploadFiles.map((f, i) => `
    <div class="upload-item" id="uploadItem_${i}">
      <div class="file-icon ${getIconClass(guessTypeFromName(f.name))}"
           style="width:28px;height:28px;font-size:14px">
        ${getIconSvg(guessTypeFromName(f.name))}
      </div>
      <div style="flex:1;min-width:0">
        <div class="upload-item-name">${escHtml(f.name)}</div>
        <div class="upload-progress">
          <div class="upload-progress-bar" id="uploadBar_${i}"></div>
        </div>
      </div>
      <span class="upload-item-size">${humanSize(f.size)}</span>
    </div>`).join('');
  document.getElementById('uploadStartBtn').disabled = false;
}

async function startUpload() {
  if (!uploadFiles.length) return;

  // Collect & validate metadata before touching any file
  const sysEntries  = collectSystemMeta();
  const userEntries = collectUserMeta();
  if (sysEntries === null || userEntries === null) return; // toast already shown

  const systemJson = sysEntries.length  ? JSON.stringify(sysEntries)  : '';
  const userJson   = userEntries.length ? JSON.stringify(userEntries) : '';

  document.getElementById('uploadStartBtn').disabled = true;

  for (let i = 0; i < uploadFiles.length; i++) {
    const file = uploadFiles[i];
    const bar  = document.getElementById(`uploadBar_${i}`);

    const formData = new FormData();
    formData.append('bucket',          state.bucket);
    formData.append('prefix',          state.prefix);
    formData.append('csrf_token',      csrf());
    formData.append('system_metadata', systemJson);
    formData.append('user_metadata',   userJson);
    formData.append('file',            file);

    let progress = 0;
    const ticker = setInterval(() => {
      progress = Math.min(progress + Math.random() * 15, 85);
      if (bar) bar.style.width = progress + '%';
    }, 300);

    try {
      const res  = await fetch('/api/s3/upload', { method: 'POST', body: formData });
      clearInterval(ticker);
      if (bar) bar.style.width = '100%';
      const data = await res.json();
      if (res.ok) {
        showToast(`"${file.name}" uploaded successfully`, 'success');
      } else {
        showToast(data.detail || `Upload failed: ${file.name}`, 'error');
        if (bar) bar.style.background = 'var(--error)';
      }
    } catch (e) {
      clearInterval(ticker);
      showToast(`Upload failed: ${file.name}`, 'error');
    }
  }

  setTimeout(() => { closeUploadModal(); loadObjects(); }, 800);
}

// ── Metadata Settings Panel ────────────────────────────────────────────────

/** All allowed system-defined key names (must match backend SYSTEM_METADATA_KEYS). */
const SYSTEM_KEYS = [
  'Content-Type',
  'Cache-Control',
  'Content-Disposition',
  'Content-Encoding',
  'Content-Language',
  'Expires',
  'Website-Redirect-Location',
];

/** Placeholder hints per system key to guide the user. */
const SYSTEM_KEY_HINTS = {
  'Content-Type':              'e.g. image/png, application/pdf',
  'Cache-Control':             'e.g. max-age=86400, no-cache',
  'Content-Disposition':       'e.g. attachment; filename="file.pdf"',
  'Content-Encoding':          'e.g. gzip, identity',
  'Content-Language':          'e.g. en-US, fr',
  'Expires':                   'e.g. Thu, 01 Jan 2026 00:00:00 GMT',
  'Website-Redirect-Location': 'e.g. /new-path or https://example.com',
};

let metaPanelOpen = true;

function toggleMetaPanel() {
  metaPanelOpen = !metaPanelOpen;
  const panel = document.getElementById('metaPanel');
  const btn   = document.getElementById('metaCollapseBtn');
  panel.style.display = metaPanelOpen ? '' : 'none';
  btn.classList.toggle('collapsed', !metaPanelOpen);
  btn.title = metaPanelOpen ? 'Collapse' : 'Expand';
}

function resetMetadataPanel() {
  document.getElementById('systemMetaRows').innerHTML = '';
  document.getElementById('userMetaRows').innerHTML   = '';
  syncEmptyState('sys');
  syncEmptyState('usr');
  // Ensure panel is open
  metaPanelOpen = true;
  document.getElementById('metaPanel').style.display = '';
  document.getElementById('metaCollapseBtn').classList.remove('collapsed');
}

/** Show/hide the "no rows" empty-state text for a section. */
function syncEmptyState(section) {
  const rowsEl = document.getElementById(section === 'sys' ? 'systemMetaRows' : 'userMetaRows');
  const emptyEl = document.getElementById(section === 'sys' ? 'sysEmpty' : 'usrEmpty');
  const hasRows = rowsEl.querySelectorAll('.meta-row').length > 0;
  emptyEl.style.display = hasRows ? 'none' : 'block';
}

/* ── Remove button SVG (shared) ── */
const REMOVE_SVG = `<svg width="13" height="13" viewBox="0 0 24 24" fill="none"
  stroke="currentColor" stroke-width="2.5">
  <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
</svg>`;

/* ── System-defined row ── */
function addSystemRow() {
  const container = document.getElementById('systemMetaRows');
  const row = document.createElement('div');
  row.className = 'meta-row sys-row';

  // Build key dropdown
  const keyOpts = SYSTEM_KEYS.map(k =>
    `<option value="${k}">${k}</option>`
  ).join('');

  row.innerHTML = `
    <select class="sys-key" onchange="onSysKeyChange(this)">
      <option value="" disabled selected>Select key…</option>
      ${keyOpts}
    </select>
    <input type="text" class="sys-value"
           placeholder="Select a key first"
           disabled maxlength="1024" />
    <button type="button" class="meta-row-remove"
            onclick="removeRow(this,'sys')" title="Remove">${REMOVE_SVG}</button>`;

  container.appendChild(row);
  syncEmptyState('sys');
  row.querySelector('.sys-key').focus();
}

/** Enable value input and update placeholder when a system key is chosen. */
function onSysKeyChange(sel) {
  const row      = sel.closest('.meta-row');
  const valInput = row.querySelector('.sys-value');
  const key      = sel.value;
  valInput.disabled     = false;
  valInput.placeholder  = SYSTEM_KEY_HINTS[key] || 'Enter value…';
  // Clear any previous validation error
  sel.classList.remove('input-error');
  valInput.classList.remove('input-error');
  valInput.focus();
}

/* ── User-defined row ── */
function addUserRow() {
  const container = document.getElementById('userMetaRows');
  const row = document.createElement('div');
  row.className = 'meta-row usr-row';

  row.innerHTML = `
    <input type="text" class="usr-key"
           placeholder="Key (e.g. department)"
           maxlength="128" />
    <input type="text" class="usr-value"
           placeholder="Value (e.g. finance)"
           maxlength="1024" />
    <button type="button" class="meta-row-remove"
            onclick="removeRow(this,'usr')" title="Remove">${REMOVE_SVG}</button>`;

  container.appendChild(row);
  syncEmptyState('usr');
  row.querySelector('.usr-key').focus();
}

function removeRow(btn, section) {
  btn.closest('.meta-row').remove();
  syncEmptyState(section);
}

/* ── Collect & validate system metadata ── */
function collectSystemMeta() {
  const rows    = document.querySelectorAll('#systemMetaRows .meta-row');
  const entries = [];
  let valid     = true;

  rows.forEach(row => {
    const keySel = row.querySelector('.sys-key');
    const valEl  = row.querySelector('.sys-value');
    const key    = keySel.value.trim();
    const value  = valEl.value.trim();

    // Clear previous errors
    keySel.classList.remove('input-error');
    valEl.classList.remove('input-error');

    if (!key) {
      keySel.classList.add('input-error');
      showToast('Please select a key for every system-defined metadata row.', 'error');
      keySel.focus();
      valid = false;
      return;
    }
    if (!value) {
      valEl.classList.add('input-error');
      showToast(`Value for system metadata key "${key}" cannot be empty.`, 'error');
      valEl.focus();
      valid = false;
      return;
    }
    entries.push({ key, value });
  });

  return valid ? entries : null;
}

/* ── Collect & validate user metadata ── */
function collectUserMeta() {
  const rows    = document.querySelectorAll('#userMetaRows .meta-row');
  const entries = [];
  const seen    = new Set();
  let valid     = true;

  rows.forEach(row => {
    const keyEl  = row.querySelector('.usr-key');
    const valEl  = row.querySelector('.usr-value');
    const key    = keyEl.value.trim();
    const value  = valEl.value.trim();

    // Clear previous errors
    keyEl.classList.remove('input-error');
    valEl.classList.remove('input-error');

    if (!key && !value) return; // skip completely empty rows silently

    if (!key) {
      keyEl.classList.add('input-error');
      showToast('User-defined metadata key cannot be empty.', 'error');
      keyEl.focus();
      valid = false;
      return;
    }
    if (!/^[a-zA-Z0-9\-_]{1,128}$/.test(key)) {
      keyEl.classList.add('input-error');
      showToast(`Key "${key}" is invalid. Use only letters, numbers, hyphens, and underscores.`, 'error');
      keyEl.focus();
      valid = false;
      return;
    }
    if (!value) {
      valEl.classList.add('input-error');
      showToast(`Value for user metadata key "${key}" cannot be empty.`, 'error');
      valEl.focus();
      valid = false;
      return;
    }
    if (seen.has(key.toLowerCase())) {
      keyEl.classList.add('input-error');
      showToast(`Duplicate user metadata key "${key}". Each key must be unique.`, 'error');
      keyEl.focus();
      valid = false;
      return;
    }
    seen.add(key.toLowerCase());
    entries.push({ key, value });
  });

  return valid ? entries : null;
}


function openFolderModal() {
  document.getElementById('folderNameInput').value = '';
  document.getElementById('folderModal').style.display = 'flex';
  setTimeout(() => document.getElementById('folderNameInput').focus(), 100);
}

function closeFolderModal() {
  document.getElementById('folderModal').style.display = 'none';
}

async function createFolder() {
  const name = document.getElementById('folderNameInput').value.trim();
  if (!name) { showToast('Enter a folder name', 'error'); return; }

  const form = new FormData();
  form.append('bucket', state.bucket);
  form.append('prefix', state.prefix);
  form.append('folder_name', name);
  form.append('csrf_token', csrf());

  showLoading('Creating folder...');
  try {
    const res = await fetch('/api/s3/folder/create', { method: 'POST', body: form });
    const data = await res.json();
    if (res.ok) {
      showToast(data.message, 'success');
      closeFolderModal();
      loadObjects();
    } else {
      showToast(data.detail || 'Create failed', 'error');
    }
  } catch (e) {
    showToast('Create folder failed', 'error');
  } finally {
    hideLoading();
  }
}

// ── Rename ─────────────────────────────────────────────────────────────────
function openRenameModal(key, currentName) {
  document.getElementById('renameOldKey').value = key;
  document.getElementById('renameNewName').value = currentName;
  document.getElementById('renameModal').style.display = 'flex';
  setTimeout(() => {
    const input = document.getElementById('renameNewName');
    input.focus();
    input.select();
  }, 100);
}

function closeRenameModal() {
  document.getElementById('renameModal').style.display = 'none';
}

async function renameObject() {
  const oldKey = document.getElementById('renameOldKey').value;
  const newName = document.getElementById('renameNewName').value.trim();
  if (!newName) { showToast('Enter a new name', 'error'); return; }

  const form = new FormData();
  form.append('bucket', state.bucket);
  form.append('old_key', oldKey);
  form.append('new_name', newName);
  form.append('csrf_token', csrf());

  showLoading('Renaming...');
  try {
    const res = await fetch('/api/s3/rename', { method: 'POST', body: form });
    const data = await res.json();
    if (res.ok) {
      showToast(data.message, 'success');
      closeRenameModal();
      loadObjects();
    } else {
      showToast(data.detail || 'Rename failed', 'error');
    }
  } catch (e) {
    showToast('Rename failed', 'error');
  } finally {
    hideLoading();
  }
}

// ── Object Info / Metadata Viewer ─────────────────────────────────────────
async function openInfoModal(key) {
  document.getElementById('infoModal').style.display = 'flex';
  const body = document.getElementById('infoModalBody');
  body.innerHTML = `
    <div class="loading-content" style="padding:24px 0;">
      <svg class="spin" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 12a9 9 0 1 1-6.219-8.56"/></svg>
      <span>Loading details...</span>
    </div>`;

  try {
    const res = await fetch(
      `/api/s3/metadata?bucket=${encodeURIComponent(state.bucket)}&key=${encodeURIComponent(key)}`
    );
    const data = await res.json();
    if (!res.ok) {
      body.innerHTML = `<div class="info-empty">${escHtml(data.detail || 'Failed to load metadata')}</div>`;
      return;
    }
    renderInfoModal(data);
  } catch (e) {
    body.innerHTML = `<div class="info-empty">Failed to load object details</div>`;
  }
}

function renderInfoModal(data) {
  const body = document.getElementById('infoModalBody');
  const customMeta = data.metadata || {};
  const metaKeys = Object.keys(customMeta);

  let html = `
    <div class="info-row"><span class="info-row-label">Key</span><span class="info-row-value">${escHtml(data.key)}</span></div>
    <div class="info-row"><span class="info-row-label">Size</span><span class="info-row-value">${escHtml(data.size_human)}</span></div>
    <div class="info-row"><span class="info-row-label">Content Type</span><span class="info-row-value">${escHtml(data.content_type)}</span></div>
    <div class="info-row"><span class="info-row-label">Storage Class</span><span class="info-row-value">${escHtml(data.storage_class)}</span></div>
    <div class="info-row"><span class="info-row-label">ETag</span><span class="info-row-value">${escHtml(data.etag)}</span></div>
  `;

  html += `<div class="info-section-title">Custom Metadata</div>`;
  if (!metaKeys.length) {
    html += `<div class="info-empty">No custom metadata was set for this object.</div>`;
  } else {
    metaKeys.forEach(k => {
      html += `<div class="info-row"><span class="info-row-label">${escHtml(k)}</span><span class="info-row-value">${escHtml(customMeta[k])}</span></div>`;
    });
  }

  body.innerHTML = html;
}

function closeInfoModal() {
  document.getElementById('infoModal').style.display = 'none';
}


async function openPresignModal(key) {
  showLoading('Generating URL...');
  try {
    const res = await fetch(
      `/api/s3/presign?bucket=${encodeURIComponent(state.bucket)}&key=${encodeURIComponent(key)}&expiry=3600`
    );
    const data = await res.json();
    if (res.ok) {
      document.getElementById('presignUrlInput').value = data.url;
      document.getElementById('presignOpenLink').href = data.url;
      document.getElementById('presignModal').style.display = 'flex';
    } else {
      showToast(data.detail || 'Failed to generate URL', 'error');
    }
  } catch (e) {
    showToast('Failed to generate URL', 'error');
  } finally {
    hideLoading();
  }
}

function closePresignModal() {
  document.getElementById('presignModal').style.display = 'none';
}

function copyPresignUrl() {
  const url = document.getElementById('presignUrlInput').value;
  navigator.clipboard.writeText(url).then(() => showToast('URL copied to clipboard', 'success'));
}

// ── Context Menu ───────────────────────────────────────────────────────────
function showContextMenu(event, itemJson) {
  event.preventDefault();
  const item = JSON.parse(itemJson);
  state.ctxTarget = item;

  const menu = document.getElementById('contextMenu');
  menu.style.display = 'block';

  const x = Math.min(event.clientX, window.innerWidth - 200);
  const y = Math.min(event.clientY, window.innerHeight - 220);
  menu.style.left = x + 'px';
  menu.style.top  = y + 'px';

  // buttons: [0]=download [1]=rename [2]=presign [3]=viewInfo [4]=editMeta [divider] [5]=delete
  const isFolder  = item.type === 'folder';
  const btns      = menu.querySelectorAll('button');
  btns[0].style.display = isFolder ? 'none' : 'flex'; // download
  btns[2].style.display = isFolder ? 'none' : 'flex'; // copy url
  btns[3].style.display = isFolder ? 'none' : 'flex'; // view info
  btns[4].style.display = isFolder ? 'none' : 'flex'; // edit metadata
}

function hideContextMenu() {
  document.getElementById('contextMenu').style.display = 'none';
}

function ctxDownload() {
  if (state.ctxTarget) downloadFile(state.ctxTarget.key);
  hideContextMenu();
}

function ctxRename() {
  if (state.ctxTarget) openRenameModal(state.ctxTarget.key, state.ctxTarget.name);
  hideContextMenu();
}

function ctxPresign() {
  if (state.ctxTarget) openPresignModal(state.ctxTarget.key);
  hideContextMenu();
}

function ctxViewInfo() {
  if (state.ctxTarget) openInfoModal(state.ctxTarget.key);
  hideContextMenu();
}

function ctxEditMetadata() {
  if (state.ctxTarget) openEditMetaModal(state.ctxTarget.key);
  hideContextMenu();
}

function ctxDelete() {
  if (state.ctxTarget) confirmDelete(state.ctxTarget.key, state.ctxTarget.type === 'folder');
  hideContextMenu();
}

// ── Confirm Modal ──────────────────────────────────────────────────────────
let confirmCallback = null;

function showConfirmModal(title, message, onConfirm) {
  document.getElementById('confirmTitle').textContent = title;
  document.getElementById('confirmMessage').textContent = message;
  confirmCallback = onConfirm;
  document.getElementById('confirmModal').style.display = 'flex';
  document.getElementById('confirmOkBtn').onclick = () => {
    closeConfirmModal();
    if (confirmCallback) confirmCallback();
  };
}

function closeConfirmModal() {
  document.getElementById('confirmModal').style.display = 'none';
  confirmCallback = null;
}

// ── Loading ────────────────────────────────────────────────────────────────
function showLoading(text = 'Loading...') {
  document.getElementById('loadingText').textContent = text;
  document.getElementById('loadingOverlay').style.display = 'flex';
}

function hideLoading() {
  document.getElementById('loadingOverlay').style.display = 'none';
}

// ── Toast ──────────────────────────────────────────────────────────────────
function showToast(message, type = 'info') {
  const icons = {
    success: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="20 6 9 17 4 12"/></svg>',
    error: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>',
    info: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>',
  };

  const toast = document.createElement('div');
  toast.className = `toast ${type} fade-in`;
  toast.innerHTML = `<span class="toast-icon">${icons[type] || icons.info}</span><span class="toast-text">${escHtml(message)}</span>`;
  document.getElementById('toastContainer').appendChild(toast);

  setTimeout(() => {
    toast.style.animation = 'slideOut 0.3s ease forwards';
    setTimeout(() => toast.remove(), 300);
  }, 4000);
}

// ── Helpers ────────────────────────────────────────────────────────────────
function escHtml(str) {
  if (!str) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function escAttr(str) {
  // Same escaping as escHtml; kept distinct for readability at call sites
  // where the value is inserted into an HTML attribute (e.g. input value=).
  return escHtml(str);
}

function humanSize(bytes) {
  if (!bytes) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  const i = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), 4);
  return (bytes / Math.pow(1024, i)).toFixed(1) + ' ' + units[i];
}

function guessTypeFromName(name) {
  const ext = name.toLowerCase().split('.').pop();
  const map = {
    images: ['jpg','jpeg','png','gif','webp','svg','bmp'],
    video: ['mp4','avi','mov','mkv','webm'],
    audio: ['mp3','wav','flac','aac','ogg'],
    document: ['pdf','doc','docx','xls','xlsx','ppt','pptx'],
    text: ['txt','md','csv','log','xml','html'],
    code: ['py','js','ts','java','cpp','c','go','rs','json','yaml','yml'],
    archive: ['zip','tar','gz','bz2','7z','rar'],
  };
  for (const [type, exts] of Object.entries(map)) {
    if (exts.includes(ext)) return type;
  }
  return 'file';
}

// ══ Edit Metadata ══════════════════════════════════════════════════════════

/** Tracks which object key is currently being edited. */
let emCurrentKey   = null;
let emPanelIsOpen  = true;

/**
 * Open the Edit Metadata modal for a file object.
 * Fetches current metadata from the backend, then populates all rows.
 */
async function openEditMetaModal(key) {
  // Always reset to clean state first — this is the guard against the
  // "still buffering" bug where a previous successful save left buttons
  // in the em-disabled state and the spinner still showing.
  closeEditMetaModal();

  emCurrentKey = key;

  // Show modal in loading state
  const modal = document.getElementById('editMetaModal');
  modal.style.display = 'flex';
  document.getElementById('emLoadingState').style.display = 'flex';
  document.getElementById('emPropsCard').style.display    = 'none';
  document.getElementById('emMetaCard').style.display     = 'none';
  document.getElementById('emSaveBtn').style.display      = 'none';
  document.getElementById('emObjectName').textContent     = key.split('/').pop();

  try {
    const res = await fetch(
      `/api/s3/metadata?bucket=${encodeURIComponent(state.bucket)}&key=${encodeURIComponent(key)}`
    );
    if (!res.ok) {
      const err = await res.json();
      document.getElementById('emLoadingState').innerHTML =
        `<span style="color:var(--error);padding:24px 0;">${escHtml(err.detail || 'Failed to load metadata')}</span>`;
      return;
    }
    const data = await res.json();
    emPopulate(data);
  } catch (e) {
    document.getElementById('emLoadingState').innerHTML =
      `<span style="color:var(--error);padding:24px 0;">Network error — could not load metadata.</span>`;
  }
}

/** Populate modal with object properties and pre-fill metadata rows. */
function emPopulate(data) {
  // Object Properties
  document.getElementById('emPropName').textContent        = data.name        || data.key;
  document.getElementById('emPropBucket').textContent      = data.bucket      || state.bucket;
  document.getElementById('emPropSize').textContent        = data.size_human  || '-';
  document.getElementById('emPropModified').textContent    = data.last_modified_human || '-';
  document.getElementById('emPropStorageClass').textContent= data.storage_class || 'STANDARD';
  document.getElementById('emPropEtag').textContent        = data.etag        || '-';

  // System-defined rows (pre-populated from current metadata)
  (data.system_metadata || []).forEach(entry => {
    emAddSystemRow(entry.key, entry.value);
  });

  // User-defined rows (pre-populated from current metadata)
  (data.user_metadata || []).forEach(entry => {
    emAddUserRow(entry.key, entry.value);
  });

  // Switch to populated state
  document.getElementById('emLoadingState').style.display = 'none';
  document.getElementById('emPropsCard').style.display    = 'block';
  document.getElementById('emMetaCard').style.display     = 'block';
  document.getElementById('emSaveBtn').style.display      = 'flex';
}

function closeEditMetaModal() {
  document.getElementById('editMetaModal').style.display = 'none';
  emCurrentKey   = null;
  emPanelIsOpen  = true;

  // ── Re-enable every control that emExecuteSave may have disabled ──────
  // This is the root cause of the "still buffering after save" bug:
  // em-disabled was added during save but never removed on the success path
  // because closeEditMetaModal was called before re-enabling.
  const btnIds = ['emSaveBtn', 'emCancelBtn', 'emCloseBtn', 'emAddSysBtn', 'emAddUsrBtn'];
  btnIds.forEach(id => {
    const el = document.getElementById(id);
    if (el) el.classList.remove('em-disabled');
  });

  // Restore the save button to its original label (in case spinner is showing)
  const saveBtn = document.getElementById('emSaveBtn');
  if (saveBtn) {
    saveBtn.innerHTML = `
      <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/>
        <polyline points="17 21 17 13 7 13 7 21"/>
        <polyline points="7 3 7 8 15 8"/>
      </svg>
      Save Changes`;
    saveBtn.style.display = 'none'; // hidden until emPopulate shows it
  }

  // Clear metadata rows and reset panel visibility
  document.getElementById('emSystemRows').innerHTML = '';
  document.getElementById('emUserRows').innerHTML   = '';
  emSyncEmpty('sys');
  emSyncEmpty('usr');

  // Reset the collapse button
  const collapseBtn = document.getElementById('emCollapseBtn');
  if (collapseBtn) collapseBtn.classList.remove('collapsed');
  const panel = document.getElementById('emMetaPanel');
  if (panel) panel.style.display = '';

  // Clear pending save data
  document._emPendingSys  = null;
  document._emPendingUser = null;
}

function toggleEmPanel() {
  emPanelIsOpen = !emPanelIsOpen;
  const panel = document.getElementById('emMetaPanel');
  const btn   = document.getElementById('emCollapseBtn');
  panel.style.display = emPanelIsOpen ? '' : 'none';
  btn.classList.toggle('collapsed', !emPanelIsOpen);
}

function emSyncEmpty(section) {
  const rowsId  = section === 'sys' ? 'emSystemRows' : 'emUserRows';
  const emptyId = section === 'sys' ? 'emSysEmpty'   : 'emUsrEmpty';
  const hasRows = document.getElementById(rowsId).querySelectorAll('.meta-row').length > 0;
  document.getElementById(emptyId).style.display = hasRows ? 'none' : 'block';
}

/** Add a system-defined row to the Edit Metadata modal, optionally pre-filled. */
function emAddSystemRow(preKey = '', preValue = '') {
  const container = document.getElementById('emSystemRows');

  // Check for duplicate key before adding
  if (preKey) {
    const existing = [...container.querySelectorAll('.sys-key')].some(s => s.value === preKey);
    if (existing) return; // silently skip duplicate (happens on re-populate)
  }

  const row = document.createElement('div');
  row.className = 'meta-row sys-row';

  const keyOpts = SYSTEM_KEYS.map(k =>
    `<option value="${k}" ${k === preKey ? 'selected' : ''}>${k}</option>`
  ).join('');

  const valueDisabled = preKey ? '' : 'disabled';
  const placeholder   = preKey ? (SYSTEM_KEY_HINTS[preKey] || 'Enter value…') : 'Select a key first';

  row.innerHTML = `
    <select class="sys-key" onchange="emOnSysKeyChange(this)">
      <option value="" disabled ${!preKey ? 'selected' : ''}>Select key…</option>
      ${keyOpts}
    </select>
    <input type="text" class="sys-value"
           placeholder="${escAttr(placeholder)}"
           value="${escAttr(preValue)}"
           ${valueDisabled} maxlength="1024" />
    <button type="button" class="meta-row-remove"
            onclick="emRemoveRow(this,'sys')" title="Remove">${REMOVE_SVG}</button>`;

  container.appendChild(row);
  emSyncEmpty('sys');
}

function emOnSysKeyChange(sel) {
  // Prevent duplicate system keys
  const container  = document.getElementById('emSystemRows');
  const allSelects = [...container.querySelectorAll('.sys-key')].filter(s => s !== sel);
  const chosen     = sel.value;
  if (allSelects.some(s => s.value === chosen)) {
    showToast(`"${chosen}" is already set. Remove the existing row first.`, 'error');
    sel.value = '';
    return;
  }
  const row      = sel.closest('.meta-row');
  const valInput = row.querySelector('.sys-value');
  valInput.disabled    = false;
  valInput.placeholder = SYSTEM_KEY_HINTS[chosen] || 'Enter value…';
  sel.classList.remove('input-error');
  valInput.classList.remove('input-error');
  valInput.focus();
}

/** Add a user-defined row to the Edit Metadata modal, optionally pre-filled. */
function emAddUserRow(preKey = '', preValue = '') {
  const container = document.getElementById('emUserRows');
  const row = document.createElement('div');
  row.className = 'meta-row usr-row';
  row.innerHTML = `
    <input type="text" class="usr-key"
           placeholder="Key (e.g. department)"
           value="${escAttr(preKey)}"
           maxlength="128" />
    <input type="text" class="usr-value"
           placeholder="Value (e.g. finance)"
           value="${escAttr(preValue)}"
           maxlength="1024" />
    <button type="button" class="meta-row-remove"
            onclick="emRemoveRow(this,'usr')" title="Remove">${REMOVE_SVG}</button>`;
  container.appendChild(row);
  emSyncEmpty('usr');
}

function emRemoveRow(btn, section) {
  btn.closest('.meta-row').remove();
  emSyncEmpty(section);
}

/** Collect + validate system rows; return [{key,value}] array or null on error. */
function emCollectSystemMeta() {
  const rows    = document.querySelectorAll('#emSystemRows .meta-row');
  const entries = [];
  const seen    = new Set();
  let valid     = true;

  rows.forEach(row => {
    const keySel = row.querySelector('.sys-key');
    const valEl  = row.querySelector('.sys-value');
    const key    = keySel.value.trim();
    const value  = valEl.value.trim();

    keySel.classList.remove('input-error');
    valEl.classList.remove('input-error');

    if (!key) {
      keySel.classList.add('input-error');
      showToast('Please select a key for every system-defined metadata row.', 'error');
      keySel.focus(); valid = false; return;
    }
    if (seen.has(key)) {
      keySel.classList.add('input-error');
      showToast(`Duplicate system key "${key}". Each key may only appear once.`, 'error');
      keySel.focus(); valid = false; return;
    }
    if (!value) {
      valEl.classList.add('input-error');
      showToast(`Value for "${key}" cannot be empty.`, 'error');
      valEl.focus(); valid = false; return;
    }

    // Validate Expires format if set
    if (key === 'Expires' && isNaN(Date.parse(value))) {
      valEl.classList.add('input-error');
      showToast('Expires must be a valid date/time (e.g. Thu, 01 Jan 2026 00:00:00 GMT).', 'error');
      valEl.focus(); valid = false; return;
    }

    seen.add(key);
    entries.push({ key, value });
  });

  return valid ? entries : null;
}

/** Collect + validate user-defined rows; return [{key,value}] array or null on error. */
function emCollectUserMeta() {
  const rows    = document.querySelectorAll('#emUserRows .meta-row');
  const entries = [];
  const seen    = new Set();
  let valid     = true;
  let totalSize = 0;

  rows.forEach(row => {
    const keyEl = row.querySelector('.usr-key');
    const valEl = row.querySelector('.usr-value');
    const key   = keyEl.value.trim();
    const value = valEl.value.trim();

    keyEl.classList.remove('input-error');
    valEl.classList.remove('input-error');

    if (!key && !value) return; // skip empty rows silently

    if (!key) {
      keyEl.classList.add('input-error');
      showToast('User-defined metadata key cannot be empty.', 'error');
      keyEl.focus(); valid = false; return;
    }
    if (!/^[a-zA-Z0-9\-_]{1,128}$/.test(key)) {
      keyEl.classList.add('input-error');
      showToast(`Key "${key}": use only letters, numbers, hyphens, and underscores (max 128 chars).`, 'error');
      keyEl.focus(); valid = false; return;
    }
    if (seen.has(key.toLowerCase())) {
      keyEl.classList.add('input-error');
      showToast(`Duplicate user metadata key "${key}".`, 'error');
      keyEl.focus(); valid = false; return;
    }
    if (!value) {
      valEl.classList.add('input-error');
      showToast(`Value for "${key}" cannot be empty.`, 'error');
      valEl.focus(); valid = false; return;
    }
    totalSize += key.length + value.length;
    if (totalSize > 2048) {
      valEl.classList.add('input-error');
      showToast('Combined user metadata exceeds the 2 KB S3 limit.', 'error');
      valid = false; return;
    }

    seen.add(key.toLowerCase());
    entries.push({ key, value });
  });

  return valid ? entries : null;
}

/** Show confirmation modal before saving. */
function emRequestSave() {
  const sysEntries  = emCollectSystemMeta();
  const userEntries = emCollectUserMeta();
  if (sysEntries === null || userEntries === null) return; // toast already shown

  // Store validated entries for when confirm is clicked
  document._emPendingSys  = sysEntries;
  document._emPendingUser = userEntries;

  document.getElementById('emConfirmModal').style.display = 'flex';
  document.getElementById('emConfirmOkBtn').onclick = emExecuteSave;
}

function closeEmConfirm() {
  document.getElementById('emConfirmModal').style.display = 'none';
}

/** Actually call the PUT /api/s3/metadata endpoint. */
async function emExecuteSave() {
  closeEmConfirm();

  const sysEntries  = document._emPendingSys  || [];
  const userEntries = document._emPendingUser || [];

  // Disable all controls while saving
  const saveBtn   = document.getElementById('emSaveBtn');
  const cancelBtn = document.getElementById('emCancelBtn');
  const closeBtn  = document.getElementById('emCloseBtn');
  const addSysBtn = document.getElementById('emAddSysBtn');
  const addUsrBtn = document.getElementById('emAddUsrBtn');
  [saveBtn, cancelBtn, closeBtn, addSysBtn, addUsrBtn].forEach(b => b && b.classList.add('em-disabled'));

  // Show spinner on save button
  const origSaveBtnHTML = saveBtn.innerHTML;
  saveBtn.innerHTML = `<svg class="spin" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 12a9 9 0 1 1-6.219-8.56"/></svg> Updating metadata…`;

  const formData = new FormData();
  formData.append('bucket',          state.bucket);
  formData.append('key',             emCurrentKey);
  formData.append('csrf_token',      csrf());
  formData.append('system_metadata', sysEntries.length  ? JSON.stringify(sysEntries)  : '');
  formData.append('user_metadata',   userEntries.length ? JSON.stringify(userEntries) : '');

  try {
    const res  = await fetch('/api/s3/metadata', { method: 'PUT', body: formData });
    const data = await res.json();

    if (res.ok) {
      showToast('✅ Metadata updated successfully.', 'success');
      closeEditMetaModal();
      // Refresh the file list so the updated metadata is reflected in View Info
      loadObjects();
    } else {
      // Show friendly error, re-enable controls
      showToast(data.detail || 'Failed to update metadata.', 'error');
      [saveBtn, cancelBtn, closeBtn, addSysBtn, addUsrBtn].forEach(b => b && b.classList.remove('em-disabled'));
      saveBtn.innerHTML = origSaveBtnHTML;
    }
  } catch (e) {
    showToast('Network error — metadata update failed.', 'error');
    [saveBtn, cancelBtn, closeBtn, addSysBtn, addUsrBtn].forEach(b => b && b.classList.remove('em-disabled'));
    saveBtn.innerHTML = origSaveBtnHTML;
  }
}