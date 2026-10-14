/**
 * ShareNow — Frontend Application Logic
 * Real-time File, Image & Text Sharing
 */

(function () {
  'use strict';

  // -----------------------------------------------------------
  // State
  // -----------------------------------------------------------
  const urlParams = new URLSearchParams(window.location.search);
  let currentRoom = (urlParams.get('room') || 'global').toLowerCase().trim();
  let roomPin = sessionStorage.getItem(`room_pin_${currentRoom}`) || '';
  let items = [];
  let activeFilter = 'all';
  let searchQuery = '';
  let soundEnabled = localStorage.getItem('sharenow_sound') !== 'false';
  let ws = null;
  let qrcodeInstance = null;
  let networkInfo = null;

  // -----------------------------------------------------------
  // DOM Elements
  // -----------------------------------------------------------
  const currentRoomLabel = document.getElementById('currentRoomLabel');
  const roomLockIcon = document.getElementById('roomLockIcon');
  const roomBtn = document.getElementById('roomBtn');
  const peersCount = document.getElementById('peersCount');
  const storageBar = document.getElementById('storageBar');
  const storageLabel = document.getElementById('storageLabel');
  const soundToggleBtn = document.getElementById('soundToggleBtn');
  const soundIcon = document.getElementById('soundIcon');
  const themeToggleBtn = document.getElementById('themeToggleBtn');
  const themeIcon = document.getElementById('themeIcon');
  const messageInput = document.getElementById('messageInput');
  const dropZone = document.getElementById('dropZone');
  const fileInput = document.getElementById('fileInput');
  const cameraInput = document.getElementById('cameraInput');
  const snapCameraBtn = document.getElementById('snapCameraBtn');
  const burnCheck = document.getElementById('burnCheck');
  const sendBtn = document.getElementById('sendBtn');
  const uploadProgressContainer = document.getElementById('uploadProgressContainer');
  const uploadProgressBar = document.getElementById('uploadProgressBar');
  const uploadPercentText = document.getElementById('uploadPercentText');
  const uploadStatusText = document.getElementById('uploadStatusText');
  const searchInput = document.getElementById('searchInput');
  const filterTabs = document.querySelectorAll('.filter-tab');
  const itemsGrid = document.getElementById('itemsGrid');
  const emptyState = document.getElementById('emptyState');
  const dragOverlay = document.getElementById('dragOverlay');
  const toastContainer = document.getElementById('toastContainer');

  // Modals
  const qrModal = document.getElementById('qrModal');
  const qrModalBtn = document.getElementById('qrModalBtn');
  const closeQrModal = document.getElementById('closeQrModal');
  const qrcodeContainer = document.getElementById('qrcodeContainer');
  const qrUrlLabel = document.getElementById('qrUrlLabel');
  const copyQrLinkBtn = document.getElementById('copyQrLinkBtn');

  const roomModal = document.getElementById('roomModal');
  const closeRoomModal = document.getElementById('closeRoomModal');
  const modalRoomInput = document.getElementById('modalRoomInput');
  const modalPinInput = document.getElementById('modalPinInput');
  const saveRoomBtn = document.getElementById('saveRoomBtn');
  const setPinBtn = document.getElementById('setPinBtn');

  const lightboxModal = document.getElementById('lightboxModal');
  const closeLightbox = document.getElementById('closeLightbox');
  const lightboxImg = document.getElementById('lightboxImg');
  const lightboxDownloadBtn = document.getElementById('lightboxDownloadBtn');

  // -----------------------------------------------------------
  // Audio Synthesizer (Zero External Dependencies)
  // -----------------------------------------------------------
  function playNotificationChime() {
    if (!soundEnabled) return;
    try {
      const AudioContext = window.AudioContext || window.webkitAudioContext;
      if (!AudioContext) return;
      const ctx = new AudioContext();

      const now = ctx.currentTime;
      const osc1 = ctx.createOscillator();
      const osc2 = ctx.createOscillator();
      const gain = ctx.createGain();

      osc1.type = 'sine';
      osc1.frequency.setValueAtTime(587.33, now); // D5
      osc1.frequency.exponentialRampToValueAtTime(880, now + 0.12); // A5

      osc2.type = 'triangle';
      osc2.frequency.setValueAtTime(880, now + 0.12);

      gain.gain.setValueAtTime(0.15, now);
      gain.gain.exponentialRampToValueAtTime(0.001, now + 0.35);

      osc1.connect(gain);
      osc2.connect(gain);
      gain.connect(ctx.destination);

      osc1.start(now);
      osc2.start(now + 0.12);
      osc1.stop(now + 0.35);
      osc2.stop(now + 0.35);
    } catch (e) {
      // Audio autoplay policy catch
    }
  }

  // -----------------------------------------------------------
  // Toast Notifications
  // -----------------------------------------------------------
  function showToast(message, type = 'info') {
    const toast = document.createElement('div');
    const color = type === 'error' ? 'bg-red-950/90 border-red-800 text-red-200' :
                  type === 'success' ? 'bg-emerald-950/90 border-emerald-800 text-emerald-200' :
                  'bg-slate-900/95 border-slate-700 text-slate-200';

    toast.className = `p-3 rounded-xl border shadow-xl text-xs flex items-center space-x-2 backdrop-blur-md animate-slide pointer-events-auto ${color}`;
    toast.innerHTML = `<span>${escapeHtml(message)}</span>`;
    toastContainer.appendChild(toast);

    setTimeout(() => {
      toast.style.opacity = '0';
      toast.style.transform = 'translateY(10px)';
      toast.style.transition = 'all 0.3s ease';
      setTimeout(() => toast.remove(), 300);
    }, 3500);
  }

  // -----------------------------------------------------------
  // WebSocket Hub
  // -----------------------------------------------------------
  function initWebSocket() {
    if (ws) {
      try { ws.close(); } catch (e) {}
    }

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/ws/${encodeURIComponent(currentRoom)}`;
    
    ws = new WebSocket(wsUrl);

    let fallbackPollInterval = null;

    ws.onopen = () => {
      console.log(`[WebSocket] Connected to room: ${currentRoom}`);
      if (fallbackPollInterval) {
        clearInterval(fallbackPollInterval);
        fallbackPollInterval = null;
      }
    };

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        handleWsEvent(data);
      } catch (err) {
        console.error('[WebSocket] Parse error:', err);
      }
    };

    ws.onclose = () => {
      // Reconnect attempt and enable polling fallback for serverless environments (e.g. Vercel)
      if (!fallbackPollInterval) {
        fallbackPollInterval = setInterval(loadItems, 4000);
      }
      setTimeout(initWebSocket, 5000);
    };

    ws.onerror = () => {
      if (!fallbackPollInterval) {
        fallbackPollInterval = setInterval(loadItems, 4000);
      }
      try { ws.close(); } catch (e) {}
    };
  }


  function handleWsEvent(data) {
    switch (data.event) {
      case 'new_item':
        // Avoid duplicate
        if (!items.some(i => i.id === data.item.id)) {
          items.unshift(data.item);
          renderItems();
          playNotificationChime();
          showToast(`New ${data.item.type} shared: ${data.item.content.substring(0, 30)}`, 'success');
        }
        break;

      case 'delete_item':
        items = items.filter(i => i.id !== data.id);
        const card = document.getElementById(`item-${data.id}`);
        if (card) {
          card.style.opacity = '0';
          card.style.transform = 'scale(0.95)';
          card.style.transition = 'all 0.3s ease';
          setTimeout(() => {
            card.remove();
            checkEmptyState();
          }, 300);
        }
        break;

      case 'storage_update':
        updateStorageUI(data.storage);
        break;

      case 'client_count':
        peersCount.textContent = `${data.count} online`;
        break;
    }
  }

  // -----------------------------------------------------------
  // Data Fetching
  // -----------------------------------------------------------
  async function loadItems() {
    try {
      let url = `/api/items?room=${encodeURIComponent(currentRoom)}`;
      if (roomPin) url += `&room_pin=${encodeURIComponent(roomPin)}`;

      const res = await fetch(url);
      if (res.status === 401) {
        promptRoomPin();
        return;
      }
      const data = await res.json();
      items = data.items || [];
      if (data.is_protected) {
        roomLockIcon.classList.remove('hidden');
      } else {
        roomLockIcon.classList.add('hidden');
      }
      if (data.storage) {
        updateStorageUI(data.storage);
      }
      renderItems();
    } catch (err) {
      console.error('Error fetching items:', err);
    }
  }

  async function fetchNetworkInfo() {
    try {
      const res = await fetch('/api/network-info');
      networkInfo = await res.json();
    } catch (e) {
      console.error('Could not fetch network info:', e);
    }
  }

  // -----------------------------------------------------------
  // Storage UI Update
  // -----------------------------------------------------------
  function updateStorageUI(stats) {
    if (!stats) return;
    const pct = Math.min(100, stats.percentage || 0);
    storageBar.style.width = `${pct}%`;
    storageLabel.textContent = `${stats.used_mb} / ${stats.max_mb} MB`;

    if (pct > 85) {
      storageBar.className = 'bg-red-500 h-2 rounded-full transition-all duration-500';
    } else if (pct > 65) {
      storageBar.className = 'bg-amber-500 h-2 rounded-full transition-all duration-500';
    } else {
      storageBar.className = 'bg-indigo-500 h-2 rounded-full transition-all duration-500';
    }
  }

  // -----------------------------------------------------------
  // Live Expiration Countdowns
  // -----------------------------------------------------------
  function updateCountdowns() {
    const now = Date.now() / 1000;
    const badges = document.querySelectorAll('.expiration-badge');

    badges.forEach(badge => {
      const expiresAt = parseFloat(badge.dataset.expires);
      const remaining = Math.max(0, Math.floor(expiresAt - now));

      if (remaining <= 0) {
        const itemId = badge.dataset.id;
        const card = document.getElementById(`item-${itemId}`);
        if (card) {
          card.remove();
          items = items.filter(i => i.id !== itemId);
          checkEmptyState();
        }
        return;
      }

      const mins = Math.floor(remaining / 60);
      const secs = remaining % 60;
      badge.textContent = `⏱ ${mins}m ${secs < 10 ? '0' : ''}${secs}s`;

      if (remaining < 600) { // Under 10 mins
        badge.className = 'expiration-badge px-2 py-0.5 rounded-full text-[10px] font-mono font-medium bg-red-950/80 border border-red-800/80 text-red-400 animate-pulse';
      }
    });
  }

  setInterval(updateCountdowns, 1000);

  // -----------------------------------------------------------
  // Rendering Items
  // -----------------------------------------------------------
  function renderItems() {
    itemsGrid.innerHTML = '';
    const now = Date.now() / 1000;

    const filtered = items.filter(item => {
      if (item.expires_at <= now) return false;
      if (activeFilter !== 'all' && item.type !== activeFilter) return false;
      if (searchQuery) {
        const q = searchQuery.toLowerCase();
        const contentMatch = (item.content || '').toLowerCase().includes(q);
        const mimeMatch = (item.mime_type || '').toLowerCase().includes(q);
        if (!contentMatch && !mimeMatch) return false;
      }
      return true;
    });

    if (filtered.length === 0) {
      emptyState.classList.remove('hidden');
    } else {
      emptyState.classList.add('hidden');
      filtered.forEach(item => {
        const card = createItemCard(item);
        itemsGrid.appendChild(card);
      });
      lucide.createIcons();
    }
  }

  function checkEmptyState() {
    if (itemsGrid.children.length === 0) {
      emptyState.classList.remove('hidden');
    } else {
      emptyState.classList.add('hidden');
    }
  }

  function createItemCard(item) {
    const card = document.createElement('div');
    card.id = `item-${item.id}`;
    card.className = 'item-card bg-slate-900/80 border border-slate-800 rounded-2xl p-4 shadow-lg flex flex-col justify-between backdrop-blur-sm relative overflow-hidden group animate-fade';

    const now = Date.now() / 1000;
    const remaining = Math.max(0, Math.floor(item.expires_at - now));
    const mins = Math.floor(remaining / 60);
    const secs = remaining % 60;
    const isUrgent = remaining < 600;

    const badgeClass = isUrgent
      ? 'bg-red-950/80 border-red-800/80 text-red-400 animate-pulse'
      : 'bg-indigo-950/60 border-indigo-800/50 text-indigo-300';

    const burnBadge = item.burn_after_reading
      ? `<span class="px-2 py-0.5 rounded-full text-[10px] font-medium bg-amber-950/80 border border-amber-700/60 text-amber-300 flex items-center gap-1">🔥 1-Time</span>`
      : '';

    let contentHtml = '';

    if (item.type === 'image') {
      const imgUrl = `/uploads/${encodeURIComponent(item.file_path)}${roomPin ? `?room_pin=${encodeURIComponent(roomPin)}` : ''}`;
      contentHtml = `
        <div class="rounded-xl overflow-hidden bg-slate-950 border border-slate-800 relative cursor-pointer my-2.5 max-h-48 flex items-center justify-center group/img" onclick="window.ShareNow.openLightbox('${imgUrl}', '${escapeHtml(item.content)}')">
          <img src="${imgUrl}" alt="${escapeHtml(item.content)}" class="w-full h-44 object-cover group-hover/img:scale-105 transition duration-300" loading="lazy" />
          <div class="absolute inset-0 bg-black/40 opacity-0 group-hover/img:opacity-100 transition flex items-center justify-center text-white text-xs font-medium gap-1.5 backdrop-blur-[2px]">
            <i data-lucide="maximize-2" class="w-4 h-4"></i>
            <span>Zoom</span>
          </div>
        </div>
        <div class="flex items-center justify-between text-[11px] text-slate-400 mb-2 font-mono">
          <span class="truncate max-w-[170px]" title="${escapeHtml(item.content)}">${escapeHtml(item.content)}</span>
          <span>${formatFileSize(item.file_size)}</span>
        </div>
      `;
    } else if (item.type === 'file') {
      const ext = (item.content.split('.').pop() || 'file').toUpperCase();
      contentHtml = `
        <div class="flex items-center space-x-3 p-3 bg-slate-950/60 border border-slate-800/80 rounded-xl my-2.5">
          <div class="w-10 h-10 rounded-lg bg-indigo-950/80 border border-indigo-800/50 text-indigo-400 flex items-center justify-center font-bold text-xs shrink-0">
            ${escapeHtml(ext.substring(0, 4))}
          </div>
          <div class="flex-1 min-w-0">
            <p class="text-xs font-medium text-slate-200 truncate" title="${escapeHtml(item.content)}">${escapeHtml(item.content)}</p>
            <p class="text-[11px] text-slate-500 font-mono">${formatFileSize(item.file_size)}</p>
          </div>
        </div>
      `;
    } else {
      // Text / Note
      const formattedText = linkify(escapeHtml(item.content));
      contentHtml = `
        <div class="my-2.5 p-3 rounded-xl bg-slate-950/60 border border-slate-800/80 text-xs sm:text-sm text-slate-200 leading-relaxed font-sans break-words max-h-52 overflow-y-auto select-text whitespace-pre-wrap">
          ${formattedText}
        </div>
      `;
    }

    // Action buttons
    let actionButtons = '';
    if (item.type === 'text') {
      actionButtons = `
        <button onclick="window.ShareNow.copyText('${escapeJsString(item.content)}')" class="flex items-center space-x-1 px-2.5 py-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-300 hover:text-white text-xs font-medium transition" title="Copy text">
          <i data-lucide="copy" class="w-3.5 h-3.5"></i>
          <span>Copy</span>
        </button>
      `;
    } else {
      const downloadUrl = `/uploads/${encodeURIComponent(item.file_path)}?download=true${roomPin ? `&room_pin=${encodeURIComponent(roomPin)}` : ''}`;
      actionButtons = `
        <a href="${downloadUrl}" download="${escapeHtml(item.content)}" class="flex items-center space-x-1 px-2.5 py-1.5 rounded-lg bg-brand-600 hover:bg-brand-500 text-white text-xs font-medium shadow-sm transition" title="Download file">
          <i data-lucide="download" class="w-3.5 h-3.5"></i>
          <span>Download</span>
        </a>
      `;
    }

    card.innerHTML = `
      <div>
        <div class="flex items-center justify-between gap-1 mb-1">
          <div class="flex items-center gap-1.5">
            <span class="expiration-badge px-2 py-0.5 rounded-full text-[10px] font-mono font-medium border ${badgeClass}" data-expires="${item.expires_at}" data-id="${item.id}">
              ⏱ ${mins}m ${secs < 10 ? '0' : ''}${secs}s
            </span>
            ${burnBadge}
          </div>
          <button onclick="window.ShareNow.deleteItem('${item.id}')" class="text-slate-500 hover:text-red-400 p-1 rounded-md transition" title="Delete item">
            <i data-lucide="trash-2" class="w-3.5 h-3.5"></i>
          </button>
        </div>
        ${contentHtml}
      </div>
      <div class="flex items-center justify-between pt-2 border-t border-slate-800/60 mt-1">
        <span class="text-[10px] text-slate-500 font-mono">${formatDate(item.created_at)}</span>
        <div class="flex items-center space-x-1.5">
          ${actionButtons}
        </div>
      </div>
    `;

    return card;
  }

  // -----------------------------------------------------------
  // Message & Upload Actions
  // -----------------------------------------------------------
  async function sendMessage() {
    const text = messageInput.value.trim();
    if (!text) return;

    sendBtn.disabled = true;
    try {
      const res = await fetch('/api/messages', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          content: text,
          room: currentRoom,
          burn_after_reading: burnCheck.checked,
          room_pin: roomPin || null
        })
      });

      if (!res.ok) {
        const err = await res.json();
        showToast(err.detail || 'Failed to post note', 'error');
        return;
      }

      messageInput.value = '';
      burnCheck.checked = false;
      showToast('Note posted!', 'success');
    } catch (e) {
      showToast('Network error while posting note', 'error');
    } finally {
      sendBtn.disabled = false;
    }
  }

  function uploadFiles(files) {
    if (!files || files.length === 0) return;

    Array.from(files).forEach(file => {
      const formData = new FormData();
      formData.append('file', file);
      formData.append('room', currentRoom);
      formData.append('burn_after_reading', burnCheck.checked);
      if (roomPin) formData.append('room_pin', roomPin);

      uploadProgressContainer.classList.remove('hidden');
      uploadStatusText.textContent = `Uploading ${file.name}...`;

      const xhr = new XMLHttpRequest();
      xhr.open('POST', '/api/upload', true);

      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable) {
          const pct = Math.round((e.loaded / e.total) * 100);
          uploadProgressBar.style.width = `${pct}%`;
          uploadPercentText.textContent = `${pct}%`;
        }
      };

      xhr.onload = () => {
        uploadProgressContainer.classList.add('hidden');
        uploadProgressBar.style.width = '0%';
        if (xhr.status === 200) {
          showToast(`Uploaded ${file.name}!`, 'success');
          burnCheck.checked = false;
        } else {
          try {
            const err = JSON.parse(xhr.responseText);
            showToast(err.detail || 'Upload failed', 'error');
          } catch (e) {
            showToast('Upload failed', 'error');
          }
        }
      };

      xhr.onerror = () => {
        uploadProgressContainer.classList.add('hidden');
        showToast('Network error during upload', 'error');
      };

      xhr.send(formData);
    });
  }

  // -----------------------------------------------------------
  // Global Clipboard Paste (Ctrl+V)
  // -----------------------------------------------------------
  window.addEventListener('paste', (e) => {
    // If active in messageInput, let plain text paste normally
    if (document.activeElement === messageInput && (!e.clipboardData.files || e.clipboardData.files.length === 0)) {
      return;
    }

    if (e.clipboardData && e.clipboardData.files && e.clipboardData.files.length > 0) {
      e.preventDefault();
      uploadFiles(e.clipboardData.files);
      showToast('Pasting image/file from clipboard...', 'info');
      return;
    }

    const text = e.clipboardData.getData('text');
    if (text && document.activeElement !== messageInput) {
      messageInput.value = text;
      messageInput.focus();
      showToast('Pasted text into note box', 'info');
    }
  });

  // -----------------------------------------------------------
  // Drag & Drop
  // -----------------------------------------------------------
  let dragCounter = 0;
  window.addEventListener('dragenter', (e) => {
    e.preventDefault();
    dragCounter++;
    dragOverlay.classList.remove('hidden');
  });

  window.addEventListener('dragleave', (e) => {
    e.preventDefault();
    dragCounter--;
    if (dragCounter <= 0) {
      dragCounter = 0;
      dragOverlay.classList.add('hidden');
    }
  });

  window.addEventListener('dragover', (e) => {
    e.preventDefault();
  });

  window.addEventListener('drop', (e) => {
    e.preventDefault();
    dragCounter = 0;
    dragOverlay.classList.add('hidden');
    if (e.dataTransfer && e.dataTransfer.files) {
      uploadFiles(e.dataTransfer.files);
    }
  });

  dropZone.addEventListener('click', () => fileInput.click());
  fileInput.addEventListener('change', () => {
    uploadFiles(fileInput.files);
    fileInput.value = '';
  });

  snapCameraBtn.addEventListener('click', () => cameraInput.click());
  cameraInput.addEventListener('change', () => {
    uploadFiles(cameraInput.files);
    cameraInput.value = '';
  });

  // -----------------------------------------------------------
  // Room Switcher & PIN
  // -----------------------------------------------------------
  function promptRoomPin() {
    modalRoomInput.value = currentRoom;
    modalPinInput.value = '';
    roomModal.classList.remove('hidden');
    modalPinInput.focus();
  }

  roomBtn.addEventListener('click', () => {
    modalRoomInput.value = currentRoom;
    modalPinInput.value = roomPin;
    roomModal.classList.remove('hidden');
    modalRoomInput.focus();
  });

  closeRoomModal.addEventListener('click', () => roomModal.classList.add('hidden'));

  saveRoomBtn.addEventListener('click', async () => {
    const newRoom = (modalRoomInput.value || 'global').trim().toLowerCase();
    const pin = modalPinInput.value.trim();

    currentRoom = newRoom;
    currentRoomLabel.textContent = newRoom;
    if (pin) {
      roomPin = pin;
      sessionStorage.setItem(`room_pin_${currentRoom}`, pin);
    } else {
      roomPin = '';
      sessionStorage.removeItem(`room_pin_${currentRoom}`);
    }

    const newUrl = new URL(window.location);
    newUrl.searchParams.set('room', currentRoom);
    window.history.pushState({}, '', newUrl);

    roomModal.classList.add('hidden');
    initWebSocket();
    loadItems();
    showToast(`Switched to room #${currentRoom}`);
  });

  setPinBtn.addEventListener('click', async () => {
    const pin = modalPinInput.value.trim();
    if (!pin || pin.length < 4) {
      showToast('PIN must be at least 4 digits', 'error');
      return;
    }
    try {
      const res = await fetch('/api/rooms/pin', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ room: currentRoom, pin })
      });
      if (res.ok) {
        roomPin = pin;
        sessionStorage.setItem(`room_pin_${currentRoom}`, pin);
        roomLockIcon.classList.remove('hidden');
        roomModal.classList.add('hidden');
        showToast(`Room #${currentRoom} locked with PIN!`, 'success');
      } else {
        showToast('Failed to lock room', 'error');
      }
    } catch (e) {
      showToast('Error setting PIN', 'error');
    }
  });

  // -----------------------------------------------------------
  // Mobile QR Code Modal
  // -----------------------------------------------------------
  qrModalBtn.addEventListener('click', () => {
    qrcodeContainer.innerHTML = '';
    const origin = networkInfo && networkInfo.primary_url ? networkInfo.primary_url : window.location.origin;
    const mobileUrl = `${origin}/?room=${encodeURIComponent(currentRoom)}`;

    qrUrlLabel.textContent = mobileUrl;
    qrcodeInstance = new QRCode(qrcodeContainer, {
      text: mobileUrl,
      width: 170,
      height: 170,
      colorDark: '#0f172a',
      colorLight: '#ffffff',
      correctLevel: QRCode.CorrectLevel.M
    });

    qrModal.classList.remove('hidden');
  });

  closeQrModal.addEventListener('click', () => qrModal.classList.add('hidden'));

  copyQrLinkBtn.addEventListener('click', () => {
    const url = qrUrlLabel.textContent;
    navigator.clipboard.writeText(url).then(() => {
      showToast('Mobile link copied!', 'success');
    });
  });

  // -----------------------------------------------------------
  // Theme & Sound Toggles
  // -----------------------------------------------------------
  function applyTheme(theme) {
    if (theme === 'light') {
      document.documentElement.classList.remove('dark');
      themeIcon.setAttribute('data-lucide', 'moon');
    } else {
      document.documentElement.classList.add('dark');
      themeIcon.setAttribute('data-lucide', 'sun');
    }
    localStorage.setItem('sharenow_theme', theme);
    lucide.createIcons();
  }

  themeToggleBtn.addEventListener('click', () => {
    const isDark = document.documentElement.classList.contains('dark');
    applyTheme(isDark ? 'light' : 'dark');
  });

  soundToggleBtn.addEventListener('click', () => {
    soundEnabled = !soundEnabled;
    localStorage.setItem('sharenow_sound', soundEnabled);
    soundIcon.setAttribute('data-lucide', soundEnabled ? 'volume-2' : 'volume-x');
    lucide.createIcons();
    showToast(soundEnabled ? 'Chime sound on' : 'Chime sound muted');
  });

  // -----------------------------------------------------------
  // Filters & Search
  // -----------------------------------------------------------
  filterTabs.forEach(btn => {
    btn.addEventListener('click', () => {
      filterTabs.forEach(b => {
        b.classList.remove('bg-brand-600', 'text-white', 'active');
        b.classList.add('text-slate-400');
      });
      btn.classList.add('bg-brand-600', 'text-white', 'active');
      btn.classList.remove('text-slate-400');
      activeFilter = btn.dataset.filter;
      renderItems();
    });
  });

  searchInput.addEventListener('input', (e) => {
    searchQuery = e.target.value.trim();
    renderItems();
  });

  // -----------------------------------------------------------
  // Global Actions (exposed on window.ShareNow)
  // -----------------------------------------------------------
  window.ShareNow = {
    copyText: (text) => {
      navigator.clipboard.writeText(text).then(() => {
        showToast('Copied to clipboard!', 'success');
      });
    },

    deleteItem: async (id) => {
      try {
        let url = `/api/items/${encodeURIComponent(id)}`;
        if (roomPin) url += `?room_pin=${encodeURIComponent(roomPin)}`;
        const res = await fetch(url, { method: 'DELETE' });
        if (res.ok) {
          items = items.filter(i => i.id !== id);
          const el = document.getElementById(`item-${id}`);
          if (el) el.remove();
          checkEmptyState();
          showToast('Item deleted', 'info');
        } else {
          showToast('Failed to delete item', 'error');
        }
      } catch (e) {
        showToast('Error deleting item', 'error');
      }
    },

    openLightbox: (src, title) => {
      lightboxImg.src = src;
      lightboxDownloadBtn.href = src;
      lightboxDownloadBtn.download = title;
      lightboxModal.classList.remove('hidden');
    }
  };

  closeLightbox.addEventListener('click', () => lightboxModal.classList.add('hidden'));

  // -----------------------------------------------------------
  // Helper Functions
  // -----------------------------------------------------------
  function escapeHtml(str) {
    if (!str) return '';
    return str
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }

  function escapeJsString(str) {
    if (!str) return '';
    return str.replace(/\\/g, '\\\\').replace(/'/g, "\\'").replace(/\n/g, '\\n').replace(/\r/g, '');
  }

  function linkify(text) {
    const urlPattern = /(\b(https?|ftp):\/\/[-A-Z0-9+&@#\/%?=~_|!:,.;]*[-A-Z0-9+&@#\/%=~_|])/gim;
    return text.replace(urlPattern, '<a href="$1" target="_blank" rel="noopener noreferrer" class="text-brand-400 hover:underline break-all">$1</a>');
  }

  function formatFileSize(bytes) {
    if (!bytes || bytes === 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
  }

  function formatDate(timestamp) {
    if (!timestamp) return '';
    const date = new Date(timestamp * 1000);
    return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  }

  // -----------------------------------------------------------
  // Init
  // -----------------------------------------------------------
  currentRoomLabel.textContent = currentRoom;
  const initialTheme = localStorage.getItem('sharenow_theme') || 'dark';
  applyTheme(initialTheme);
  soundIcon.setAttribute('data-lucide', soundEnabled ? 'volume-2' : 'volume-x');

  sendBtn.addEventListener('click', sendMessage);
  messageInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      sendMessage();
    }
  });

  // PWA Service Worker Registration
  if ('serviceWorker' in navigator) {
    window.addEventListener('load', () => {
      navigator.serviceWorker.register('/sw.js').catch(() => {});
    });
  }

  initWebSocket();
  loadItems();
  fetchNetworkInfo();
  lucide.createIcons();

})();
