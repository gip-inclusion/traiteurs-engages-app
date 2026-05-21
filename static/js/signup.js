(function () {
  // Libellés du titre / sous-titre une fois le type choisi. Avant le
  // choix, le HTML porte « Créer votre compte » / « Rejoignez Les
  // Traiteurs Engagés » ; on les restaure tels quels en cas de retour.
  var DEFAULT_TITLE = 'Créer votre compte';
  var DEFAULT_SUBTITLE = 'Rejoignez Les Traiteurs Engagés';
  var TITLES = {
    client: { title: 'Inscription entreprise', subtitle: 'Créez votre compte pour commander des prestations.' },
    caterer: { title: 'Inscription traiteur', subtitle: 'Créez votre compte pour proposer vos prestations.' },
  };

  function setStepHeader(role) {
    var title = document.getElementById('signup-title');
    var subtitle = document.getElementById('signup-subtitle');
    if (!title || !subtitle) return;
    if (role && TITLES[role]) {
      title.textContent = TITLES[role].title;
      subtitle.textContent = TITLES[role].subtitle;
    } else {
      title.textContent = DEFAULT_TITLE;
      subtitle.textContent = DEFAULT_SUBTITLE;
    }
  }

  function selectRole(role) {
    var roleInput = document.getElementById('role-input');
    var signupForm = document.getElementById('signup-form');
    var catererFields = document.getElementById('caterer-fields');
    var roleChoiceBlock = document.getElementById('role-choice-block');
    var backBtn = document.getElementById('back-to-role');

    if (roleInput) roleInput.value = role === 'client' ? 'client_admin' : 'caterer';
    // Cache le bloc de choix et révèle le formulaire. Le retour
    // (back-to-role) inverse les deux opérations.
    if (roleChoiceBlock) roleChoiceBlock.classList.add('hidden');
    if (signupForm) signupForm.classList.remove('hidden');
    if (backBtn) backBtn.classList.remove('hidden');
    if (catererFields) catererFields.classList.toggle('hidden', role !== 'caterer');

    setStepHeader(role);

    var catererInputs = document.querySelectorAll('#caterer-fields input, #caterer-fields select');
    catererInputs.forEach(function (el) { el.required = (role === 'caterer'); });

    revalidate();
  }

  function backToRoleChoice() {
    var roleInput = document.getElementById('role-input');
    var signupForm = document.getElementById('signup-form');
    var roleChoiceBlock = document.getElementById('role-choice-block');
    var backBtn = document.getElementById('back-to-role');

    if (roleInput) roleInput.value = '';
    if (signupForm) signupForm.classList.add('hidden');
    if (roleChoiceBlock) roleChoiceBlock.classList.remove('hidden');
    if (backBtn) backBtn.classList.add('hidden');
    setStepHeader(null);
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
    var backEl = ev.target.closest('[data-action="back-to-role"]');
    if (backEl) {
      backToRoleChoice();
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
