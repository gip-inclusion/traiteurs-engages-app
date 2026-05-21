(function () {
  'use strict';

  function open(modal) {
    if (!modal) return;
    modal.classList.remove('hidden');
    document.body.style.overflow = 'hidden';
    if (window.lucide) lucide.createIcons();
  }
  function close(modal) {
    if (!modal) return;
    modal.classList.add('hidden');
    // Release the scroll lock only if no other modal is still open.
    var anyOpen = document.querySelector('[id^="quote-pdf-modal-"]:not(.hidden)');
    if (!anyOpen) document.body.style.overflow = '';
  }
  function findOpenModal() {
    return document.querySelector('[id^="quote-pdf-modal-"]:not(.hidden)');
  }

  document.addEventListener('DOMContentLoaded', function () {
    document.addEventListener('click', function (ev) {
      var openBtn = ev.target.closest('[data-action="open-quote-pdf"]');
      if (openBtn) {
        var targetId = openBtn.getAttribute('data-target');
        if (targetId) open(document.getElementById(targetId));
        return;
      }
      var closeBtn = ev.target.closest('[data-action="close-quote-pdf"]');
      if (closeBtn) {
        var targetIdC = closeBtn.getAttribute('data-target');
        close(document.getElementById(targetIdC));
        return;
      }
      // Backdrop click (the modal node itself, not its content card).
      if (ev.target && ev.target.id && ev.target.id.indexOf('quote-pdf-modal-') === 0) {
        close(ev.target);
      }
    });

    document.addEventListener('keydown', function (ev) {
      if (ev.key !== 'Escape') return;
      var openModal = findOpenModal();
      if (openModal) close(openModal);
    });
  });
})();
