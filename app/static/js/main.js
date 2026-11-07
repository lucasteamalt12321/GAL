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

  function initScalePickers() {
    document.querySelectorAll("[data-scale-picker]").forEach(function (root) {
      var scale = [];
      try {
        scale = JSON.parse(root.getAttribute("data-scale") || "[]");
      } catch (error) {
        return;
      }
      var input = root.querySelector('input[name="harder_count"]');
      var compare = root.querySelector(".scale-compare");
      var question = root.querySelector(".scale-question");
      var result = root.querySelector(".scale-result");
      var reset = root.querySelector("[data-scale-reset]");
      var form = root.closest("form");
      if (!input || !compare || !question || !result) return;

      var lo = 0;
      var hi = scale.length;
      var prefill = (input.value || "").trim();
      if (prefill !== "" && scale.length) {
        var parsed = parseInt(prefill, 10);
        if (!isNaN(parsed) && parsed >= 0 && parsed <= scale.length) {
          lo = parsed;
          hi = parsed;
        }
      }

      function titleAt(index) {
        var item = scale[index];
        if (!item) return "достижение";
        if (item.achievement && item.achievement.title) {
          return item.achievement.title;
        }
        return item.title || "достижение";
      }

      function showResult() {
        input.value = String(lo);
        if (!scale.length) {
          result.textContent = "";
          compare.hidden = true;
          reset.hidden = true;
          return;
        }
        if (lo <= 0) {
          result.textContent = "Сложнее всех достижений в вашей личной шкале.";
        } else if (lo >= scale.length) {
          result.textContent = "Легче всех достижений в вашей личной шкале.";
        } else {
          result.textContent =
            "Сложнее, чем «" + titleAt(lo - 1) + "», но легче, чем «" + titleAt(lo) + "».";
        }
        compare.hidden = true;
        reset.hidden = false;
      }

      function render() {
        if (!scale.length || lo >= hi) {
          showResult();
          return;
        }
        var middle = Math.floor((lo + hi) / 2);
        question.textContent =
          "Новое достижение сложнее или легче, чем «" + titleAt(middle) + "»?";
        compare.hidden = false;
        reset.hidden = true;
        result.textContent = "";
        compare.setAttribute("data-middle", String(middle));
      }

      compare.querySelectorAll("[data-scale-answer]").forEach(function (button) {
        button.addEventListener("click", function () {
          var middle = parseInt(compare.getAttribute("data-middle"), 10);
          if (isNaN(middle)) return;
          if (button.getAttribute("data-scale-answer") === "harder") {
            hi = middle;
          } else {
            lo = middle + 1;
          }
          render();
        });
      });

      if (reset) {
        reset.addEventListener("click", function () {
          lo = 0;
          hi = scale.length;
          input.value = "";
          render();
        });
      }

      if (form) {
        form.addEventListener("submit", function (event) {
          if (scale.length && input.value === "") {
            event.preventDefault();
            window.alert("Ответьте, сложнее или легче достижение в вашей шкале.");
          }
        });
      }

      render();
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    initNav();
    initFileSizeChecks();
    initConfirms();
    initReloadButtons();
    initScalePickers();
  });
})();
