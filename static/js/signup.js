(function () {
  function selectRole(role) {
    var roleInput = document.getElementById('role-input');
    var signupForm = document.getElementById('signup-form');
    var catererFields = document.getElementById('caterer-fields');

    if (roleInput) roleInput.value = role === 'client' ? 'client_admin' : 'caterer';
    // Le formulaire reste masqué tant qu'aucune carte n'a été cliquée
    // (cf. la classe `hidden` au chargement). Une fois qu'on en choisit
    // une, on l'affiche pour de bon — pas de bascule vers display:none
    // lors d'un changement de carte ensuite.
    if (signupForm) signupForm.classList.remove('hidden');
    if (catererFields) catererFields.classList.toggle('hidden', role !== 'caterer');

    // Highlight de la carte active. La classe `role-card-active` porte
    // le style (border coral + fond léger) — cf. static/css/app.css.
    var cardClient = document.getElementById('card-client');
    var cardCaterer = document.getElementById('card-caterer');
    if (cardClient) cardClient.classList.toggle('role-card-active', role === 'client');
    if (cardCaterer) cardCaterer.classList.toggle('role-card-active', role === 'caterer');

    var catererInputs = document.querySelectorAll('#caterer-fields input, #caterer-fields select');
    catererInputs.forEach(function (el) { el.required = (role === 'caterer'); });

    revalidate();
  }

  function togglePassword(inputId, btn) {
    var input = document.getElementById(inputId);
    if (!input) return;
    var icon = btn.querySelector('[data-lucide]');
    if (input.type === 'password') {
      input.type = 'text';
      if (icon) icon.setAttribute('data-lucide', 'eye-off');
    } else {
      input.type = 'password';
      if (icon) icon.setAttribute('data-lucide', 'eye');
    }
    if (window.lucide) lucide.createIcons();
  }

  // Mirror of blueprints/auth.py.validate_password — server re-validates
  // on POST so this is UX only.
  function passwordRules(pw) {
    pw = pw || '';
    var hasLower = /[a-z]/.test(pw);
    var hasUpper = /[A-Z]/.test(pw);
    var hasDigit = /\d/.test(pw);
    var hasSpecial = /[^A-Za-z0-9]/.test(pw);
    var categories = (hasLower ? 1 : 0) + (hasUpper ? 1 : 0) + (hasDigit ? 1 : 0) + (hasSpecial ? 1 : 0);
    return {
      length: pw.length >= 12,
      categories: categories >= 3,
    };
  }

  function paintRule(li, ok) {
    if (!li) return;
    li.classList.toggle('pw-rule-ok', ok);
    var icon = li.querySelector('[data-lucide]');
    if (icon) {
      icon.setAttribute('data-lucide', ok ? 'check-circle' : 'circle');
    }
  }

  function isFormValid() {
    var form = document.getElementById('signup-form');
    if (!form || form.style.display === 'none') return false;
    // offsetParent === null filters out the inputs hidden by role toggle.
    var fields = form.querySelectorAll('input[required], select[required]');
    for (var i = 0; i < fields.length; i++) {
      var f = fields[i];
      if (f.offsetParent === null) continue;
      // Checkboxes always have value="on"; branch on `checked` instead.
      if (f.type === 'checkbox') {
        if (!f.checked) return false;
        continue;
      }
      if (!String(f.value || '').trim()) return false;
    }
    var rules = passwordRules(document.getElementById('password').value);
    return rules.length && rules.categories;
  }

  function revalidate() {
    var pw = document.getElementById('password');
    if (pw) {
      var rules = passwordRules(pw.value);
      paintRule(document.querySelector('#password-requirements [data-rule="length"]'), rules.length);
      paintRule(document.querySelector('#password-requirements [data-rule="categories"]'), rules.categories);
      if (window.lucide) lucide.createIcons();
    }
    var btn = document.getElementById('signup-submit');
    var hint = document.getElementById('signup-hint');
    if (btn) {
      var valid = isFormValid();
      btn.disabled = !valid;
      btn.classList.toggle('signup-submit-disabled', !valid);
      if (hint) hint.style.display = valid ? 'none' : 'block';
    }
  }

  document.addEventListener('click', function (ev) {
    var roleEl = ev.target.closest('[data-action="select-role"]');
    if (roleEl) {
      selectRole(roleEl.dataset.role);
      return;
    }
    var pwEl = ev.target.closest('[data-action="toggle-password"]');
    if (pwEl) {
      togglePassword(pwEl.dataset.target, pwEl);
    }
  });

  document.addEventListener('input', function (ev) {
    if (ev.target.closest('#signup-form')) revalidate();
  });
  document.addEventListener('change', function (ev) {
    if (ev.target.closest('#signup-form')) revalidate();
  });

  document.addEventListener('DOMContentLoaded', function () {
    // No pre-selection: the form stays hidden until the user clicks
    // one of the two role cards. Avoids the prior bug where caterers
    // landed on the pre-selected Entreprise form and filled it out.
    revalidate();
  });
})();
