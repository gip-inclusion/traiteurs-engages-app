(function () {
  'use strict';

  document.addEventListener('DOMContentLoaded', function () {
    var menu = document.getElementById('new-request-modal');
    if (!menu) return;

    var currentTrigger = null;

    function position(trigger) {
      var rect = trigger.getBoundingClientRect();
      // Reveal hidden first so offsetWidth is measurable.
      menu.style.visibility = 'hidden';
      menu.classList.remove('hidden');
      var menuWidth = menu.offsetWidth;
      var left = rect.right - menuWidth;
      var minLeft = 8;
      var maxLeft = window.innerWidth - menuWidth - 8;
      if (left < minLeft) left = minLeft;
      if (left > maxLeft) left = Math.max(minLeft, maxLeft);
      menu.style.top = (rect.bottom + 8) + 'px';
      menu.style.left = left + 'px';
      menu.style.visibility = '';
    }

    function open(trigger) {
      currentTrigger = trigger;
      position(trigger);
      if (window.lucide) lucide.createIcons();
    }
    function close() {
      menu.classList.add('hidden');
      currentTrigger = null;
    }
    function isOpen() {
      return !menu.classList.contains('hidden');
    }

    document.addEventListener('click', function (ev) {
      var trigger = ev.target.closest('[data-action="open-new-request-menu"]');
      if (trigger) {
        if (isOpen() && currentTrigger === trigger) {
          close();
        } else {
          open(trigger);
        }
        return;
      }
      if (isOpen() && !ev.target.closest('#new-request-modal')) {
        close();
      }
    });

    document.addEventListener('keydown', function (ev) {
      if (ev.key === 'Escape' && isOpen()) close();
    });

    // Close (rather than reposition) on scroll/resize.
    window.addEventListener('scroll', function () {
      if (isOpen()) close();
    }, true);
    window.addEventListener('resize', function () {
      if (isOpen()) close();
    });
  });
})();
