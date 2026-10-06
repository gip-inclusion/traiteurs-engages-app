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

  function fileInputOf(form) {
    return form.querySelector('[data-modal-file]');
  }

  function hasFile(form) {
    var input = fileInputOf(form);
    return !!(input && input.files && input.files.length > 0);
  }

  // Vide la selection et masque le chip. Appele au choix d'un fichier, au
  // clic sur la croix, et a la fermeture de la modale.
  function clearFile(form) {
    var input = fileInputOf(form);
    if (input) input.value = '';
    var chip = form.querySelector('[data-modal-file-chip]');
    if (chip) chip.classList.add('hidden');
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
      clearFile(form);
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

    var fileInput = fileInputOf(form);
    var attachBtn = form.querySelector('[data-modal-attach]');
    if (attachBtn && fileInput) {
      attachBtn.addEventListener('click', function () { fileInput.click(); });
    }
    if (fileInput) {
      fileInput.addEventListener('change', function () {
        var chip = form.querySelector('[data-modal-file-chip]');
        var name = form.querySelector('[data-modal-file-name]');
        if (hasFile(form) && chip && name) {
          name.textContent = fileInput.files[0].name;
          chip.classList.remove('hidden');
        } else if (chip) {
          chip.classList.add('hidden');
        }
        clearError(form);
      });
    }
    var fileRemove = form.querySelector('[data-modal-file-remove]');
    if (fileRemove) {
      fileRemove.addEventListener('click', function () { clearFile(form); });
    }

    form.addEventListener('submit', function (ev) {
      ev.preventDefault();
      clearError(form);

      var body = (form.querySelector('[name="body"]').value || '').trim();
      // PJ seule autorisee, comme dans la messagerie : texte OU fichier.
      if (!body && !hasFile(form)) {
        showError(form, 'Ajoutez un message ou une pièce jointe.');
        return;
      }

      var recipientId = form.querySelector('[name="recipient_id"]').value;
      var orderInput = form.querySelector('[name="order_id"]');
      var qrInput = form.querySelector('[name="quote_request_id"]');

      var csrfMeta = document.querySelector('meta[name="csrf-token"]');
      var csrfToken = csrfMeta ? csrfMeta.content : '';

      var fetchOpts;
      if (hasFile(form)) {
        // Multipart quand il y a un fichier — ne PAS fixer Content-Type,
        // le navigateur ajoute le boundary lui-même.
        var fd = new FormData();
        fd.append('recipient_id', recipientId);
        fd.append('body', body);
        if (orderInput && orderInput.value) fd.append('order_id', orderInput.value);
        if (qrInput && qrInput.value) fd.append('quote_request_id', qrInput.value);
        fd.append('file', fileInputOf(form).files[0]);
        fetchOpts = {
          method: 'POST',
          headers: { 'X-CSRFToken': csrfToken },
          body: fd,
        };
      } else {
        var payload = { recipient_id: recipientId, body: body };
        if (orderInput && orderInput.value) payload.order_id = orderInput.value;
        if (qrInput && qrInput.value) payload.quote_request_id = qrInput.value;
        fetchOpts = {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': csrfToken,
          },
          body: JSON.stringify(payload),
        };
      }

      if (sendBtn) sendBtn.disabled = true;
      fetch('/api/messages', fetchOpts)
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
