/* speedward web UI. Plain script, no build step, no outside requests.
 *
 * Results live only in this tab's sessionStorage (key "speedward:result") with a saved-at time and
 * are dropped after RESULT_TTL_SECONDS (from <body data-ttl>). The server keeps nothing: the page
 * turns the saved JSON into HTML by posting it to /render (stateless) and exports by posting it to
 * /export/<fmt>. Text from results is only ever set with textContent; the one HTML insertion is the
 * escaped fragment returned by our own /render, parsed with DOMParser (CSP blocks inline script).
 */
(function () {
  "use strict";

  var KEY = "speedward:result";
  var body = document.body;
  var TTL_MS = (parseInt(body.getAttribute("data-ttl"), 10) || 900) * 1000;
  var csrfMeta = document.querySelector('meta[name="csrf-token"]');
  var CSRF = csrfMeta ? csrfMeta.getAttribute("content") : "";
  var POLL_MS = 1000;

  function $(id) { return document.getElementById(id); }
  var form = $("scan-form");
  if (!form) { return; }

  // --- storage ---------------------------------------------------------------------------------
  var storage = null;
  try {
    storage = window.sessionStorage;
    storage.setItem("speedward:probe", "1");
    storage.removeItem("speedward:probe");
  } catch (e) {
    storage = null;
  }

  function readSaved() {
    if (!storage) { return null; }
    try {
      var raw = storage.getItem(KEY);
      if (!raw) { return null; }
      var saved = JSON.parse(raw);
      if (!saved || typeof saved.savedAt !== "number" || !saved.result || typeof saved.result !== "object") {
        storage.removeItem(KEY);
        return null;
      }
      return saved;
    } catch (e) {
      removeSaved();
      return null;
    }
  }

  function writeSaved(saved) {
    if (!storage) { return false; }
    try {
      storage.setItem(KEY, JSON.stringify(saved));
      return true;
    } catch (e) {
      return false;
    }
  }

  function removeSaved() {
    if (!storage) { return; }
    try { storage.removeItem(KEY); } catch (e) { /* nothing to do */ }
  }

  var storageBlockedText = "Results cannot be kept in this window: the browser blocks session storage here " +
    "(some private windows do). They will be lost on refresh, so export them to keep them.";

  // --- small helpers ---------------------------------------------------------------------------
  function show(el, on) { if (el) { el.hidden = !on; } }

  function setText(el, text) { if (el) { el.textContent = text; } }

  function notice(text) {
    var el = $("notice");
    setText(el, text || "");
    show(el, !!text);
  }

  function formError(text) {
    var el = $("form-error");
    setText(el, text || "");
    show(el, !!text);
  }

  function post(url, data, asJson) {
    var headers = { "X-CSRFToken": CSRF };
    var payload;
    if (asJson) {
      headers["Content-Type"] = "application/json";
      payload = JSON.stringify(data);
    } else {
      payload = new URLSearchParams(data);
    }
    // The page sends Referrer-Policy: no-referrer. Under that policy a browser may send "Origin: null"
    // on POST (siteward saw this with form posts), which Django's CSRF check rejects. A same-origin
    // policy on these requests keeps a real Origin header, and only for our own server. (Chromium's
    // fetch was seen to pass either way; this is for the other browsers.)
    return fetch(url, {
      method: "POST", headers: headers, body: payload, credentials: "same-origin",
      referrerPolicy: "same-origin", cache: "no-store"
    });
  }

  function hostOf(target) {
    try { return new URL(target).host; } catch (e) { return String(target || ""); }
  }

  function formValues() {
    var v = {
      url: form.elements.url.value.trim(),
      locations: form.elements.locations.value.trim()
    };
    if (form.elements.traceroute.checked) { v.traceroute = "on"; }
    if (form.elements.dns.checked) { v.dns = "on"; }
    return v;
  }

  function limitValues() {
    function num(name) {
      var raw = form.elements[name].value.trim();
      if (raw === "") { return null; }
      var n = Number(raw);
      return isFinite(n) ? n : raw;
    }
    return { max_ttfb: num("max_ttfb"), max_load: num("max_load"), max_blocked: num("max_blocked") };
  }

  function fillForm(saved) {
    var f = saved.form || {};
    form.elements.url.value = f.url || saved.result.target || "";
    form.elements.locations.value = f.locations || "";
    form.elements.traceroute.checked = !!f.traceroute;
    form.elements.dns.checked = !!f.dns;
    var l = saved.limits || {};
    ["max_ttfb", "max_load", "max_blocked"].forEach(function (name) {
      form.elements[name].value = (l[name] === null || l[name] === undefined) ? "" : String(l[name]);
    });
  }

  // --- unsaved-export guard --------------------------------------------------------------------
  var current = null;

  function onBeforeUnload(e) {
    e.preventDefault();
    e.returnValue = "";
    return "";
  }
  var guardOn = false;

  function updateGuard() {
    var want = !!(current && !current.exported);
    if (want && !guardOn) { window.addEventListener("beforeunload", onBeforeUnload); }
    if (!want && guardOn) { window.removeEventListener("beforeunload", onBeforeUnload); }
    guardOn = want;
  }

  // --- estimate --------------------------------------------------------------------------------
  var lastCost = null;
  var estimateTimer = null;

  function runAgainLabels() {
    var text = lastCost === null ? "Run again" : "Run again (~" + lastCost + " tests)";
    setText($("banner-run-again"), text);
    setText($("results-run-again"), text);
  }

  function applyBudget(b) {
    lastCost = b.cost;
    runAgainLabels();
    setText($("estimate"), b.message || "");
    var over = $("over");
    if (b.refused) {
      setText(over, b.refused);
      show(over, true);
      show($("confirm-wrap"), false);
      $("start").disabled = true;
    } else if (!b.fits) {
      setText(over, b.reason);
      show(over, true);
      show($("confirm-wrap"), true);
      $("start").disabled = false;
    } else {
      show(over, false);
      show($("confirm-wrap"), false);
      $("confirm").checked = false;
      $("start").disabled = false;
    }
  }

  function estimate() {
    var v = formValues();
    delete v.url;
    return post("/estimate", v, false).then(function (r) {
      return r.json().then(function (data) {
        if (r.ok) {
          applyBudget(data);
        } else {
          setText($("estimate"), data.error || "The test cost could not be estimated.");
          lastCost = null;
          runAgainLabels();
        }
      });
    }).catch(function () {
      setText($("estimate"), "The test cost could not be estimated.");
    });
  }

  function estimateSoon() {
    if (estimateTimer) { clearTimeout(estimateTimer); }
    estimateTimer = setTimeout(estimate, 350);
  }

  ["locations", "traceroute", "dns"].forEach(function (name) {
    form.elements[name].addEventListener("input", estimateSoon);
    form.elements[name].addEventListener("change", estimateSoon);
  });

  // --- results ---------------------------------------------------------------------------------
  var expiryTimer = null;

  function hideResults() {
    show($("results"), false);
    $("results-body").replaceChildren();
    show($("saved-banner"), false);
  }

  function showResults(saved) {
    return post("/render", { result: saved.result, limits: saved.limits || {} }, true).then(function (r) {
      if (!r.ok) { throw new Error("render"); }
      return r.text();
    }).then(function (html) {
      var doc = new DOMParser().parseFromString(html, "text/html");
      var target = $("results-body");
      target.replaceChildren.apply(target, Array.prototype.slice.call(doc.body.childNodes));
      setText($("results-title"), hostOf(saved.result.target));
      show($("results"), true);
    }).catch(function () {
      removeSaved();
      current = null;
      updateGuard();
      hideResults();
      notice("The saved results could not be shown and were removed.");
    });
  }

  function scheduleExpiry(savedAt) {
    if (expiryTimer) { clearTimeout(expiryTimer); }
    expiryTimer = setTimeout(expire, Math.max(0, savedAt + TTL_MS - Date.now()));
  }

  function expiredText() {
    var minutes = Math.round(TTL_MS / 60000);
    return "Saved results expired after " + minutes + " minute" + (minutes === 1 ? "" : "s") + " and were removed.";
  }

  function expire() {
    removeSaved();
    current = null;
    updateGuard();
    hideResults();
    notice(expiredText());
  }

  function clearNow() {
    if (expiryTimer) { clearTimeout(expiryTimer); }
    removeSaved();
    current = null;
    updateGuard();
    hideResults();
    notice("");
  }

  function storeResult(result) {
    current = {
      v: 1, savedAt: Date.now(), result: result, limits: limitValues(), form: formValues(), exported: false
    };
    if (!writeSaved(current)) {
      notice(storage ? "These results could not be kept in this tab (storage is full). Export them now to keep them."
                     : storageBlockedText);
    }
    updateGuard();
    scheduleExpiry(current.savedAt);
    show($("saved-banner"), false);
    return showResults(current);
  }

  // --- scanning --------------------------------------------------------------------------------
  function progress(lines) {
    var ul = $("progress-lines");
    ul.replaceChildren();
    (lines || []).forEach(function (line) {
      var li = document.createElement("li");
      li.textContent = line;
      ul.appendChild(li);
    });
  }

  function poll(id) {
    fetch("/scan/" + encodeURIComponent(id) + "/status", { cache: "no-store", credentials: "same-origin" })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.status === "running") {
          progress(data.progress);
          setTimeout(function () { poll(id); }, POLL_MS);
          return;
        }
        show($("progress"), false);
        $("start").disabled = false;
        if (data.status === "done") {
          storeResult(data.result);
        } else {
          formError(data.error || "The scan failed.");
        }
      })
      .catch(function () {
        show($("progress"), false);
        $("start").disabled = false;
        formError("Lost contact with the server while waiting for the results.");
      });
  }

  form.addEventListener("submit", function (e) {
    e.preventDefault();
    formError("");
    notice(storage ? "" : storageBlockedText);
    var v = formValues();
    if ($("confirm").checked) { v.confirm = "1"; }
    var lim = limitValues();  // checked by the server before any test is spent
    Object.keys(lim).forEach(function (k) { if (lim[k] !== null) { v[k] = String(lim[k]); } });
    $("start").disabled = true;
    post("/scan", v, false).then(function (r) {
      return r.json().then(function (data) {
        if (r.status === 202) {
          show($("saved-banner"), false);
          progress(["Starting"]);
          show($("progress"), true);
          poll(data.id);
          return;
        }
        $("start").disabled = false;
        if (r.status === 409 && data.needs_confirm) {
          applyBudget(data);
          formError("This scan needs more tests than are left this hour. Tick the box to go ahead anyway.");
        } else {
          if (data.cost !== undefined) { applyBudget(data); }
          formError(data.error || "The scan could not start.");
        }
      });
    }).catch(function () {
      $("start").disabled = false;
      formError("The server could not be reached.");
    });
  });

  function runAgain() {
    window.scrollTo(0, 0);
    if (typeof form.requestSubmit === "function") { form.requestSubmit(); } else { form.dispatchEvent(new Event("submit")); }
  }

  $("banner-run-again").addEventListener("click", runAgain);
  $("results-run-again").addEventListener("click", runAgain);
  $("banner-clear").addEventListener("click", clearNow);
  $("results-clear").addEventListener("click", clearNow);

  // --- export ----------------------------------------------------------------------------------
  Array.prototype.forEach.call(document.querySelectorAll("[data-export]"), function (button) {
    button.addEventListener("click", function () {
      if (!current) { return; }
      var fmt = button.getAttribute("data-export");
      post("/export/" + fmt, { result: current.result }, true).then(function (r) {
        if (!r.ok) { throw new Error("export"); }
        return r.blob();
      }).then(function (blob) {
        var a = document.createElement("a");
        var href = URL.createObjectURL(blob);
        a.href = href;
        a.download = "speedward-report." + fmt;
        document.body.appendChild(a);
        a.click();
        a.remove();
        setTimeout(function () { URL.revokeObjectURL(href); }, 10000);
        current.exported = true;
        writeSaved(current);
        updateGuard();
      }).catch(function () {
        notice("The export could not be made.");
      });
    });
  });

  // --- map: dots select their table row ---------------------------------------------------------
  function dotOf(target) {
    return target && target.closest ? target.closest("circle[data-row]") : null;
  }

  function describe(dot) {
    var title = dot.querySelector("title");
    setText($("map-detail"), title ? title.textContent : "");
  }

  function select(dot) {
    describe(dot);
    var rows = $("results-body").querySelectorAll("tr[data-row]");
    Array.prototype.forEach.call(rows, function (tr) {
      var on = tr.getAttribute("data-row") === dot.getAttribute("data-row");
      tr.classList.toggle("hl", on);
      if (on) { tr.scrollIntoView({ block: "nearest" }); }
    });
  }

  var resultsBody = $("results-body");
  resultsBody.addEventListener("click", function (e) {
    var dot = dotOf(e.target);
    if (dot) { select(dot); }
  });
  resultsBody.addEventListener("keydown", function (e) {
    var dot = dotOf(e.target);
    if (dot && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); select(dot); }
  });
  resultsBody.addEventListener("focusin", function (e) {
    var dot = dotOf(e.target);
    if (dot) { describe(dot); }
  });
  resultsBody.addEventListener("mouseover", function (e) {
    var dot = dotOf(e.target);
    if (dot) { describe(dot); }
  });

  // --- start -----------------------------------------------------------------------------------
  function start() {
    if (!storage) {
      notice(storageBlockedText);
      estimate();
      return;
    }
    var saved = readSaved();
    if (saved) {
      var age = Date.now() - saved.savedAt;
      if (age >= TTL_MS || age < -60000) {
        removeSaved();
        notice(expiredText());
      } else {
        current = saved;
        fillForm(saved);
        var minutes = Math.max(0, Math.floor(age / 60000));
        setText($("saved-text"), "Showing results for " + hostOf(saved.result.target) + " from " + minutes +
          " minute" + (minutes === 1 ? "" : "s") + " ago. Kept until you close this tab. No tests were used.");
        show($("saved-banner"), true);
        updateGuard();
        scheduleExpiry(saved.savedAt);
        showResults(saved);
      }
    }
    estimate();
  }

  start();
}());
