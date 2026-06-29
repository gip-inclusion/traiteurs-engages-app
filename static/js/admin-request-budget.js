// Interdépendance budget global ↔ budget par personne sur le formulaire
// d'édition admin d'une demande, calée sur le wizard client
// (static/js/wizard.js) : modifier l'un recalcule l'autre via le nombre
// de convives.
(function () {
  var budgetGlobal = document.getElementById("budget_global");
  var budgetPerPerson = document.getElementById("budget_per_person");
  var guestCount = document.getElementById("guest_count");
  if (!budgetGlobal || !budgetPerPerson) return;

  function guests() {
    return parseInt(guestCount ? guestCount.value : "0", 10) || 0;
  }

  function syncFromGlobal() {
    var g = guests();
    var total = parseFloat(budgetGlobal.value);
    if (g > 0 && total > 0) {
      budgetPerPerson.value = (total / g).toFixed(2);
    }
  }

  function syncFromPerPerson() {
    var g = guests();
    var pp = parseFloat(budgetPerPerson.value);
    if (g > 0 && pp > 0) {
      budgetGlobal.value = (pp * g).toFixed(2);
    }
  }

  budgetGlobal.addEventListener("input", syncFromGlobal);
  budgetPerPerson.addEventListener("input", syncFromPerPerson);
  if (guestCount) {
    guestCount.addEventListener("input", function () {
      if (budgetGlobal.value) {
        syncFromGlobal();
      } else if (budgetPerPerson.value) {
        syncFromPerPerson();
      }
    });
  }
})();
