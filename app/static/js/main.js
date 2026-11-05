/* GAL — минимальный клиентский JS: меню, проверка размера файла,
   подтверждение необратимых действий. Без фреймворков. */
(function () {
  "use strict";

  function initNav() {
    var toggle = document.querySelector(".nav-toggle");
    var nav = document.getElementById("site-nav");
    if (!toggle || !nav) return;
    toggle.addEventListener("click", function () {
      var open = nav.classList.toggle("nav-open");
      toggle.setAttribute("aria-expanded", open ? "true" : "false");
    });
  }

  function initFileSizeChecks() {
    document.querySelectorAll('input[type="file"][data-max-bytes]').forEach(function (input) {
      input.addEventListener("change", function () {
        var max = parseInt(input.getAttribute("data-max-bytes"), 10);
        var file = input.files && input.files[0];
        if (!file || !max || file.size <= max) return;
        var mib = Math.round(max / (1024 * 1024));
        window.alert("Файл больше " + mib + " МБ. Для крупных доказательств используйте ссылку.");
        input.value = "";
      });
    });
  }

  function initConfirms() {
    document.querySelectorAll("form[data-confirm]").forEach(function (form) {
      form.addEventListener("submit", function (event) {
        var message =
          (event.submitter && event.submitter.getAttribute("data-confirm")) ||
          form.getAttribute("data-confirm");
        if (message && !window.confirm(message)) event.preventDefault();
      });
    });
    document.querySelectorAll("button[data-confirm]").forEach(function (button) {
      button.addEventListener("click", function (event) {
        if (!window.confirm(button.getAttribute("data-confirm"))) {
          event.preventDefault();
        }
      });
    });
  }

  function initReloadButtons() {
    document.querySelectorAll("[data-reload]").forEach(function (button) {
      button.addEventListener("click", function () {
        window.location.reload();
      });
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    initNav();
    initFileSizeChecks();
    initConfirms();
    initReloadButtons();
  });
})();
