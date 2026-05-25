(function () {
  'use strict';

  document.addEventListener('DOMContentLoaded', function () {
    setupTabs();

    var container = document.getElementById('messages-container');
    if (!container) return;

    var threadId = container.dataset.threadId;
    var currentUserId = container.dataset.currentUserId;
    var otherAvatarUrl = container.dataset.otherAvatarUrl || '';

    function escapeHtml(text) {
      var d = document.createElement('div');
      d.textContent = text;
      return d.innerHTML;
    }

    function formatDate(iso) {
      var d = new Date(iso);
      var months = ['janvier','février','mars','avril','mai','juin',
                    'juillet','août','septembre','octobre','novembre','décembre'];
      var day = d.getDate();
      var month = months[d.getMonth()];
      var hh = ('0' + d.getHours()).slice(-2);
      var mm = ('0' + d.getMinutes()).slice(-2);
      var today = new Date();
      var sameDay = d.toDateString() === today.toDateString();
      return sameDay ? (hh + ':' + mm) : (day + ' ' + month + ' · ' + hh + ':' + mm);
    }

    function receivedAvatarHtml() {
      if (otherAvatarUrl) {
        return '<div class="w-8 h-8 rounded-lg flex items-center justify-center bg-cream flex-shrink-0" style="overflow:hidden;">' +
          '<img src="' + escapeHtml(otherAvatarUrl) + '" alt="" style="width:100%;height:100%;object-fit:contain;padding:0.0625rem;">' +
          '</div>';
      }
      return '<div class="w-8 h-8 rounded-lg flex items-center justify-center bg-cream flex-shrink-0">' +
        '<i data-lucide="building-2" class="w-4 h-4 text-text-soft"></i>' +
        '</div>';
    }

    function renderMessage(msg) {
      var isSent = msg.sender_id === currentUserId;
      var row = document.createElement('div');
      // `flex-row-reverse` would be cleaner, but the curated Tailwind
      // bundle doesn't include it; `justify-end` is already there.
      row.className = 'flex items-start gap-2 mb-4 ' + (isSent ? 'justify-end' : '');

      var bubbleWrap = document.createElement('div');
      bubbleWrap.className = 'max-w-[70%] flex flex-col ' + (isSent ? 'items-end' : 'items-start');
      // Le body peut être vide (message « pièce jointe seule »).
      var bodyHtml = msg.body
        ? '<p class="whitespace-pre-line">' + escapeHtml(msg.body) + '</p>'
        : '';
      var attachmentHtml = '';
      if (msg.attachment_url) {
        attachmentHtml =
          '<a href="' + escapeHtml(msg.attachment_url) + '" target="_blank" rel="noopener" ' +
          'class="inline-flex items-center gap-1.5 underline ' +
          (isSent ? 'text-white' : 'text-navy') + (bodyHtml ? ' mt-1.5' : '') + '">' +
          '<i data-lucide="paperclip" class="w-3.5 h-3.5 flex-shrink-0"></i>' +
          '<span class="truncate">' + escapeHtml(msg.attachment_name || 'Pièce jointe') + '</span>' +
          '</a>';
      }
      bubbleWrap.innerHTML =
        '<div class="rounded-2xl px-4 py-2.5 text-sm ' +
          (isSent ? 'bg-navy text-white' : 'bg-cream text-text') +
        '">' + bodyHtml + attachmentHtml + '</div>' +
        '<p class="text-xs mt-1 text-mute">' + escapeHtml(formatDate(msg.created_at)) + '</p>';

      if (!isSent) {
        var avatar = document.createElement('div');
        avatar.innerHTML = receivedAvatarHtml();
        var firstChild = avatar.firstChild;
        if (firstChild) row.appendChild(firstChild);
      }
      row.appendChild(bubbleWrap);
      return row;
    }

    function scrollToBottom() {
      container.scrollTop = container.scrollHeight;
    }

    function loadMessages() {
      fetch('/api/messages/' + threadId)
        .then(function (r) { return r.json(); })
        .then(function (data) {
          container.innerHTML = '';
          (data.messages || []).forEach(function (msg) {
            container.appendChild(renderMessage(msg));
          });
          if (window.lucide) lucide.createIcons();
          scrollToBottom();
        });
    }

    var form = document.getElementById('message-form');
    if (form) {
      var bodyInput = form.querySelector('[name="body"]');
      var sendBtn = document.getElementById('message-send-btn');
      var fileInput = document.getElementById('message-file');
      var attachBtn = document.getElementById('message-attach-btn');
      var fileChip = document.getElementById('message-file-chip');
      var fileChipName = document.getElementById('message-file-name');
      var fileRemove = document.getElementById('message-file-remove');

      function hasFile() {
        return fileInput && fileInput.files && fileInput.files.length > 0;
      }

      function refreshSendBtn() {
        // PJ seule autorisée : actif si texte OU fichier.
        var enabled = bodyInput.value.trim().length > 0 || hasFile();
        sendBtn.disabled = !enabled;
        if (enabled) {
          sendBtn.classList.remove('bg-disabled', 'opacity-60');
          sendBtn.classList.add('bg-navy');
        } else {
          sendBtn.classList.add('bg-disabled', 'opacity-60');
          sendBtn.classList.remove('bg-navy');
        }
      }

      function clearFile() {
        if (fileInput) fileInput.value = '';
        if (fileChip) fileChip.classList.add('hidden');
      }

      bodyInput.addEventListener('input', refreshSendBtn);
      if (attachBtn && fileInput) {
        attachBtn.addEventListener('click', function () { fileInput.click(); });
      }
      if (fileInput) {
        fileInput.addEventListener('change', function () {
          if (hasFile() && fileChip && fileChipName) {
            fileChipName.textContent = fileInput.files[0].name;
            fileChip.classList.remove('hidden');
          } else if (fileChip) {
            fileChip.classList.add('hidden');
          }
          refreshSendBtn();
        });
      }
      if (fileRemove) {
        fileRemove.addEventListener('click', function () {
          clearFile();
          refreshSendBtn();
        });
      }
      refreshSendBtn();

      form.addEventListener('submit', function (e) {
        e.preventDefault();
        var body = bodyInput.value.trim();
        if (!body && !hasFile()) return;

        var recipientId = form.querySelector('[name="recipient_id"]').value;
        var orderId = form.querySelector('[name="order_id"]');
        var qrId = form.querySelector('[name="quote_request_id"]');
        var csrfMeta = document.querySelector('meta[name="csrf-token"]');
        var csrfToken = csrfMeta ? csrfMeta.content : '';
        sendBtn.disabled = true;

        var fetchOpts;
        if (hasFile()) {
          // Multipart quand il y a un fichier — ne PAS fixer Content-Type,
          // le navigateur ajoute le boundary lui-même.
          var fd = new FormData();
          fd.append('recipient_id', recipientId);
          fd.append('body', body);
          if (orderId && orderId.value) fd.append('order_id', orderId.value);
          if (qrId && qrId.value) fd.append('quote_request_id', qrId.value);
          fd.append('file', fileInput.files[0]);
          fetchOpts = {
            method: 'POST',
            headers: { 'X-CSRFToken': csrfToken },
            body: fd,
          };
        } else {
          var payload = { recipient_id: recipientId, body: body };
          if (orderId && orderId.value) payload.order_id = orderId.value;
          if (qrId && qrId.value) payload.quote_request_id = qrId.value;
          fetchOpts = {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
            body: JSON.stringify(payload),
          };
        }

        fetch('/api/messages', fetchOpts)
          .then(function (r) {
            return r.json().then(function (data) { return { ok: r.ok, data: data }; });
          })
          .then(function (res) {
            if (!res.ok) {
              // Inline banner: the messagerie has no flash surface.
              showErrorBanner(res.data && res.data.error ? res.data.error : 'Erreur lors de l’envoi.');
              refreshSendBtn();
              return;
            }
            clearErrorBanner();
            bodyInput.value = '';
            clearFile();
            refreshSendBtn();
            loadMessages();
          })
          .catch(function () {
            showErrorBanner('Erreur reseau. Reessayez.');
            refreshSendBtn();
          });
      });

      function showErrorBanner(text) {
        var existing = document.getElementById('messagerie-error-banner');
        if (existing) existing.remove();
        var div = document.createElement('div');
        div.id = 'messagerie-error-banner';
        div.className = 'mx-4 mb-2 px-3 py-2 rounded-lg text-xs bg-danger-soft text-danger';
        div.textContent = text;
        form.parentNode.insertBefore(div, form);
      }
      function clearErrorBanner() {
        var existing = document.getElementById('messagerie-error-banner');
        if (existing) existing.remove();
      }
    }

    loadMessages();
    setInterval(loadMessages, 10000);
  });

  function setupTabs() {
    document.addEventListener('click', function (ev) {
      var btn = ev.target.closest('[data-action="messagerie-tab"]');
      if (!btn) return;
      var targetId = btn.dataset.target;
      var allBtns = document.querySelectorAll('[data-action="messagerie-tab"]');
      allBtns.forEach(function (b) {
        var active = b === btn;
        b.classList.toggle('border-navy', active);
        b.classList.toggle('text-navy', active);
        b.classList.toggle('border-transparent', !active);
        b.classList.toggle('text-mute', !active);
      });
      var panes = document.querySelectorAll('.messagerie-tab-pane');
      panes.forEach(function (p) {
        p.classList.toggle('hidden', p.id !== targetId);
      });
    });
  }
})();
