// CSP P2: external script, no inline (no per-render nonce by design).
(function () {
  'use strict';

  document.addEventListener('DOMContentLoaded', function () {
    var modal = document.getElementById('pdf-preview-modal');
    if (!modal) return;

    function open() {
      modal.classList.remove('hidden');
      document.body.style.overflow = 'hidden';
      if (window.lucide) lucide.createIcons();
    }
    function close() {
      modal.classList.add('hidden');
      document.body.style.overflow = '';
    }

    document.addEventListener('click', function (ev) {
      if (ev.target.closest('[data-action="open-pdf-preview"]')) { open(); return; }
      if (ev.target.closest('[data-action="close-pdf-preview"]')) { close(); return; }
      // Backdrop click (the modal node itself, not its content card).
      if (ev.target === modal) close();
    });

    document.addEventListener('keydown', function (ev) {
      if (ev.key === 'Escape' && !modal.classList.contains('hidden')) close();
    });
  });
})();
