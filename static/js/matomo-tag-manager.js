// Matomo Tag Manager bootstrap.
// Externalisé pour rester compatible avec la CSP `script-src 'self'`.
// Le tag <script> qui charge ce fichier doit porter
// data-matomo-container-url="<URL container Matomo>". L'attribut est
// peuplé côté serveur par config.MATOMO_TAG_MANAGER_URL ; si la var
// n'est pas définie, le template n'émet pas le script et ce code n'est
// jamais évalué. document.currentScript est null avec `defer`, d'où le
// querySelector ciblé.
(function () {
  var script = document.querySelector("script[data-matomo-container-url]");
  var containerUrl = script && script.dataset.matomoContainerUrl;
  if (!containerUrl) return;

  var _mtm = (window._mtm = window._mtm || []);
  _mtm.push({ "mtm.startTime": new Date().getTime(), event: "mtm.Start" });

  var d = document;
  var g = d.createElement("script");
  var s = d.getElementsByTagName("script")[0];
  g.async = true;
  g.src = containerUrl;
  s.parentNode.insertBefore(g, s);
})();
