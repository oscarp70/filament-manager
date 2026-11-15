(function () {
  "use strict";

  // ---- Thema (licht/donker) ----
  var toggle = document.getElementById("theme-toggle");
  if (toggle) {
    toggle.addEventListener("click", function () {
      var next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
      document.documentElement.dataset.theme = next;
      try { localStorage.setItem("fm-theme", next); } catch (e) {}
    });
  }

  // ---- Bevestigingsdialoog ----
  var dialog = document.getElementById("confirm-dialog");
  var dialogText = document.getElementById("confirm-text");

  function askConfirm(question, onOk) {
    if (!dialog || typeof dialog.showModal !== "function") {
      if (window.confirm(question)) onOk();
      return;
    }
    dialogText.textContent = question;
    dialog.returnValue = "";
    dialog.addEventListener("close", function handler() {
      dialog.removeEventListener("close", handler);
      if (dialog.returnValue === "ok") onOk();
    });
    dialog.showModal();
  }

  // HTMX-verzoeken met hx-confirm
  document.addEventListener("htmx:confirm", function (evt) {
    if (!evt.detail.question) return;
    evt.preventDefault();
    askConfirm(evt.detail.question, function () { evt.detail.issueRequest(true); });
  });

  // Gewone formulieren met data-confirm (werkt ook zonder HTMX)
  document.addEventListener("submit", function (evt) {
    var form = evt.target;
    if (!form.dataset || !form.dataset.confirm || form.dataset.confirmed) return;
    evt.preventDefault();
    askConfirm(form.dataset.confirm, function () {
      form.dataset.confirmed = "1";
      form.submit();
    });
  });

  // ---- Kleur: color picker, hex en RGB synchroon houden ----
  var picker = document.getElementById("hex-picker");
  var hexText = document.getElementById("hex-text");
  var rIn = document.getElementById("r");
  var gIn = document.getElementById("g");
  var bIn = document.getElementById("b");

  function toHex(n) {
    n = Math.max(0, Math.min(255, parseInt(n, 10) || 0));
    return n.toString(16).padStart(2, "0");
  }
  function fromRgb() {
    return "#" + toHex(rIn.value) + toHex(gIn.value) + toHex(bIn.value);
  }
  function setRgb(hex) {
    var m = /^#?([0-9a-f]{6})$/i.exec(hex.trim());
    if (!m) return false;
    var h = m[1];
    rIn.value = parseInt(h.slice(0, 2), 16);
    gIn.value = parseInt(h.slice(2, 4), 16);
    bIn.value = parseInt(h.slice(4, 6), 16);
    return true;
  }
  function notifyPreview() {
    document.body.dispatchEvent(new Event("colorsync"));
  }

  if (picker && hexText && rIn && gIn && bIn) {
    picker.addEventListener("input", function () {
      setRgb(picker.value);
      hexText.value = picker.value.toUpperCase();
      hexText.removeAttribute("aria-invalid");
    });
    hexText.addEventListener("input", function () {
      var v = hexText.value.trim();
      if (v && v[0] !== "#") v = "#" + v;
      if (setRgb(v)) {
        picker.value = v.toLowerCase();
        hexText.removeAttribute("aria-invalid");
      } else {
        hexText.setAttribute("aria-invalid", "true");
      }
    });
    hexText.addEventListener("blur", function () {
      hexText.value = fromRgb().toUpperCase();
      hexText.removeAttribute("aria-invalid");
    });
    [rIn, gIn, bIn].forEach(function (el) {
      el.addEventListener("input", function () {
        var hex = fromRgb();
        picker.value = hex;
        hexText.value = hex.toUpperCase();
      });
      el.addEventListener("blur", function () {
        el.value = Math.max(0, Math.min(255, parseInt(el.value, 10) || 0));
      });
    });

    document.querySelectorAll(".palette-swatch").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var hex = btn.dataset.hex;
        picker.value = hex;
        hexText.value = hex.toUpperCase();
        setRgb(hex);
        var name = document.getElementById("color_name");
        if (name && !name.value.trim()) name.value = btn.dataset.name;
        notifyPreview();
      });
    });
  }

  // ---- Gewicht nieuw aanpassen: gewicht nu meenemen bij een nieuwe spoel ----
  var wNew = document.getElementById("weight_new");
  var wNow = document.getElementById("weight_current");
  if (wNew && wNow) {
    var lastNew = wNew.value;
    wNew.addEventListener("input", function () {
      if (wNow.value === lastNew) wNow.value = wNew.value; // nog vol: synchroon houden
      wNow.max = wNew.value;
      lastNew = wNew.value;
    });
  }

  // ---- Zojuist opgeslagen kaart in beeld brengen ----
  var hl = document.querySelector(".card-highlight");
  if (hl && hl.scrollIntoView) hl.scrollIntoView({ block: "center", behavior: "smooth" });
  if (window.location.search.indexOf("saved=") !== -1 && window.history.replaceState) {
    var url = new URL(window.location.href);
    url.searchParams.delete("saved");
    window.history.replaceState({}, "", url.pathname + (url.search || ""));
  }
})();
