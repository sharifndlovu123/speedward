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
    if (text && el.scrollIntoView) { el.scrollIntoView({ block: "nearest" }); }  // short windows scroll the column
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
    var checks = checkBoxes().filter(function (b) { return b.checked; }).map(function (b) { return b.value; });
    if (checks.length) { v.checks = checks.join(","); }
    return v;
  }

  // The "More checks" boxes (name="check"); their cost comes from /estimate like everything else.
  function checkBoxes() {
    return Array.prototype.slice.call(form.querySelectorAll('input[name="check"]'));
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
    var ticked = String(f.checks || "").split(",");
    checkBoxes().forEach(function (b) { b.checked = ticked.indexOf(b.value) !== -1; });
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
  var costStale = true;  // true until /estimate has answered for the current picks and options
  // Mirrored on the Start button (data-cost="ready" or "stale") so tests can wait for a fresh cost.
  function setStale(on) {
    costStale = on;
    if (on) { show($("mc-total"), false); }
    $("start").setAttribute("data-cost", on ? "stale" : "ready");
  }
  setStale(true);
  var estimateFailed = false;
  // The cost is shown before every scan (a hard rule): Start is refused while it is stale or failed.
  var COST_WAIT = "Wait for the test cost to be shown, then press Start again. A scan never starts before its cost is known.";
  var COST_FAILED = "The test cost could not be estimated, so the scan was not started. Trying again: press Start once the cost is shown.";

  function costMessageShown() {
    var t = $("form-error").textContent;
    return t === COST_WAIT || t === COST_FAILED;
  }

  function runAgainLabels() {
    var text = lastCost === null ? "Run again" : "Run again (~" + lastCost + " tests)";
    setText($("banner-run-again"), text);
    setText($("results-run-again"), text);
  }

  function applyBudget(b) {
    applyView(b.view);
    lastCost = b.cost;
    setStale(false);
    estimateFailed = false;
    if (costMessageShown()) { formError(""); }
    renderSummary();
    runAgainLabels();
    setText($("estimate"), b.message || "");
    setText($("mc-on"), b.checks_line || "");
    setText($("mc-breakdown"), b.breakdown || "");
    show($("mc-total"), !!b.breakdown);
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

  // What the address is, never the address itself: the estimate leaves out the checks it cannot use
  // (HTTP/2 on a plain http:// address; IPv6, DNS resolvers and the DNS trace on an IP address).
  function addressFlags() {
    var text = form.elements.url.value.trim();
    var out = { plain: false, ip: false };
    if (!text) { return out; }
    try {
      var u = new URL(text.indexOf("://") === -1 ? "https://" + text : text);
      var host = u.hostname;
      out.plain = u.protocol === "http:";
      out.ip = /^[\d.]+$/.test(host) || host.indexOf(":") !== -1 || host.charAt(0) === "[";
    } catch (e) { /* not an address yet: charge every check */ }
    return out;
  }

  function estimate() {
    var v = formValues();
    delete v.url;
    var flags = addressFlags();
    if (flags.plain) { v.plain = "1"; }
    if (flags.ip) { v.ip = "1"; }
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
          setStale(true);
          estimateFailed = true;
          renderSummary();
          setText($("estimate"), data.error || "The test cost could not be estimated.");
          refetching = false;
          markStale(true);
          renderBar(null);
          lastCost = null;
          runAgainLabels();
        }
      });
    }).catch(function () {
      if (seq !== estimateSeq) { return; }
      setStale(true);
      estimateFailed = true;
      renderSummary();
      refetching = false;
      markStale(true);
      setText($("estimate"), "The test cost could not be estimated.");
    });
  }

  function estimateSoon() {
    if (estimateTimer) { clearTimeout(estimateTimer); }
    estimateTimer = setTimeout(function () { estimateTimer = null; estimate(); }, 350);
  }

  ["traceroute", "dns"].forEach(function (name) {
    function changed() {
      setStale(true);
      estimateFailed = false;
      renderSummary();
      estimateSoon();
    }
    form.elements[name].addEventListener("input", changed);
    form.elements[name].addEventListener("change", changed);
  });
  // Only a change in what the address is (plain http, IP address) can change the cost.
  var lastFlags = "";
  form.elements.url.addEventListener("input", function () {
    var f = addressFlags(), key = (f.plain ? "p" : "") + (f.ip ? "i" : "");
    if (key === lastFlags) { return; }
    lastFlags = key;
    setStale(true);
    estimateFailed = false;
    renderSummary();
    estimateSoon();
  });

  checkBoxes().forEach(function (b) {
    b.addEventListener("change", function () {
      setStale(true);
      estimateFailed = false;
      renderSummary();
      estimateSoon();
    });
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
    if (previewEl) { renderPreview(); }
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
    setStale(true);
    estimateFailed = false;
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
    colFocus();
    var svg = $("picker-map").querySelector("svg");
    if (svg) {
      svg.classList.toggle("sw-scanning", on);
      svg.classList.toggle("sw-locked", on);
    }
    if (pinEl) {
      pinEl.setAttribute("tabindex", on ? "-1" : "0");
      if (on) { pinEl.setAttribute("aria-disabled", "true"); } else { pinEl.removeAttribute("aria-disabled"); }
    }
    places.forEach(function (pl) {
      if (pl.dot) {
        pl.dot.setAttribute("tabindex", on ? "-1" : "0");
        if (on) { pl.dot.setAttribute("aria-disabled", "true"); } else { pl.dot.removeAttribute("aria-disabled"); }
      }
    });
  }

  // --- server pin: where the tested server is, so lines can draw from it -------------------------
  // Two numbers kept in this tab only (sessionStorage "speedward:pin", same expiry as results) and
  // posted with /scan as server_lat/server_lon. A configured position or geo-IP result still wins on
  // the server. Preview lines are SVG built here from the page's own dots; no outside data.
  var PIN_KEY = "speedward:pin";
  var SVGNS = "http://www.w3.org/2000/svg";
  var pinBox = $("picker-map");
  var pinSvg = pinBox.querySelector("svg");
  var pinEl = pinBox.querySelector(".sw-pin");
  var previewEl = pinBox.querySelector(".sw-preview");
  var pinHome = (pinEl.getAttribute("data-home") || "0,0").split(",").map(Number);
  var vb = pinSvg.viewBox.baseVal;
  var pin = null;           // {lat, lon} or null
  var pinTimer = null;
  var dragging = false;
  var moved = false;        // a click or tap that never moves must not place the pin
  var downAt = null;
  var DRAG_PX = 4;
  // The unset pin rests here; the first arrow key starts from this spot.
  var pinRest = { lat: 0, lon: 165 };  // same spot as data-home (see render_picker_map)

  function clampNum(n, lo, hi) { return Math.min(hi, Math.max(lo, n)); }
  function pinOk(p) {
    return !!p && typeof p.lat === "number" && typeof p.lon === "number" && isFinite(p.lat) && isFinite(p.lon) &&
      p.lat >= -90 && p.lat <= 90 && p.lon >= -180 && p.lon <= 180;
  }
  function toX(lon) { return (lon + 180) / 360 * vb.width; }
  function toY(lat) { return (90 - lat) / 180 * vb.height; }
  function round4(n) { return Math.round(n * 10000) / 10000; }

  function readPin() {
    if (!storage) { return null; }
    try {
      var raw = storage.getItem(PIN_KEY);
      if (!raw) { return null; }
      var saved = JSON.parse(raw);
      if (!saved || typeof saved.savedAt !== "number" || !pinOk(saved) || Date.now() - saved.savedAt > TTL_MS) {
        storage.removeItem(PIN_KEY);
        return null;
      }
      return { lat: saved.lat, lon: saved.lon, savedAt: saved.savedAt };
    } catch (e) {
      try { storage.removeItem(PIN_KEY); } catch (e2) { /* nothing to do */ }
      return null;
    }
  }

  function savePin() {
    if (pinTimer) { clearTimeout(pinTimer); pinTimer = null; }
    if (!storage) { return; }
    try {
      if (pin) {
        storage.setItem(PIN_KEY, JSON.stringify({ lat: pin.lat, lon: pin.lon, savedAt: Date.now() }));
        pinTimer = setTimeout(function () { clearPin(); }, TTL_MS);
      } else {
        storage.removeItem(PIN_KEY);
      }
    } catch (e) { /* kept for this page view only */ }
  }

  function arcPath(sx, sy, x, y) {
    var dist = Math.sqrt((x - sx) * (x - sx) + (y - sy) * (y - sy));
    return "M" + sx + "," + sy + " Q" + (sx + x) / 2 + "," + ((sy + y) / 2 - dist * 0.25) + " " + x + "," + y;
  }

  function renderPreview() {
    previewEl.replaceChildren();
    if (!pin) { return; }
    var sx = toX(pin.lon), sy = toY(pin.lat);
    places.forEach(function (pl) {
      if (!selected[pl.name] || !pl.dot) { return; }
      var path = document.createElementNS(SVGNS, "path");
      path.setAttribute("d", arcPath(sx, sy, Number(pl.dot.getAttribute("cx")), Number(pl.dot.getAttribute("cy"))));
      previewEl.appendChild(path);
    });
  }

  function deg(n, pos, neg) { return Math.abs(n).toFixed(2) + "° " + (n >= 0 ? pos : neg); }

  function dir(n, pos, neg) { return Math.abs(n).toFixed(2) + " " + (n >= 0 ? pos : neg); }

  function renderPin() {
    var x = pin ? toX(pin.lon) : pinHome[0];
    var y = pin ? toY(pin.lat) : pinHome[1];
    pinEl.setAttribute("transform", "translate(" + x + "," + y + ")");
    pinEl.style.opacity = pin ? "1" : "0.7";
    pinEl.classList.toggle("sw-unset", !pin);
    var label = pinEl.querySelector("text");  // flips to the left near the right edge so it never clips
    var flip = x > vb.width * 0.88;
    label.textContent = pin ? "Server" : "Server (drag me)";
    label.setAttribute("text-anchor", flip ? "end" : "start");
    label.setAttribute("x", String((flip ? -15 : 15) * vb.width / 1000));
    pinEl.setAttribute("aria-label", "Server position pin, " + pinWhere());
    // Edge label: the pin's own position, only while one is set (never a guessed place).
    var coords = $("edge-coords");
    setText(coords, pin ? "Server pin · " + deg(pin.lat, "N", "S") + " · " + deg(pin.lon, "E", "W") : "");
    show(coords, !!pin && !$("picker-map").hidden);
    var sel = $("server-city");
    var value = "";
    if (pin) {
      value = "custom";
      Array.prototype.forEach.call(sel.options, function (o) {
        if (o.hasAttribute("data-lat") && Math.abs(Number(o.getAttribute("data-lat")) - pin.lat) < 0.01 &&
            Math.abs(Number(o.getAttribute("data-lon")) - pin.lon) < 0.01) { value = o.value; }
      });
    }
    sel.value = value;
    renderPreview();
  }

  // Announced only on drop, key presses and the city list, never while dragging.
  function announcePin() {
    setText($("pin-status"), pin ? "Server pin: " + pinWhere() + ". Kept in this tab only." : "Server pin removed.");
  }

  function pinWhere() { return pin ? dir(pin.lat, "north", "south") + ", " + dir(pin.lon, "east", "west") : "not set"; }

  function setPin(lat, lon, persist) {
    var p = { lat: round4(clampNum(lat, -90, 90)), lon: round4(clampNum(lon, -180, 180)) };
    if (!pinOk(p)) { return; }
    pin = p;
    renderPin();
    if (persist) { savePin(); }
  }

  function clearPin() {
    pin = null;
    savePin();
    renderPin();
  }

  function pinPoint(e) {
    var pt = pinSvg.createSVGPoint();
    pt.x = e.clientX;
    pt.y = e.clientY;
    var m = pinSvg.getScreenCTM();
    return m ? pt.matrixTransform(m.inverse()) : null;
  }

  function dragTo(e) {
    var pt = pinPoint(e);
    if (!pt) { return; }
    setPin(90 - clampNum(pt.y, 0, vb.height) / vb.height * 180, clampNum(pt.x, 0, vb.width) / vb.width * 360 - 180, false);
  }

  pinEl.addEventListener("pointerdown", function (e) {
    if (picksLocked || (e.button !== undefined && e.button !== 0)) { return; }
    e.preventDefault();
    e.stopPropagation();
    dragging = true;
    moved = false;
    downAt = { x: e.clientX, y: e.clientY };
    pinEl.classList.add("sw-drag");
    try { pinEl.setPointerCapture(e.pointerId); } catch (err) { /* no capture: still works inside the map */ }
    pinEl.focus();
  });
  pinEl.addEventListener("pointermove", function (e) {
    if (!dragging) { return; }
    if (!moved) {
      if (Math.abs(e.clientX - downAt.x) < DRAG_PX && Math.abs(e.clientY - downAt.y) < DRAG_PX) { return; }
      moved = true;
    }
    dragTo(e);
  });
  function endDrag() {
    if (!dragging) { return; }
    dragging = false;
    pinEl.classList.remove("sw-drag");
    if (moved) { savePin(); announcePin(); }
  }
  pinEl.addEventListener("pointerup", endDrag);
  pinEl.addEventListener("pointercancel", endDrag);
  pinEl.addEventListener("click", function (e) { e.stopPropagation(); });
  pinEl.addEventListener("keydown", function (e) {
    if (picksLocked) { return; }
    var step = e.shiftKey ? 10 : 2;
    var dLat = 0, dLon = 0;
    if (e.key === "ArrowUp") { dLat = step; }
    else if (e.key === "ArrowDown") { dLat = -step; }
    else if (e.key === "ArrowLeft") { dLon = -step; }
    else if (e.key === "ArrowRight") { dLon = step; }
    else if (e.key === "Delete" || e.key === "Backspace") { e.preventDefault(); clearPin(); announcePin(); return; }
    else { return; }
    e.preventDefault();
    var base = pin || pinRest;
    setPin(base.lat + dLat, base.lon + dLon, true);
    announcePin();
  });
  $("server-city").addEventListener("change", function () {
    var opt = this.options[this.selectedIndex];
    if (!opt.value) { clearPin(); announcePin(); return; }
    if (opt.value === "custom") { return; }
    setPin(Number(opt.getAttribute("data-lat")), Number(opt.getAttribute("data-lon")), true);
    announcePin();
  });

  // On phones the pin gets an invisible hit circle at least 44 px across (46 px, to be safe) (sized from the map's scale).
  var pinHit = document.createElementNS(SVGNS, "circle");
  pinHit.setAttribute("class", "sw-hit");
  pinHit.setAttribute("fill", "none");
  pinHit.setAttribute("pointer-events", "all");
  pinHit.setAttribute("r", "0");
  pinEl.insertBefore(pinHit, pinEl.firstChild);
  function sizePinHit() {
    var m = pinSvg.getScreenCTM();
    var scale = m ? Math.abs(m.a) : 0;
    pinHit.setAttribute("r", phone() && scale > 0 ? String(Math.ceil(23 / scale * 10) / 10) : "0");
  }
  window.addEventListener("resize", sizePinHit);

  var placing = false;
  function setPlacing(on) {
    placing = !!on && !picksLocked;
    setText($("pin-move"), placing ? "Cancel moving" : "Move pin");
    show($("pin-move-hint"), placing);
    pinSvg.classList.toggle("sw-placing", placing);
    if (placing) {
      if (sheet === "full") { setSheet("half"); }  // the map must be on screen to tap it
      setText($("pin-status"), "Tap the map where the server is. Press Cancel moving or Escape to stop.");
    }
  }
  $("pin-move").addEventListener("click", function () { setPlacing(!placing); });
  function placeAt(e) {
    var pt = pinPoint(e);
    setPlacing(false);
    if (!pt) { return; }
    setPin(90 - clampNum(pt.y, 0, vb.height) / vb.height * 180, clampNum(pt.x, 0, vb.width) / vb.width * 360 - 180, true);
    announcePin();
    if (rendered($("pin-move"))) { $("pin-move").focus(); }
  }

  initPicker();
  setPicks([], true);
  var saved0 = readPin();
  if (saved0) { setPin(saved0.lat, saved0.lon, false); pinTimer = setTimeout(function () { clearPin(); }, Math.max(0, saved0.savedAt + TTL_MS - Date.now())); } else { renderPin(); }

  var pickerMap = $("picker-map");
  pickerMap.addEventListener("click", function (e) {
    if (placing) { placeAt(e); return; }
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

  // --- phone layout: one thin top bar with a menu, one bottom sheet with three heights -----------
  // Below 700 px (the same width as the stylesheet). The sheet height lives on <body data-sheet>; the
  // stylesheet decides what each height shows. Drag or tap the handle, or use Show more / Show less;
  // keyboard and screen-reader users get the buttons, and the new height is announced.
  var phoneMQ = window.matchMedia ? window.matchMedia("(max-width: 700px)") : null;
  function phone() { return !!(phoneMQ && phoneMQ.matches); }
  var SHEETS = ["peek", "half", "full"];
  var sheet = "peek";
  var sheetTexts = {
    peek: "Panel lowered: the address, the cost and Start.",
    half: "Panel at half height: places and More checks.",
    full: "Panel at full height: every setting."
  };
  var sheetResultTexts = {
    peek: "Panel lowered: the headline.",
    half: "Panel at half height: the summary.",
    full: "Panel at full height: the details."
  };

  function rendered(el) { return !!(el && el.getClientRects().length); }

  function setSheet(h, quiet) {
    if (SHEETS.indexOf(h) === -1) { h = "peek"; }
    // Read before anything changes: the browser drops focus from an element as soon as it is hidden.
    var a = document.activeElement;
    var changed = h !== sheet;
    sheet = h;
    body.setAttribute("data-sheet", h);
    show($("sheet-less"), h !== "peek");
    show($("sheet-more"), h !== "full");
    var results = hasResults && !picking;
    if (phone() && results) {
      var d = $("drawer");
      if (h === "full" && d.hidden) {
        setDrawer(true, $("sheet-more"));
      } else if (h !== "full" && !d.hidden) {
        show(d, false);
        $("drawer-toggle").setAttribute("aria-expanded", "false");
        clearSelected();
      }
    }
    if (changed && h === "peek") { $("sheet-body").scrollTop = 0; }  // peek always shows its top
    if (changed && !quiet) { setText($("sheet-state"), (results ? sheetResultTexts : sheetTexts)[h]); }
    // Focus never stays on something the new height hides: it moves to the sheet's own buttons.
    var now = document.activeElement;
    if (phone() && a && a !== body && (now === a || now === body || !now) && $("sheet").contains(a) && !rendered(a)) {
      var next = rendered($("sheet-more")) ? $("sheet-more") : $("sheet-less");
      next.focus();
    }
  }

  function stepSheet(by) {
    var i = Math.max(0, Math.min(SHEETS.length - 1, SHEETS.indexOf(sheet) + by));
    setSheet(SHEETS[i]);
  }

  $("sheet-more").addEventListener("click", function () { stepSheet(1); });
  $("sheet-less").addEventListener("click", function () { stepSheet(-1); });

  // The handle: a tap moves up one height (from full back down to peek); a drag snaps to the height
  // nearest where the finger is let go, in the direction it moved. Pointer only (aria-hidden): the
  // buttons next to it do the same for everyone.
  var handle = $("sheet-handle");
  var grab = null;
  handle.addEventListener("pointerdown", function (e) {
    if (e.button !== undefined && e.button !== 0) { return; }
    e.preventDefault();
    grab = { y: e.clientY };
    body.classList.add("sheet-drag");
    try { handle.setPointerCapture(e.pointerId); } catch (err) { /* still works without capture */ }
  });
  function endGrab(e, cancelled) {
    if (!grab) { return; }
    var dy = grab.y - e.clientY;
    grab = null;
    body.classList.remove("sheet-drag");
    if (cancelled) { return; }
    if (Math.abs(dy) < 8) { setSheet(sheet === "full" ? "peek" : SHEETS[SHEETS.indexOf(sheet) + 1]); return; }
    var at = e.clientY / Math.max(1, window.innerHeight);  // where the top of the sheet was let go
    var nearest = at < 0.25 ? "full" : at < 0.6 ? "half" : "peek";
    var i = SHEETS.indexOf(sheet), j = SHEETS.indexOf(nearest);
    if (dy > 0) { j = Math.max(j, i + 1); } else { j = Math.min(j, i - 1); }
    setSheet(SHEETS[Math.max(0, Math.min(SHEETS.length - 1, j))]);
  }
  handle.addEventListener("pointerup", function (e) { endGrab(e, false); });
  handle.addEventListener("pointercancel", function (e) { endGrab(e, true); });

  // The menu (phones): Export, Run again, Clear now, Locations, the colour theme and the allowance.
  function menuOpen() { return body.classList.contains("menu-open"); }
  function setMenu(on) {
    body.classList.toggle("menu-open", !!on);
    $("menu-toggle").setAttribute("aria-expanded", on ? "true" : "false");
  }
  $("menu-toggle").addEventListener("click", function () { setMenu(!menuOpen()); });
  // An action from the menu closes it (after the action's own handler has run).
  $("results-bar").addEventListener("click", function (e) {
    var b = e.target.closest ? e.target.closest(".actions button") : null;
    if (!b || !menuOpen()) { return; }
    setMenu(false);
    if (!$("menu-toggle").hidden) { $("menu-toggle").focus(); }
  });
  document.addEventListener("click", function (e) {
    if (menuOpen() && !$("topbar").contains(e.target)) { setMenu(false); }
  });

  function layoutChanged() {
    if (!phone()) { setMenu(false); }
    if (phone() && hasResults && !picking && !$("drawer").hidden) { setSheet("full", true); }
    colFocus();
    sizePinHit();
  }
  if (phoneMQ) {
    if (phoneMQ.addEventListener) { phoneMQ.addEventListener("change", layoutChanged); } else { phoneMQ.addListener(layoutChanged); }
  }
  sizePinHit();

  // --- layout modes: map in front, panels over it ----------------------------------------------
  var hasResults = false;
  var picking = true;
  var lastMode = null;

  function applyMode() {
    var showPicker = !hasResults || picking;
    show($("picker-map"), showPicker);
    show($("results-map"), !showPicker);
    show($("form-panel"), showPicker);
    show($("picker-strip"), showPicker);
    show($("results-panel"), hasResults && !picking);
    show($("results-bar"), hasResults);
    show($("edge-coords"), showPicker && !!pin);
    if (showPicker) { setDrawer(false); }  // otherwise the Table button and dots control the drawer
    var t = $("locations-toggle");
    t.setAttribute("aria-pressed", picking ? "true" : "false");
    document.body.classList.toggle("has-results", hasResults);
    document.body.classList.toggle("show-results", hasResults && !picking);
    // A new mode starts the phone sheet at a fitting height: the headline (peek) for results, the
    // places (half) when changing the places of a result, the address and Start (peek) otherwise.
    var mode = !hasResults ? "pick" : picking ? "repick" : "results";
    if (mode !== lastMode) {
      lastMode = mode;
      setSheet(mode === "repick" ? "half" : "peek", true);
    }
    colFocus();
  }

  // When the side column shows no form (picks locked while testing, or the results summary) it can
  // still scroll but holds nothing focusable: let the keyboard reach it so all of it can be read.
  function colFocus() {
    var want = picksLocked || (hasResults && !picking);
    var on = phone() ? $("sheet-body") : $("left-col");
    var off = phone() ? $("left-col") : $("sheet-body");
    if (want) { on.setAttribute("tabindex", "0"); } else { on.removeAttribute("tabindex"); }
    off.removeAttribute("tabindex");
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
    if (phone() && hasResults && !picking) {
      var want = open ? "full" : (sheet === "full" ? "half" : sheet);
      if (want !== sheet) { setSheet(want, true); }
    }
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
    if (e.key !== "Escape") { return; }
    if (menuOpen()) { e.preventDefault(); setMenu(false); $("menu-toggle").focus(); return; }
    if (placing) { e.preventDefault(); setPlacing(false); $("pin-move").focus(); return; }
    if (!$("drawer").hidden) { e.preventDefault(); closeDrawer(true); }
  });
  Array.prototype.forEach.call(document.querySelectorAll("[data-collapse]"), function (b) {
    b.addEventListener("click", function () {
      var panel = $(b.getAttribute("data-collapse"));
      var collapsed = panel.classList.toggle("collapsed");
      b.setAttribute("aria-expanded", collapsed ? "false" : "true");
      setText(b, collapsed ? "Show" : "Hide");
    });
  });

  applyMode();  // sets the starting sheet height (peek) and the body classes

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
      var panelNodes = take(".figures, .excluded-note, .verdict, .failed-list, #map-detail, .legend, .position-note");
      // "Where the time goes": the headline sits right under the figures; the diagram stays in the drawer.
      var head = take(".bn-head");
      // "Who can reach it": its finding follows, the grid stays in the drawer above the diagram.
      head = head.concat(take(".rc-head"));
      // "Is it fast?": its findings follow too; the table stays in the drawer.
      head = head.concat(take(".sp-head"));
      var at = panelNodes.findIndex(function (n) { return n.classList.contains("figures"); });
      Array.prototype.splice.apply(panelNodes, [at + 1, 0].concat(head));
      var rest = doc.querySelector(".fragment") || doc.body;
      var mapBox = $("results-map");
      mapBox.replaceChildren.apply(mapBox, mapNodes);
      var summary = $("results-summary");
      summary.replaceChildren.apply(summary, panelNodes);
      var target = $("results-body");
      target.replaceChildren.apply(target, Array.prototype.slice.call(rest.childNodes));
      averageLabel = null;
      setText($("results-title"), hostOf(saved.result.target));
      hasResults = true;
      picking = false;
      applyMode();
      $("left-col").scrollTop = 0;  // the headline figures first
      $("sheet-body").scrollTop = 0;
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
    clearPin();
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
    if (costStale) {  // the cost on screen is not for these picks, or could not be worked out
      formError(estimateFailed ? COST_FAILED : COST_WAIT);
      if (estimateFailed || !estimateTimer) { estimateSoon(); }
      return;
    }
    if ($("confirm").checked) { v.confirm = "1"; }
    if (pin) { v.server_lat = String(pin.lat); v.server_lon = String(pin.lon); }
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
          $("left-col").scrollTop = 0;  // progress is at the top of the column: bring it on screen
          $("sheet-body").scrollTop = 0;
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

  // --- "Place a server manually": the pin is only a fallback, used when nothing could be placed ---
  $("results-summary").addEventListener("click", function (e) {
    var b = e.target.closest ? e.target.closest("[data-place-pin]") : null;
    if (!b) { return; }
    setPicking(true);
    var panel = $("form-panel");
    if (panel.classList.contains("collapsed")) { panel.querySelector("[data-collapse]").click(); }
    pinEl.focus();
    setText($("pin-status"), "Drag the Server pin or use the arrow keys, then run the test again. " +
      "It is your own claim and is used only when no answering server can be placed.");
  });

  // --- map: dots select their table row ---------------------------------------------------------
  function dotOf(target) {
    return target && target.closest ? target.closest("circle[data-row]") : null;
  }

  // Squares (the server that answered) only describe themselves; they have no single table row.
  function squareOf(target) {
    return target && target.closest ? target.closest("rect.sw-sq") : null;
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
    showPlace(dot.getAttribute("data-row"));
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
    var dot = dotOf(e.target) || squareOf(e.target);
    if (dot) { describe(dot); }
  });
  resultsBody.addEventListener("mouseover", function (e) {
    var dot = dotOf(e.target) || squareOf(e.target);
    if (dot) { describe(dot); }
  });

  // --- "where the time goes": one view per place, all rendered (escaped) by the server -------------
  // Selecting a place (map dot, table row or the place list) shows that place's diagram; blocked or
  // failed places show only their reason. Nothing is built from data here: views are only shown or hidden.
  var averageLabel = null;

  function showPlace(key) {
    var body = $("results-body");
    var views = body.querySelectorAll(".bn-view");
    if (!views.length) { return; }
    if (!body.querySelector('.bn-view[data-bn-view="' + key + '"]')) { key = "all"; }
    Array.prototype.forEach.call(views, function (v) { v.hidden = v.getAttribute("data-bn-view") !== key; });
    var label = body.querySelector("[data-bn-showing]");
    if (averageLabel === null && label) { averageLabel = label.textContent; }
    var pick = body.querySelector('.bn-pick[data-bn-show="' + key + '"] .bn-pname');
    setText(label, key === "all" || !pick ? averageLabel : pick.textContent);
    Array.prototype.forEach.call(body.querySelectorAll(".bn-pick"), function (b) {
      if (b.getAttribute("data-bn-show") === key) { b.setAttribute("aria-current", "true"); } else { b.removeAttribute("aria-current"); }
    });
    show(body.querySelector('.bn-showing [data-bn-show="all"]'), key !== "all");
  }

  $("results-body").addEventListener("click", function (e) {
    var b = e.target.closest ? e.target.closest("[data-bn-show]") : null;
    if (b) {
      showPlace(b.getAttribute("data-bn-show"));
      var top = $("results-body").querySelector(".bn-showing");
      if (top && b.classList.contains("bn-pick")) { top.scrollIntoView({ block: "nearest" }); }
      return;
    }
    var tr = e.target.closest ? e.target.closest("tr[data-row]") : null;
    if (!tr) { return; }
    Array.prototype.forEach.call($("results-body").querySelectorAll("tr[data-row]"), function (row) {
      row.classList.toggle("hl", row === tr);
    });
    showPlace(tr.getAttribute("data-row"));
  });

  $("results-summary").addEventListener("click", function (e) {
    var b = e.target.closest ? e.target.closest("[data-rc-open]") : null;
    if (!b) { return; }
    setDrawer(true, b);
    var title = $("results-body").querySelector(".rc-title");
    if (title) { title.scrollIntoView({ block: "start" }); title.focus(); }
  });

  $("results-summary").addEventListener("click", function (e) {
    var b = e.target.closest ? e.target.closest("[data-sp-open]") : null;
    if (!b) { return; }
    setDrawer(true, b);
    var title = $("results-body").querySelector(".sp-title");
    if (title) { title.scrollIntoView({ block: "start" }); title.focus(); }
  });

  $("results-summary").addEventListener("click", function (e) {
    var b = e.target.closest ? e.target.closest("[data-bn-open]") : null;
    if (!b) { return; }
    setDrawer(true, b);
    showPlace("all");
    var title = $("results-body").querySelector(".bn-title");
    if (title) { title.scrollIntoView({ block: "start" }); title.focus(); }
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
