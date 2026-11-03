/* speedward colour theme: Light, Dark or Auto (the default, which follows the device).
 *
 * Loaded from <head> without defer, so a saved choice is on <html data-theme> before the first paint
 * (no flash of the other theme). It is a file, not an inline script, so the strict CSP holds.
 * The choice is kept in this browser's localStorage only ("speedward:theme"); it is never sent to the
 * server and no cookie is set. When storage is blocked the switch still works for this page view.
 */
(function () {
  "use strict";

  var KEY = "speedward:theme";
  var root = document.documentElement;

  function valid(v) { return v === "light" || v === "dark" ? v : "auto"; }

  function read() {
    try { return valid(window.localStorage.getItem(KEY)); } catch (e) { return "auto"; }
  }

  function write(v) {
    try {
      if (v === "auto") { window.localStorage.removeItem(KEY); } else { window.localStorage.setItem(KEY, v); }
    } catch (e) { /* kept for this page view only */ }
  }

  function apply(v) {
    if (v === "auto") { root.removeAttribute("data-theme"); } else { root.setAttribute("data-theme", v); }
  }

  var choice = read();
  apply(choice);

  function radios() { return Array.prototype.slice.call(document.querySelectorAll('input[name="theme"]')); }

  function mark() { radios().forEach(function (r) { r.checked = r.value === choice; }); }

  function wire() {
    mark();
    radios().forEach(function (r) {
      r.addEventListener("change", function () {
        if (!r.checked) { return; }
        choice = valid(r.value);
        apply(choice);
        write(choice);
      });
    });
  }

  // Another tab of this site changed the choice: follow it here too.
  window.addEventListener("storage", function (e) {
    if (e.key !== null && e.key !== KEY) { return; }
    choice = read();
    apply(choice);
    mark();
  });

  if (document.readyState === "loading") { document.addEventListener("DOMContentLoaded", wire); } else { wire(); }
}());
