(function () {
  function renderIcons() { if (window.lucide) lucide.createIcons(); }

  function markActiveSidebar() {
    var path = window.location.pathname;
    document.querySelectorAll('.sidebar-link').forEach(function (link) {
      var href = link.getAttribute('href');
      if (href && (path === href || path.startsWith(href + '/'))) link.classList.add('active');
    });
  }

  function toggleMobileMenu() {
    var sidebar = document.getElementById('sidebar');
    var overlay = document.getElementById('sidebar-overlay');
    if (sidebar) sidebar.classList.toggle('-translate-x-full');
    if (overlay) overlay.classList.toggle('hidden');
  }

  function updateLayout() {
    var main = document.getElementById('main-content');
    if (!main) return;
    main.style.marginLeft = window.innerWidth >= 1024 ? '241px' : '0';
  }

  function updateNotificationBadge() {
    // Server pre-renders the count; this poll only refreshes live. A
    // non-JSON response leaves the SSR value rather than wiping to 0.
    fetch('/api/notifications', { credentials: 'same-origin' })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) {
        if (!data || typeof data.unread_count !== 'number') return;
        var n = Math.max(0, data.unread_count);
        document.querySelectorAll('.notification-badge').forEach(function (badge) {
          // Short-circuit when the count hasn't moved — saves DOM writes on idle tabs.
          if (badge.dataset.count === String(n)) return;
          var plural = n === 1 ? '' : 's';
          badge.classList.toggle('hidden', n <= 0);
          badge.textContent = n > 99 ? '99+' : String(n);
          badge.setAttribute('data-count', String(n));
          badge.setAttribute('aria-label', n + ' notification' + plural + ' non lue' + plural);
        });
      })
      .catch(function () {});
  }

  var ACTIONS = {
    'toggle-mobile-menu': function () { toggleMobileMenu(); },
    'dialog-open': function (el) {
      var d = document.getElementById(el.dataset.target);
      if (d && d.showModal) d.showModal();
      else if (d) { d.classList.remove('hidden'); d.classList.add('flex'); }
    },
    'dialog-close': function (el) {
      var d = document.getElementById(el.dataset.target);
      if (d && d.close) d.close();
      else if (d) { d.classList.add('hidden'); d.classList.remove('flex'); }
    },
    'dismiss-parent': function (el) {
      var target = el.closest(el.dataset.target || '[data-dismissable]') || el.parentElement;
      if (target) target.remove();
    },
    'toggle-hidden': function (el) {
      var d = document.getElementById(el.dataset.target);
      if (d) d.classList.toggle('hidden');
    },
    'submit-form': function (el) {
      var f = document.getElementById(el.dataset.target);
      if (f) f.submit();
    },
    'confirm-submit': function (el, ev) {
      if (!window.confirm(el.dataset.confirm || 'Confirmer ?')) ev.preventDefault();
    },
    'copy-to-clipboard': function (el) {
      var text = el.dataset.value;
      if (!text && el.dataset.target) {
        var src = document.getElementById(el.dataset.target);
        if (src) text = src.value || src.textContent || '';
      }
      if (!text) return;
      var done = function () {
        var label = el.querySelector('[data-copy-label]') || el;
        var original = label.textContent;
        label.textContent = el.dataset.copiedLabel || 'Copié !';
        setTimeout(function () { label.textContent = original; }, 1500);
      };
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(done, function () {});
        return;
      }
      // Fallback for older browsers / non-HTTPS contexts.
      var ta = document.createElement('textarea');
      ta.value = text;
      ta.style.position = 'fixed';
      ta.style.opacity = '0';
      document.body.appendChild(ta);
      ta.select();
      try { document.execCommand('copy'); done(); } catch (e) {}
      document.body.removeChild(ta);
    },
  };

  document.addEventListener('click', function (ev) {
    var el = ev.target.closest('[data-action]');
    if (!el) return;
    var fn = ACTIONS[el.dataset.action];
    if (fn) fn(el, ev);
  });

  // Close on click outside; relies on running after the toggle-hidden
  // handler so the bell can still open the dropdown.
  document.addEventListener('click', function (ev) {
    var dropdown = document.getElementById('notifications-modal');
    if (!dropdown || dropdown.classList.contains('hidden')) return;
    if (ev.target.closest('[data-target="notifications-modal"]')) return;
    if (ev.target.closest('#notifications-modal')) return;
    dropdown.classList.add('hidden');
  });

  function initBackdropDismiss() {
    document.querySelectorAll('[data-backdrop-dismiss]').forEach(function (el) {
      if (el.__bdInit) return;
      el.__bdInit = true;
      el.addEventListener('click', function (ev) {
        if (ev.target !== el) return;
        if (el.close) el.close();
        else { el.classList.add('hidden'); el.classList.remove('flex'); }
      });
    });
  }

  // Per-category auto-dismiss; hover pauses the timer.
  function initFlashToasts() {
    var FLASH_DURATIONS = { success: 4000, info: 5000, error: 7000 };
    var toasts = document.querySelectorAll('.flash-toast');
    toasts.forEach(function (toast) {
      var category = toast.dataset.flashCategory || 'info';
      var duration = FLASH_DURATIONS[category] || FLASH_DURATIONS.info;
      var timerId = null;

      function dismiss() {
        toast.classList.add('flash-toast-leaving');
        // Wait for the CSS transition so SR doesn't lose the node mid-flight.
        setTimeout(function () {
          if (toast.parentNode) toast.parentNode.removeChild(toast);
        }, 280);
      }
      function start() { timerId = setTimeout(dismiss, duration); }
      function clear() { if (timerId) { clearTimeout(timerId); timerId = null; } }

      toast.addEventListener('mouseenter', clear);
      toast.addEventListener('mouseleave', start);
      toast.addEventListener('click', function (ev) {
        if (ev.target.closest('[data-action="dismiss-parent"]')) clear();
      });
      start();
    });
  }

  document.addEventListener('DOMContentLoaded', function () {
    renderIcons();
    markActiveSidebar();
    var mobileBtn = document.getElementById('mobile-menu-btn');
    if (mobileBtn) mobileBtn.addEventListener('click', toggleMobileMenu);
    updateLayout();
    window.addEventListener('resize', updateLayout);
    if (document.querySelector('.notification-badge')) {
      // Pause polling on hidden tabs; fresh fetch fires on resume.
      var pollId = null;
      function startPolling() {
        if (pollId !== null) return;
        updateNotificationBadge();
        pollId = setInterval(updateNotificationBadge, 30000);
      }
      function stopPolling() {
        if (pollId === null) return;
        clearInterval(pollId);
        pollId = null;
      }
      if (!document.hidden) startPolling();
      document.addEventListener('visibilitychange', function () {
        if (document.hidden) stopPolling();
        else startPolling();
      });
    }
    initBackdropDismiss();
    initFlashToasts();
  });
})();
