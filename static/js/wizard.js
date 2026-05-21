// pageshow + event.persisted detects a bfcache restore (Chrome/Firefox),
// which Cache-Control: no-store doesn't always defeat. Opt-in via
// data-back-redirect so only new.html (not edit.html) bounces away.
window.addEventListener('pageshow', function (e) {
  if (!e.persisted) return;
  var f = document.getElementById('wizard-form');
  if (!f || !f.dataset || !f.dataset.backRedirect) return;
  window.location.replace(f.dataset.backRedirect);
});

document.addEventListener('DOMContentLoaded', function () {
  var totalSteps = 7;
  var currentStep = 1;
  var form = document.getElementById('wizard-form');

  var stepLabels = [
    'Type de service',
    'Evenement',
    'Budget',
    'Regimes alimentaires',
    'Boissons',
    'Services',
    'Recapitulatif',
  ];

  function showStep(step) {
    for (var i = 1; i <= totalSteps; i++) {
      var section = document.getElementById('step-' + i);
      if (section) {
        section.style.display = i === step ? 'block' : 'none';
        section.style.opacity = i === step ? '1' : '0';
      }
    }
    currentStep = step;
    updateProgressBar();
    updateButtons();
    if (step === totalSteps) populateSummary();
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }

  function updateProgressBar() {
    for (var i = 1; i <= totalSteps; i++) {
      var dot = document.getElementById('progress-dot-' + i);
      var label = document.getElementById('progress-label-' + i);
      var connector = document.getElementById('progress-connector-' + i);
      if (dot) {
        dot.classList.toggle('step-dot--done', i < currentStep);
        dot.classList.toggle('step-dot--current', i === currentStep);
      }
      if (label) {
        label.classList.toggle('step-label--current', i === currentStep);
      }
      if (connector) {
        connector.classList.toggle('step-connector--done', i < currentStep);
      }
    }
  }

  function updateButtons() {
    var prevBtn = document.getElementById('btn-prev');
    var nextBtn = document.getElementById('btn-next');
    var submitBtn = document.getElementById('btn-submit');
    if (prevBtn) prevBtn.style.display = currentStep > 1 ? 'inline-flex' : 'none';
    if (nextBtn) nextBtn.style.display = currentStep < totalSteps ? 'inline-flex' : 'none';
    if (submitBtn) submitBtn.style.display = currentStep === totalSteps ? 'inline-flex' : 'none';
  }

  // Applique les classes d'erreur app.css, insère un banner, et scrolle
  // vers le premier champ invalide.
  function clearStepErrors(section) {
    section.querySelectorAll('.wizard-field-error').forEach(function (el) {
      el.classList.remove('wizard-field-error');
    });
    section.querySelectorAll('.wizard-radio-error').forEach(function (el) {
      el.classList.remove('wizard-radio-error');
    });
    var existingBanner = section.querySelector('.wizard-error-banner');
    if (existingBanner) existingBanner.remove();
  }

  function showErrorBanner(section, count) {
    var banner = document.createElement('div');
    banner.className = 'wizard-error-banner';
    var msg = count > 1
      ? 'Veuillez remplir les ' + count + ' champs obligatoires manquants.'
      : 'Veuillez remplir le champ obligatoire manquant.';
    banner.innerHTML =
      '<i data-lucide="alert-circle" class="w-4 h-4"></i>' +
      '<span></span>';
    banner.querySelector('span').textContent = msg;
    section.insertBefore(banner, section.firstChild);
    if (window.lucide) lucide.createIcons();
  }

  function validateStep(step) {
    var section = document.getElementById('step-' + step);
    if (!section) return true;
    clearStepErrors(section);

    var required = section.querySelectorAll('[required]');
    var invalidCount = 0;
    var firstInvalid = null;
    var seenRadioGroups = {};

    required.forEach(function (field) {
      if (field.type === 'radio') {
        // Compter le groupe une seule fois (radios partagent `name`).
        var name = field.name;
        if (seenRadioGroups[name]) return;
        seenRadioGroups[name] = true;

        var checked = section.querySelector('input[name="' + name + '"]:checked');
        if (!checked) {
          var radios = section.querySelectorAll('input[name="' + name + '"]');
          radios.forEach(function (r) {
            var lbl = r.closest('label');
            if (lbl) lbl.classList.add('wizard-radio-error');
          });
          invalidCount++;
          if (!firstInvalid) firstInvalid = radios[0];
        }
      } else if (!field.value.trim()) {
        field.classList.add('wizard-field-error');
        invalidCount++;
        if (!firstInvalid) firstInvalid = field;
      }
    });

    // Groupes "au moins un rempli" (data-required-group) : utilisé au step
    // Budget où budget_global OU budget_per_person doit être renseigné.
    var groups = {};
    section.querySelectorAll('[data-required-group]').forEach(function (el) {
      var g = el.dataset.requiredGroup;
      if (!groups[g]) groups[g] = [];
      groups[g].push(el);
    });
    Object.keys(groups).forEach(function (g) {
      var anyFilled = groups[g].some(function (el) {
        return String(el.value || '').trim();
      });
      if (!anyFilled) {
        groups[g].forEach(function (el) { el.classList.add('wizard-field-error'); });
        invalidCount++;
        if (!firstInvalid) firstInvalid = groups[g][0];
      }
    });

    if (invalidCount > 0) {
      showErrorBanner(section, invalidCount);
      if (firstInvalid && firstInvalid.scrollIntoView) {
        firstInvalid.scrollIntoView({ behavior: 'smooth', block: 'center' });
        try { firstInvalid.focus({ preventScroll: true }); } catch (e) {  }
      }
      return false;
    }
    return true;
  }

  // Auto-clear : enlever le rouge dès que l'utilisateur corrige.
  if (form) {
    form.addEventListener('input', function (ev) {
      var t = ev.target;
      if (!t || !t.classList) return;
      if (t.classList.contains('wizard-field-error') && String(t.value || '').trim()) {
        t.classList.remove('wizard-field-error');
      }
      // Group "au moins un rempli" : un seul input rempli efface tout.
      if (t.dataset && t.dataset.requiredGroup && String(t.value || '').trim()) {
        var g = t.dataset.requiredGroup;
        form.querySelectorAll('[data-required-group="' + g + '"]').forEach(function (el) {
          el.classList.remove('wizard-field-error');
        });
      }
    });
    form.addEventListener('change', function (ev) {
      var t = ev.target;
      if (!t) return;
      if (t.type === 'radio' && t.checked && t.name) {
        var group = form.querySelectorAll('input[name="' + t.name + '"]');
        group.forEach(function (r) {
          var lbl = r.closest('label');
          if (lbl) lbl.classList.remove('wizard-radio-error');
        });
      } else if (t.classList && t.classList.contains('wizard-field-error') && String(t.value || '').trim()) {
        t.classList.remove('wizard-field-error');
      }
    });
  }

  // Step Budget : affiche la fourchette ±5%/±10% sous le champ.
  function formatEur(n) {
    var rounded = Math.round(n * 100) / 100;
    if (Math.abs(rounded - Math.round(rounded)) < 0.005) {
      return String(Math.round(rounded));
    }
    return rounded.toFixed(2);
  }

  function updateBudgetRange() {
    var display = document.getElementById('budget-range-display');
    var text = document.getElementById('budget-range-text');
    if (!display || !text) return;

    var flexEl = form ? form.querySelector('input[name="budget_flexibility"]:checked') : null;
    var flex = flexEl ? flexEl.value : 'exact';
    if (flex !== '5' && flex !== '10') {
      display.classList.add('hidden');
      return;
    }
    var pct = parseInt(flex, 10) / 100;
    var bg = parseFloat(budgetGlobal && budgetGlobal.value);
    var bpp = parseFloat(budgetPerPerson && budgetPerPerson.value);

    var parts = [];
    if (bg > 0) {
      parts.push('Budget global : ' + formatEur(bg * (1 - pct)) + ' € - ' + formatEur(bg * (1 + pct)) + ' €');
    }
    if (bpp > 0) {
      parts.push('Budget par personne : ' + formatEur(bpp * (1 - pct)) + ' € - ' + formatEur(bpp * (1 + pct)) + ' €');
    }
    if (parts.length === 0) {
      display.classList.add('hidden');
      return;
    }
    // VULN-45: pas d'innerHTML, on construit des <div> en textContent.
    text.innerHTML = '';
    parts.forEach(function (line) {
      var div = document.createElement('div');
      div.textContent = line;
      text.appendChild(div);
    });
    display.classList.remove('hidden');
  }

  if (form) {
    form.addEventListener('change', function (ev) {
      if (ev.target && ev.target.name === 'budget_flexibility') {
        updateBudgetRange();
      }
    });
  }
  if (budgetGlobal) budgetGlobal.addEventListener('input', updateBudgetRange);
  if (budgetPerPerson) budgetPerPerson.addEventListener('input', updateBudgetRange);
  updateBudgetRange();

  var prevBtn = document.getElementById('btn-prev');
  var nextBtn = document.getElementById('btn-next');

  if (nextBtn) {
    nextBtn.addEventListener('click', function () {
      if (validateStep(currentStep)) {
        showStep(currentStep + 1);
      }
    });
  }

  if (prevBtn) {
    prevBtn.addEventListener('click', function () {
      if (currentStep > 1) showStep(currentStep - 1);
    });
  }

  // Empêche la soumission implicite (Entrée) avant l'étape finale ;
  // seul le bouton « Soumettre » de l'étape 7 doit déclencher l'envoi.
  if (form) {
    form.addEventListener('submit', function (ev) {
      if (currentStep < totalSteps) {
        ev.preventDefault();
      }
    });

    // En complement : Entree dans un champ ligne-unique avance d'une
    // etape (comme « Suivant », validation incluse) au lieu de ne rien
    // faire. Les <textarea> gardent Entree = retour a la ligne ; les
    // boutons restent cliquables normalement.
    form.addEventListener('keydown', function (ev) {
      if (ev.key !== 'Enter') return;
      var t = ev.target;
      if (!t || !t.tagName) return;
      var isLineField =
        t.tagName === 'SELECT' ||
        (t.tagName === 'INPUT' &&
          t.type !== 'button' &&
          t.type !== 'submit' &&
          t.type !== 'reset');
      if (!isLineField) return;
      ev.preventDefault();
      if (currentStep < totalSteps && nextBtn) {
        nextBtn.click();
      }
    });
  }

  var budgetGlobal = document.getElementById('budget_global');
  var budgetPerPerson = document.getElementById('budget_per_person');
  var guestCount = document.getElementById('guest_count');

  function syncBudgetFromGlobal() {
    var guests = parseInt(guestCount ? guestCount.value : 0);
    var total = parseFloat(budgetGlobal.value);
    if (guests > 0 && total > 0 && budgetPerPerson) {
      budgetPerPerson.value = (total / guests).toFixed(2);
    }
  }

  function syncBudgetFromPerPerson() {
    var guests = parseInt(guestCount ? guestCount.value : 0);
    var pp = parseFloat(budgetPerPerson.value);
    if (guests > 0 && pp > 0 && budgetGlobal) {
      budgetGlobal.value = (pp * guests).toFixed(2);
    }
  }

  if (budgetGlobal) budgetGlobal.addEventListener('input', syncBudgetFromGlobal);
  if (budgetPerPerson) budgetPerPerson.addEventListener('input', syncBudgetFromPerPerson);
  if (guestCount) {
    guestCount.addEventListener('input', function () {
      if (budgetGlobal && budgetGlobal.value) syncBudgetFromGlobal();
    });
  }

  document.querySelectorAll('.dietary-toggle').forEach(function (cb) {
    cb.addEventListener('change', function () {
      var countInput = document.getElementById(cb.dataset.countTarget);
      if (countInput) {
        countInput.closest('.dietary-count-wrapper').style.display = cb.checked ? 'flex' : 'none';
        if (!cb.checked) countInput.value = '';
      }
    });
  });

  var waitstaffCb = document.getElementById('wants_waitstaff');
  var waitstaffDetails = document.getElementById('waitstaff-details-wrapper');
  if (waitstaffCb && waitstaffDetails) {
    waitstaffCb.addEventListener('change', function () {
      waitstaffDetails.style.display = waitstaffCb.checked ? 'block' : 'none';
    });
  }

  // Bascule l'attribut required sur l'horaire en miroir de la checkbox
  // pour que validateStep réagisse en cohérence.
  var setupCb = document.getElementById('wants_setup');
  var setupWrapper = document.getElementById('setup-details-wrapper');
  var setupTime = document.getElementById('service_setup_time');
  function syncSetupRequired() {
    if (!setupCb || !setupWrapper) return;
    if (setupCb.checked) {
      setupWrapper.classList.remove('hidden');
      if (setupTime) setupTime.required = true;
    } else {
      setupWrapper.classList.add('hidden');
      if (setupTime) {
        setupTime.required = false;
        // On garde la saisie pour permettre coche/décoche sans perte.
        setupTime.classList.remove('wizard-field-error');
      }
    }
  }
  if (setupCb) {
    setupCb.addEventListener('change', syncSetupRequired);
    syncSetupRequired();
  }

  var compareModeYes = document.getElementById('is_compare_mode_yes');
  var compareModeNo = document.getElementById('is_compare_mode_no');
  var catererSelect = document.getElementById('caterer-select-wrapper');
  if (compareModeYes && compareModeNo && catererSelect) {
    compareModeYes.addEventListener('change', function () {
      catererSelect.style.display = 'none';
    });
    compareModeNo.addEventListener('change', function () {
      catererSelect.style.display = 'block';
    });
  }

  function populateSummary() {
    // Mirror of MEAL_TYPE_LABELS in models.py — no JSON bridge.
    var mealTypeLabels = {
      petit_dejeuner: 'Petit-déjeuner',
      pause_gourmande: 'Pause gourmande',
      plateaux_repas: 'Plateaux repas',
      cocktail_dinatoire: 'Cocktail dînatoire',
      cocktail_dejeunatoire: 'Cocktail déjeunatoire',
      aperitif: 'Apéritif',
    };

    var flexLabels = {
      exact: 'Exact',
      '5': '+/- 5%',
      '10': '+/- 10%',
    };

    function val(id) {
      var el = document.getElementById(id);
      return el ? el.value : '';
    }

    function radioVal(name) {
      var checked = form.querySelector('input[name="' + name + '"]:checked');
      return checked ? checked.value : '';
    }

    // VULN-45: textContent (not innerHTML) so pre-filled values can't
    // become DOM-based XSS.
    function setHtml(id, text) {
      var el = document.getElementById(id);
      if (el) el.textContent = text;
    }

    var mealType = radioVal('meal_type');
    setHtml('summary-meal-type', mealTypeLabels[mealType] || mealType || '-');
    setHtml('summary-service-type', val('service_type') || '-');
    setHtml('summary-event-date', val('event_date') || '-');
    var startT = val('event_start_time');
    var endT = val('event_end_time');
    var timesText = '-';
    if (startT || endT) {
      timesText = (startT || '?') + ' – ' + (endT || '?');
    }
    setHtml('summary-event-times', timesText);
    setHtml('summary-guest-count', val('guest_count') ? val('guest_count') + ' convives' : '-');
    setHtml('summary-event-address', [val('event_address'), val('event_zip_code'), val('event_city')].filter(Boolean).join(', ') || '-');

    var bg = val('budget_global');
    var bpp = val('budget_per_person');
    var flex = radioVal('budget_flexibility');
    var budgetText = '';
    if (bg) budgetText += bg + ' EUR total';
    if (bpp) budgetText += (budgetText ? ' (' : '') + bpp + ' EUR/pers.' + (budgetText ? ')' : '');
    if (flex) budgetText += ' - ' + (flexLabels[flex] || flex);
    setHtml('summary-budget', budgetText || '-');

    var diets = [];
    var dietaryItems = [
      { id: 'dietary_vegetarian', count: 'vegetarian_count', label: 'Vegetarien' },
      { id: 'dietary_vegan', count: 'vegan_count', label: 'Vegan' },
      { id: 'dietary_halal', count: 'halal_count', label: 'Halal' },
      { id: 'dietary_gluten_free', count: 'gluten_free_count', label: 'Sans gluten' },
      { id: 'dietary_lactose_free', count: 'lactose_free_count', label: 'Sans lactose' },
    ];
    dietaryItems.forEach(function (item) {
      var cb = document.getElementById(item.id);
      if (cb && cb.checked) {
        var count = val(item.count);
        diets.push(item.label + (count ? ' (' + count + ')' : ''));
      }
    });
    setHtml('summary-dietary', diets.length > 0 ? diets.join(', ') : 'Aucun');

    var drinkItems = [];
    document.querySelectorAll('.drink-checkbox:checked').forEach(function (cb) {
      drinkItems.push(cb.dataset.label);
    });
    var drinksText = val('drinks_details');
    setHtml('summary-drinks', (drinkItems.length > 0 ? drinkItems.join(', ') : 'Aucune selection') + (drinksText ? ' - ' + drinksText : ''));

    var services = [];
    document.querySelectorAll('.service-checkbox:checked').forEach(function (cb) {
      services.push(cb.dataset.label);
    });
    setHtml('summary-services', services.length > 0 ? services.join(', ') : 'Aucun');
  }

  showStep(1);
});
