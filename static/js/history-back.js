(function () {
  'use strict';

  document.addEventListener('click', function (ev) {
    var btn = ev.target.closest('[data-action="history-back"]');
    if (!btn) return;
    ev.preventDefault();
    if (window.history.length > 1) {
      window.history.back();
    } else {
      window.location.href = '/';
    }
  });
})();
