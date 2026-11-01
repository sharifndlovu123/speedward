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
    if (text && hasResults) { setPicking(true); }  // the panel with the message must be on screen
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
    setPicks((f.locations || "").split(",").map(function (n) { return n.trim(); }).filter(Boolean),
             !f.locations);
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

  // --- allowance strip: remaining allowance, countdown, last run ------------------------------
  // All wording comes from the server (the view from /estimate holds the finished text with one
  // {minutes} placeholder). This only counts the reset down in this tab and shows "last run" from the
  // saved result's savedAt. An unknown value stays unknown. Text goes in through textContent.
  var KEY_URL = "https://globalping.io";
  var TICK_MS = 30000;
  var lastView = null;
  var fetchedAt = 0;
  var refetching = false;

  function isNum(n) { return typeof n === "number" && isFinite(n); }

  function secondsLeft() {
    if (!lastView || !isNum(lastView.reset_seconds)) { return null; }
    return Math.max(0, lastView.reset_seconds - Math.floor(Math.max(0, Date.now() - fetchedAt) / 1000));
  }

  function stripText() {
    var v = lastView;
    var secs = secondsLeft();
    if (secs === null) { return v.text_unknown; }
    if (v.text_template.indexOf("{minutes}") === -1) { return v.text_template; }
    if (secs <= 0) { return v.text_now; }
    return v.text_template.split("{minutes}").join(String(Math.ceil(secs / 60)));
  }

  function renderLastRun() {
    var saved = current || readSaved();
    var box = $("allowance-last-run");
    if (!box) { return; }
    if (!saved || !isNum(saved.savedAt)) { show(box, false); return; }
    var minutes = Math.max(0, Math.floor((Date.now() - saved.savedAt) / 60000));
    setText($("allowance-ago"), minutes < 1 ? "just now" : minutes + " min ago");
    show(box, true);
  }

  function renderStrip() {
    var el = $("allowance-text");
    renderLastRun();
    if (!el || !lastView || typeof lastView.text_template !== "string") { return; }
    var text = stripText();
    var link = false;
    if (text.slice(-KEY_URL.length) === KEY_URL) {
      text = text.slice(0, -KEY_URL.length);
      link = true;
    }
    el.textContent = text;
    if (link) {
      var a = document.createElement("a");
      a.href = KEY_URL;
      a.rel = "noopener noreferrer";
      a.textContent = KEY_URL;
      el.appendChild(a);
    }
    $("allowance-strip").classList.toggle("low", lastView.state === "low");
  }

  function markStale(on) { show($("allowance-stale"), on); }

  function renderBar(v) {
    var box = $("allowance-bar");
    if (!box) { return; }
    if (!v || !v.has_bar || !v.tests_per_hour) { show(box, false); return; }
    $("bar-used").style.width = (100 * v.used_before / v.tests_per_hour) + "%";
    $("bar-scan").style.width = (100 * v.this_scan / v.tests_per_hour) + "%";
    setText($("allowance-caption"), v.caption);
    show(box, true);
  }

  function applyView(v) {
    lastView = v || null;
    fetchedAt = Date.now();
    refetching = false;
    markStale(false);
    renderStrip();
    renderBar(lastView);
  }

  function tick() {
    renderStrip();
    var secs = secondsLeft();
    if (secs === 0 && lastView.reset_seconds > 0 && !refetching) {  // the reset time has passed: ask for the new numbers
      refetching = true;
      estimate();
    }
  }

  setInterval(tick, TICK_MS);

  // --- estimate --------------------------------------------------------------------------------
  var lastCost = null;
  var estimateTimer = null;
  var estimateSeq = 0;
  var costStale = true;

  function runAgainLabels() {
    var text = lastCost === null ? "Run again" : "Run again (~" + lastCost + " tests)";
    setText($("banner-run-again"), text);
    setText($("results-run-again"), text);
  }

  function applyBudget(b) {
    applyView(b.view);
    lastCost = b.cost;
    costStale = false;
    renderSummary();
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
    if (!v.locations) {  // never ask for "no places": the server would read that as the default spread
      setText($("estimate"), "");
      refetching = false;
      return Promise.resolve();
    }
    var seq = ++estimateSeq;
    return post("/estimate", v, false).then(function (r) {
      return r.json().then(function (data) {
        if (seq !== estimateSeq) { return; }  // a newer pick superseded this answer
        if (r.ok) {
          applyBudget(data);
        } else {
          setText($("estimate"), data.error || "The test cost could not be estimated.");
          refetching = false;
          markStale(true);
          renderBar(null);
          lastCost = null;
          runAgainLabels();
        }
      });
    }).catch(function () {
      refetching = false;
      markStale(true);
      setText($("estimate"), "The test cost could not be estimated.");
    });
  }

  function estimateSoon() {
    if (estimateTimer) { clearTimeout(estimateTimer); }
    estimateTimer = setTimeout(estimate, 350);
  }

  ["traceroute", "dns"].forEach(function (name) {
    function changed() {
      costStale = true;
      renderSummary();
      estimateSoon();
    }
    form.elements[name].addEventListener("input", changed);
    form.elements[name].addEventListener("change", changed);
  });

  // --- location picker: map dots and chips share one selection ---------------------------------
  // The chips are the source of truth for which places exist (server-rendered from the catalog);
  // added places become extra chips with no dot. The hidden `locations` input carries the
  // comma-separated selection, so /estimate and /scan are unchanged. Text goes in via textContent.
  var ZERO_TEXT = "Pick at least one place";
  var places = [];          // {name, chip, dot, isDefault, isLead}
  var byName = {};
  var selected = {};
  var picksLocked = false;

  function initPicker() {
    var dots = {};
    Array.prototype.forEach.call(document.querySelectorAll("#picker-map .sw-pick"), function (d) {
      dots[d.getAttribute("data-place")] = d;
    });
    Array.prototype.forEach.call(document.querySelectorAll("#chips .chip"), function (chip) {
      var name = chip.getAttribute("data-place");
      var pl = { name: name, chip: chip, dot: dots[name] || null,
                 isDefault: chip.hasAttribute("data-default"), isLead: chip.hasAttribute("data-lead") };
      places.push(pl);
      byName[name] = pl;
    });
  }

  function selectedNames() {
    return places.filter(function (pl) { return selected[pl.name]; }).map(function (pl) { return pl.name; });
  }

  function pluralLocations(n) { return n + (n === 1 ? " location" : " locations"); }

  function renderSummary() {
    var n = selectedNames().length;
    var text = pluralLocations(n);
    if (n > 0 && !costStale && lastCost !== null) { text += " · about " + lastCost + " tests"; }
    setText($("pick-summary"), text);
  }

  function renderPicks() {
    var names = selectedNames();
    places.forEach(function (pl) {
      var on = !!selected[pl.name];
      pl.chip.setAttribute("aria-pressed", on ? "true" : "false");
      if (pl.dot) { pl.dot.setAttribute("aria-checked", on ? "true" : "false"); }
    });
    form.elements.locations.value = names.join(",");
    var toggle = $("locations-toggle");
    setText(toggle, "Locations (" + names.length + ")");
    toggle.setAttribute("aria-label", "Locations: " + names.length + " selected (change and run again)");
    renderSummary();
  }

  function addCustom(name) {
    var lower = name.toLowerCase();
    for (var i = 0; i < places.length; i++) {
      if (places[i].name.toLowerCase() === lower) { return places[i]; }
    }
    var chip = document.createElement("button");
    chip.type = "button";
    chip.className = "chip";
    chip.setAttribute("data-place", name);
    chip.setAttribute("aria-pressed", "false");
    chip.textContent = name;
    var group = $("custom-group");
    group.appendChild(chip);
    show(group, true);
    var pl = { name: name, chip: chip, dot: null, isDefault: false, isLead: false };
    places.push(pl);
    byName[name] = pl;
    return pl;
  }

  function setPicks(names, useDefault) {
    selected = {};
    if (useDefault) {
      places.forEach(function (pl) { if (pl.isDefault) { selected[pl.name] = true; } });
    } else {
      names.forEach(function (n) { selected[addCustom(n).name] = true; });
    }
    renderPicks();
  }

  function picksChanged() {
    renderPicks();
    costStale = true;
    renderSummary();
    if (selectedNames().length === 0) {
      estimateSeq++;  // drop any answer still on its way
      formError(ZERO_TEXT);
      setText($("estimate"), "");
    } else {
      if ($("form-error").textContent === ZERO_TEXT) { formError(""); }
      estimateSoon();
    }
  }

  function toggleName(name) {
    if (picksLocked || !byName[name]) { return; }
    selected[name] = !selected[name];
    picksChanged();
  }

  function applyPreset(kind) {
    if (picksLocked) { return; }
    selected = {};
    places.forEach(function (pl) {
      if (kind === "all" ? !!pl.dot : kind === "default" ? pl.isDefault : kind === "continent" ? pl.isLead : false) {
        selected[pl.name] = true;
      }
    });
    picksChanged();
  }

  function addError(text) {
    setText($("add-error"), text || "");
    show($("add-error"), !!text);
  }

  function addPlace() {
    if (picksLocked) { return; }
    var input = $("add-name");
    var name = input.value.trim();
    if (!name) { addError("Type a place name first."); return; }
    if (name.indexOf(",") !== -1) { addError("Add one place at a time, without commas."); return; }
    if (name.length > 40) { addError("Use 40 characters or fewer."); return; }
    addError("");
    selected[addCustom(name).name] = true;
    input.value = "";
    picksChanged();
  }

  function lockPicks(on) {
    picksLocked = on;
    ["form-panel", "picker-strip"].forEach(function (id) {
      var el = $(id);
      if (on) { el.setAttribute("inert", ""); } else { el.removeAttribute("inert"); }
    });
    var svg = $("picker-map").querySelector("svg");
    if (svg) {
      svg.classList.toggle("sw-scanning", on);
      svg.classList.toggle("sw-locked", on);
    }
    places.forEach(function (pl) {
      if (pl.dot) {
        pl.dot.setAttribute("tabindex", on ? "-1" : "0");
        if (on) { pl.dot.setAttribute("aria-disabled", "true"); } else { pl.dot.removeAttribute("aria-disabled"); }
      }
    });
  }

  initPicker();
  setPicks([], true);

  var pickerMap = $("picker-map");
  pickerMap.addEventListener("click", function (e) {
    var dot = e.target.closest ? e.target.closest(".sw-pick") : null;
    if (dot) { toggleName(dot.getAttribute("data-place")); }
  });
  pickerMap.addEventListener("keydown", function (e) {
    var dot = e.target.closest ? e.target.closest(".sw-pick") : null;
    if (dot && (e.key === "Enter" || e.key === " ")) {
      e.preventDefault();
      toggleName(dot.getAttribute("data-place"));
    }
  });
  $("chips").addEventListener("click", function (e) {
    var chip = e.target.closest ? e.target.closest(".chip") : null;
    if (chip) { toggleName(chip.getAttribute("data-place")); }
  });
  Array.prototype.forEach.call(document.querySelectorAll("[data-preset]"), function (b) {
    b.addEventListener("click", function () { applyPreset(b.getAttribute("data-preset")); });
  });
  $("add-btn").addEventListener("click", addPlace);
  $("add-name").addEventListener("keydown", function (e) {
    if (e.key === "Enter") { e.preventDefault(); addPlace(); }
  });

  // --- layout modes: map in front, panels over it ----------------------------------------------
  var hasResults = false;
  var picking = true;

  function applyMode() {
    var showPicker = !hasResults || picking;
    show($("picker-map"), showPicker);
    show($("results-map"), !showPicker);
    show($("form-panel"), showPicker);
    show($("picker-strip"), showPicker);
    show($("results-panel"), hasResults && !picking);
    show($("results-bar"), hasResults);
    if (showPicker) { setDrawer(false); }  // otherwise the Table button and dots control the drawer
    var t = $("locations-toggle");
    t.setAttribute("aria-pressed", picking ? "true" : "false");
    document.body.classList.toggle("has-results", hasResults);
  }

  function setPicking(on) {
    picking = !!on;
    applyMode();
  }

  var drawerOpener = null;

  function clearSelected() {
    Array.prototype.forEach.call(document.querySelectorAll("#results-map .sw-sel"), function (c) {
      c.classList.remove("sw-sel");
    });
  }

  function setDrawer(on, opener) {
    var d = $("drawer");
    var open = !!(on && hasResults);
    show(d, open);
    $("drawer-toggle").setAttribute("aria-expanded", open ? "true" : "false");
    if (open) {
      if (opener) { drawerOpener = opener; }
      $("drawer-close").focus();
    } else {
      clearSelected();
    }
  }

  // Close the drawer; hand focus back to whatever opened it (the dot or the Table button).
  function closeDrawer(returnFocus) {
    if ($("drawer").hidden) { return; }
    var back = drawerOpener && document.body.contains(drawerOpener) ? drawerOpener : $("drawer-toggle");
    setDrawer(false);
    drawerOpener = null;
    if (returnFocus) { back.focus(); }
  }

  $("locations-toggle").addEventListener("click", function () { setPicking(!picking); });
  $("drawer-toggle").addEventListener("click", function () {
    if ($("drawer").hidden) { setDrawer(true, $("drawer-toggle")); } else { closeDrawer(true); }
  });
  $("drawer-close").addEventListener("click", function () { closeDrawer(true); });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && !$("drawer").hidden) { e.preventDefault(); closeDrawer(true); }
  });
  $("sheet-toggle").addEventListener("click", function () {
    var open = document.body.classList.toggle("sheet-open");
    $("sheet-toggle").setAttribute("aria-expanded", open ? "true" : "false");
    setText($("sheet-toggle"), open ? "Less" : "More");
  });
  Array.prototype.forEach.call(document.querySelectorAll("[data-collapse]"), function (b) {
    b.addEventListener("click", function () {
      var panel = $(b.getAttribute("data-collapse"));
      var collapsed = panel.classList.toggle("collapsed");
      b.setAttribute("aria-expanded", collapsed ? "false" : "true");
      setText(b, collapsed ? "Show" : "Hide");
    });
  });

  // --- results ---------------------------------------------------------------------------------
  var expiryTimer = null;

  function hideResults() {
    hasResults = false;
    picking = true;
    $("results-body").replaceChildren();
    $("results-map").replaceChildren();
    $("results-summary").replaceChildren();
    show($("saved-banner"), false);
    applyMode();
  }

  function showResults(saved) {
    return post("/render", { result: saved.result, limits: saved.limits || {} }, true).then(function (r) {
      if (!r.ok) { throw new Error("render"); }
      return r.text();
    }).then(function (html) {
      var doc = new DOMParser().parseFromString(html, "text/html");
      // The one fragment is split over the layout: map to the background, summary to the side
      // panel, the rest (tables) to the details drawer.
      function take(selector) {
        var nodes = Array.prototype.slice.call(doc.querySelectorAll(selector));
        nodes.forEach(function (n) { n.remove(); });
        return nodes;
      }
      var mapNodes = take("section.map");
      var panelNodes = take(".verdict, .failed-list, #map-detail, .tiles, .legend");
      var rest = doc.querySelector(".fragment") || doc.body;
      var mapBox = $("results-map");
      mapBox.replaceChildren.apply(mapBox, mapNodes);
      var summary = $("results-summary");
      summary.replaceChildren.apply(summary, panelNodes);
      var target = $("results-body");
      target.replaceChildren.apply(target, Array.prototype.slice.call(rest.childNodes));
      setText($("results-title"), hostOf(saved.result.target));
      hasResults = true;
      picking = false;
      applyMode();
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
    renderStrip();
  }

  function clearNow() {
    if (expiryTimer) { clearTimeout(expiryTimer); }
    removeSaved();
    current = null;
    updateGuard();
    hideResults();
    notice("");
    renderStrip();
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
    renderStrip();
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

  // The earlier results are still saved: show them again first, then the error, so the message is
  // placed on screen after the layout has settled (formError opens the panel that holds it).
  function scanFailed(message) {
    var back = current ? showResults(current) : Promise.resolve();
    back.then(function () { formError(message); });
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
        lockPicks(false);
        $("start").disabled = false;
        if (data.status === "done") {
          storeResult(data.result);
          estimate();  // the scan used tests: refresh the strip
        } else {
          scanFailed(data.error || "The scan failed.");
        }
      })
      .catch(function () {
        show($("progress"), false);
        lockPicks(false);
        $("start").disabled = false;
        scanFailed("Lost contact with the server while waiting for the results.");
      });
  }

  form.addEventListener("submit", function (e) {
    e.preventDefault();
    formError("");
    notice(storage ? "" : storageBlockedText);
    var v = formValues();
    if (!v.locations) { formError(ZERO_TEXT); return; }
    if ($("confirm").checked) { v.confirm = "1"; }
    var lim = limitValues();  // checked by the server before any test is spent
    Object.keys(lim).forEach(function (k) { if (lim[k] !== null) { v[k] = String(lim[k]); } });
    $("start").disabled = true;
    post("/scan", v, false).then(function (r) {
      return r.json().then(function (data) {
        if (r.status === 202) {
          show($("saved-banner"), false);
          if (current) { hideResults(); }  // the map in front is the picker again while testing
          lockPicks(true);
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
    clearSelected();
    dot.classList.add("sw-sel");
    setDrawer(true, dot);
    var rows = $("results-body").querySelectorAll("tr[data-row]");
    Array.prototype.forEach.call(rows, function (tr) {
      var on = tr.getAttribute("data-row") === dot.getAttribute("data-row");
      tr.classList.toggle("hl", on);
      if (on) { tr.scrollIntoView({ block: "nearest" }); }
    });
  }

  var resultsBody = $("results-map");
  resultsBody.addEventListener("click", function (e) {
    var dot = dotOf(e.target);
    if (dot) { select(dot); } else { closeDrawer(false); }
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
