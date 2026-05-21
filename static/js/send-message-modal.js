(function () {
  'use strict';

  // Sentinel URL produced by url_for(thread_endpoint, thread_id=<this>);
  // we substitute it with the thread_id returned by /api/messages so a
  // route rename blows up in Flask (render time), not silently here.
  var THREAD_ID_SENTINEL = '00000000-0000-0000-0000-000000000000';

  function buildThreadUrl(dialog, threadId) {
    var tpl = dialog && dialog.dataset.threadUrlTemplate;
    if (!tpl || !threadId) return null;
    return tpl.replace(THREAD_ID_SENTINEL, threadId);
  }

  function showError(form, msg) {
    var box = form.querySelector('[data-modal-error]');
    if (!box) return;
    box.textContent = msg;
    box.classList.remove('hidden');
  }

  function clearError(form) {
    var box = form.querySelector('[data-modal-error]');
    if (!box) return;
    box.classList.add('hidden');
    box.textContent = '';
  }

  function swapToSentState(modalId, threadId) {
    var dialog = document.getElementById(modalId);
    if (!dialog) return;
    var compose = dialog.querySelector('[data-modal-state="compose"]');
    var sent = dialog.querySelector('[data-modal-state="sent"]');
    if (compose) compose.classList.add('hidden');
    if (sent) sent.classList.remove('hidden');

    if (threadId) {
      var link = dialog.querySelector('[data-modal-thread-link]');
      var href = buildThreadUrl(dialog, threadId);
      if (link && href) link.href = href;
    }
    // Scoped refresh to avoid reprocessing every icon on the page.
    if (window.lucide && lucide.createIcons) {
      try { lucide.createIcons({ root: dialog }); }
      catch (_) { lucide.createIcons(); }
    }
  }

  function resetToComposeState(dialog) {
    var compose = dialog.querySelector('[data-modal-state="compose"]');
    var sent = dialog.querySelector('[data-modal-state="sent"]');
    if (compose) compose.classList.remove('hidden');
    if (sent) sent.classList.add('hidden');
    var form = dialog.querySelector('[data-send-message-form]');
    if (form) {
      var ta = form.querySelector('[name="body"]');
      if (ta) ta.value = '';
      clearError(form);
      var btn = form.querySelector('[data-modal-send]');
      if (btn) btn.disabled = false;
    }
  }

  function bindForm(form) {
    if (form.__sendMessageBound) return;
    form.__sendMessageBound = true;

    var modalId = form.dataset.sendMessageForm;
    var sendBtn = form.querySelector('[data-modal-send]');

    form.addEventListener('submit', function (ev) {
      ev.preventDefault();
      clearError(form);

      var body = (form.querySelector('[name="body"]').value || '').trim();
      if (!body) {
        showError(form, 'Le message ne peut pas être vide.');
        return;
      }

      var payload = {
        recipient_id: form.querySelector('[name="recipient_id"]').value,
        body: body,
      };
      var orderInput = form.querySelector('[name="order_id"]');
      if (orderInput && orderInput.value) payload.order_id = orderInput.value;
      var qrInput = form.querySelector('[name="quote_request_id"]');
      if (qrInput && qrInput.value) payload.quote_request_id = qrInput.value;

      var csrfMeta = document.querySelector('meta[name="csrf-token"]');
      var csrfToken = csrfMeta ? csrfMeta.content : '';

      if (sendBtn) sendBtn.disabled = true;
      fetch('/api/messages', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRFToken': csrfToken,
        },
        body: JSON.stringify(payload),
      })
        .then(function (r) {
          return r.json().then(function (data) { return { ok: r.ok, data: data }; });
        })
        .then(function (res) {
          if (!res.ok) {
            showError(
              form,
              (res.data && res.data.error) || 'Erreur lors de l’envoi.'
            );
            if (sendBtn) sendBtn.disabled = false;
            return;
          }
          swapToSentState(modalId, res.data && res.data.thread_id);
        })
        .catch(function () {
          showError(form, 'Erreur réseau. Réessayez.');
          if (sendBtn) sendBtn.disabled = false;
        });
    });
  }

  function bindCloseReset(dialog) {
    if (dialog.__sendMessageCloseBound) return;
    dialog.__sendMessageCloseBound = true;
    // `close` fires on both explicit close() and backdrop-dismiss.
    dialog.addEventListener('close', function () {
      resetToComposeState(dialog);
    });
  }

  document.addEventListener('DOMContentLoaded', function () {
    document
      .querySelectorAll('[data-send-message-form]')
      .forEach(bindForm);
    document
      .querySelectorAll('dialog[data-send-message-dialog]')
      .forEach(bindCloseReset);
  });
})();
