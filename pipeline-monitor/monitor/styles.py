"""CSS for the pipeline monitor page.

Reuses the `dsa` skin tokens verbatim from `web/lm/styles.py` (PLAN.md §6's
table) so the monitor reads as a sibling of the public site without
importing a line of its code. Everything below the token block is
monitor-specific structure: docket rows, ghost/actual bars, the scrape
panel, freshness list, and the failure band.

No colour literal appears anywhere below the token block -- every fill and
stroke is `var(--...)`, which is also what `tests/test_render.py` asserts
against `bars.py`. This file is plain text concatenated into a `<style>`
tag; it is never used as the left-hand side of a `%` format operation, so
the many literal `%` signs in the CSS (percentages, `vw`/`vh` are not `%`
but widths are) are always safe.
"""

# Verbatim from web/lm/styles.py CSS_TOKENS_DSA -- only the twelve tokens
# PLAN.md §6 names. Exact same hex values, both light and dark.
TOKENS = r"""
:root {
  --paper:#f6f4f3; --paper-2:#ffffff; --paper-3:#ece8e7;
  --ink:#231f20; --ink-2:#605c5c; --rule:#8c8989;
  --survey:#ec1f27; --survey-w:#fbd2d4; --oxide:#a00a10;
  --ochre:#6d5300; --focus:#c4151c;
  --mono: ui-monospace, "Cascadia Mono", "SF Mono", SFMono-Regular, Menlo,
          Consolas, "Liberation Mono", "Courier New", monospace;
  --sans: -apple-system, "Segoe UI", Roboto, Helvetica, Arial, system-ui,
          sans-serif;
}
@media (prefers-color-scheme: dark) {
  :root {
    --paper:#191617; --paper-2:#231f20; --paper-3:#1e1a1b;
    --ink:#f6f4f3; --ink-2:#a9a4a4; --rule:#494545;
    --survey:#ec1f27; --survey-w:#3a1416; --oxide:#f5726f;
    --ochre:#e8c34a; --focus:#f5726f;
  }
}
:root[data-theme="dark"] {
  --paper:#191617; --paper-2:#231f20; --paper-3:#1e1a1b;
  --ink:#f6f4f3; --ink-2:#a9a4a4; --rule:#494545;
  --survey:#ec1f27; --survey-w:#3a1416; --oxide:#f5726f;
  --ochre:#e8c34a; --focus:#f5726f;
}
:root[data-theme="light"] {
  --paper:#f6f4f3; --paper-2:#ffffff; --paper-3:#ece8e7;
  --ink:#231f20; --ink-2:#605c5c; --rule:#8c8989;
  --survey:#ec1f27; --survey-w:#fbd2d4; --oxide:#a00a10;
  --ochre:#6d5300; --focus:#c4151c;
}
"""

BASE = r"""
html { -webkit-text-size-adjust: 100%; }
body {
  margin: 0; background: var(--paper); color: var(--ink);
  font-family: var(--sans); font-size: 14px; line-height: 1.5;
  overflow-x: hidden;
}
*, *::before, *::after { box-sizing: border-box; }
img, svg, table { max-width: 100%; }
:focus-visible { outline: 2px solid var(--focus); outline-offset: 2px; }
.pm-wrap { max-width: 62rem; margin-inline: auto; padding-inline: clamp(0.9rem, 4vw, 2rem); }
.pm-m { font-family: var(--mono); font-variant-numeric: tabular-nums; }
.pm-eyebrow {
  font-family: var(--sans); font-size: 11px; letter-spacing: 0.13em;
  text-transform: uppercase; color: var(--ink-2); font-weight: 600;
}
.pm-skiplink { position: absolute; left: -9999px; }
.pm-skiplink:focus {
  left: 1rem; top: 1rem; z-index: 9; background: var(--ink); color: var(--paper);
  padding: 0.5rem 0.8rem; font-family: var(--sans); font-size: 0.8rem;
}

/* -- header: a 3px red rule appears ONLY while a run is live -- */
.pm-head { border-bottom: 1px solid var(--rule); }
.pm-head--live { border-bottom: 3px solid var(--survey); }
.pm-topline {
  display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between;
  gap: 0.8rem; padding-block: 0.9rem;
}
.pm-brand { display: flex; align-items: baseline; gap: 0.6rem; min-width: 0; }
.pm-brand .pm-title {
  font-family: var(--sans); font-weight: 700; font-size: 13px; letter-spacing: 0.14em;
  text-transform: uppercase; color: var(--ink);
}
.pm-brand .pm-sub { font-family: var(--mono); font-size: 12px; color: var(--ink-2); }
.pm-themebtn {
  font-family: var(--sans); font-size: 11px; letter-spacing: 0.08em; text-transform: uppercase;
  background: transparent; color: var(--ink-2); border: 1px solid var(--rule);
  padding: 0.35rem 0.65rem; cursor: pointer; border-radius: 2px;
}
.pm-themebtn:hover { color: var(--ink); border-color: var(--ink); }

/* -- headline band -- */
.pm-headline { padding-block: 1.4rem; border-bottom: 1px solid var(--rule); }
.pm-headline .pm-state {
  font-family: var(--sans); font-weight: 700; font-size: 12px; letter-spacing: 0.16em;
  text-transform: uppercase; margin: 0 0 0.5rem;
}
.pm-state--running { color: var(--survey); }
.pm-state--failed { color: var(--oxide); }
.pm-state--unknown { color: var(--ochre); }
.pm-state--idle { color: var(--ink-2); }
.pm-headline .pm-target {
  font-family: var(--mono); font-size: 22px; font-weight: 600; word-break: break-word;
  margin: 0 0 0.6rem;
}
.pm-headline .pm-timegrid {
  display: flex; flex-wrap: wrap; gap: 1.6rem 2.4rem; font-family: var(--mono); font-size: 13px;
}
.pm-headline .pm-timegrid dt {
  font-family: var(--sans); font-size: 10.5px; letter-spacing: 0.11em; text-transform: uppercase;
  color: var(--ink-2); margin: 0;
}
.pm-headline .pm-timegrid dd { margin: 0.15rem 0 0; }
.pm-headline .pm-timegrid > div { min-width: 0; }

/* -- failure band: pinned directly under the header -- */
.pm-failband {
  background: var(--paper-2); border-left: 4px solid var(--oxide);
  border-bottom: 1px solid var(--rule); padding: 0.9rem clamp(0.9rem, 4vw, 2rem);
}
.pm-failband .pm-fail-head {
  font-family: var(--sans); font-weight: 700; font-size: 12px; letter-spacing: 0.1em;
  text-transform: uppercase; color: var(--oxide); margin: 0 0 0.3rem;
}
.pm-failband p { margin: 0; font-family: var(--mono); font-size: 13px; word-break: break-word; }

/* -- empty / idle directive states -- */
.pm-empty {
  margin: 1.2rem 0; border: 1px solid var(--rule); border-left: 3px solid var(--ochre);
  background: var(--paper-2); padding: 1rem 1.2rem;
}
.pm-empty p { margin: 0; font-size: 0.95em; }

/* -- docket -- */
.pm-panel { padding-block: 1.3rem; border-bottom: 1px solid var(--rule); }
.pm-panel-head {
  display: flex; align-items: baseline; justify-content: space-between; gap: 0.6rem;
  margin-bottom: 0.7rem;
}
.pm-panel-head h2 {
  font-family: var(--sans); font-size: 12px; font-weight: 700; letter-spacing: 0.14em;
  text-transform: uppercase; margin: 0;
}
.pm-panel-head .pm-count { font-family: var(--mono); font-size: 12px; color: var(--ink-2); }

.pm-docket { display: flex; flex-direction: column; }
.pm-row {
  display: grid; grid-template-columns: 2.4rem 1fr 10rem; align-items: center;
  gap: 0.6rem 0.9rem; padding-block: 0.45rem; border-top: 1px solid var(--rule);
}
.pm-row:first-child { border-top: 0; }
.pm-row .pm-seq {
  font-family: var(--mono); font-size: 12px; color: var(--ink-2); text-align: right;
}
.pm-row .pm-name {
  font-family: var(--mono); font-size: 13px; min-width: 0; overflow: hidden;
  text-overflow: ellipsis; white-space: nowrap;
}
.pm-row .pm-status { display: flex; flex-direction: column; gap: 0.15rem; min-width: 0; }
.pm-status-row { display: flex; align-items: center; gap: 0.6rem; min-width: 0; }
.pm-status-row .pm-bar { flex: 1 1 auto; min-width: 3rem; }
.pm-status-row .pm-dur { flex: none; }
.pm-row .pm-skipword {
  font-family: var(--mono); font-size: 12px; color: var(--ink-2);
}
.pm-row .pm-dur {
  font-family: var(--mono); font-size: 12px; text-align: right; white-space: nowrap;
}
.pm-row .pm-ghost-annotation {
  font-family: var(--mono); font-size: 11px; color: var(--ink-2); display: block; text-align: right;
}
.pm-row .pm-warn { color: var(--ochre); margin-left: 0.3rem; }

.pm-skipgroup summary {
  font-family: var(--mono); font-size: 12px; color: var(--ink-2); cursor: pointer;
  padding-block: 0.45rem; border-top: 1px solid var(--rule); list-style: none;
}
.pm-skipgroup:first-of-type summary { border-top: 0; }
.pm-skipgroup summary::-webkit-details-marker { display: none; }
.pm-skipgroup summary::before { content: "\25b8"; display: inline-block; width: 1.1em; }
.pm-skipgroup[open] summary::before { content: "\25be"; }
.pm-skipgroup .pm-skiprow {
  font-family: var(--mono); font-size: 12px; color: var(--ink-2);
  padding: 0.2rem 0 0.2rem 2.4rem;
}

/* the bar itself -- see bars.py for the SVG this styles */
.pm-bar { display: block; width: 100%; height: 20px; }
.pm-ghost { fill: var(--survey-w); }
.pm-done { fill: var(--ink-2); }
.pm-live { fill: var(--survey); }
.pm-failed { fill: var(--oxide); }
.pm-overrun.pm-done, .pm-cap.pm-done {
  fill: var(--paper-2);
  stroke: var(--ink-2); stroke-width: 1.5;
}
.pm-overrun.pm-live, .pm-cap.pm-live {
  fill: var(--paper-2);
  stroke: var(--survey); stroke-width: 1.5;
}
.pm-overrun.pm-failed, .pm-cap.pm-failed {
  fill: var(--paper-2);
  stroke: var(--oxide); stroke-width: 1.5;
}
.pm-overrun { fill-opacity: 0.5; }
.pm-tick.pm-done { fill: var(--ink-2); }
.pm-tick.pm-live { fill: var(--survey); }
.pm-tick.pm-failed { fill: var(--oxide); }
.pm-ratio {
  font-family: var(--mono); font-size: 9px; fill: var(--ink-2); text-anchor: end;
}
.pm-stamp-box { fill: var(--paper-2); stroke: var(--oxide); stroke-width: 1.5; }
.pm-stamp-text {
  font-family: var(--sans); font-size: 8px; font-weight: 700; letter-spacing: 0.08em;
  fill: var(--oxide);
}

/* -- scrape panel -- */
.pm-scrape-summary { font-family: var(--mono); font-size: 13px; margin: 0 0 0.7rem; }
.pm-buckets { display: flex; flex-wrap: wrap; gap: 0.5px; background: var(--rule); border: 1px solid var(--rule); }
.pm-bucket { background: var(--paper-2); padding: 0.6rem 0.8rem; flex: 1 1 10rem; min-width: 0; }
.pm-bucket .pm-bucket-v {
  display: block; font-family: var(--mono); font-weight: 700; font-size: 20px; letter-spacing: -0.02em;
}
.pm-bucket .pm-bucket-k {
  display: block; font-family: var(--sans); font-size: 10.5px; letter-spacing: 0.08em;
  text-transform: uppercase; color: var(--ink-2); margin-top: 0.25rem;
}
.pm-bucket--matched .pm-bucket-v { color: var(--survey); }
.pm-bucket--norecord .pm-bucket-v { color: var(--ink); }
.pm-bucket--failed .pm-bucket-v { color: var(--oxide); }
.pm-bucket--waiting .pm-bucket-v { color: var(--ink-2); }
.pm-scrape-note { margin: 0.7rem 0 0; font-size: 0.9em; color: var(--ink-2); }

/* -- freshness -- */

/* Download action in a panel head. Quiet by default: this is an operator
   affordance next to the data it acts on, not a call to action. */
.pm-panel-action{display:flex;align-items:baseline;gap:.6rem;flex-wrap:wrap;margin-left:auto;}
.pm-dl{display:inline-block;padding:.3rem .7rem;border:1px solid var(--rule);
  border-radius:2px;color:var(--ink);text-decoration:none;font-size:.8125rem;
  background:var(--paper-2);}
.pm-dl:hover{border-color:var(--ink-2);}
.pm-dl:focus-visible{outline:2px solid var(--focus);outline-offset:2px;}
.pm-dl-note{font-size:.75rem;color:var(--ink-2);}
@media (max-width:600px){.pm-panel-action{margin-left:0;width:100%;}}
.pm-fresh-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 0.35rem; }
.pm-fresh-list li {
  display: flex; flex-wrap: wrap; justify-content: space-between; gap: 0.6rem;
  font-family: var(--mono); font-size: 12.5px; padding-block: 0.15rem;
}
.pm-fresh-list .pm-fresh-name { color: var(--ink); min-width: 0; word-break: break-word; }
.pm-fresh-list .pm-fresh-meta { color: var(--ink-2); white-space: nowrap; }
.pm-fresh-list .pm-fresh-missing .pm-fresh-name { color: var(--ink-2); }

/* -- log -- */
.pm-log { padding-block: 1rem; }
.pm-log summary {
  font-family: var(--sans); font-size: 12px; letter-spacing: 0.08em; text-transform: uppercase;
  color: var(--ink-2); cursor: pointer; list-style: none;
}
.pm-log summary::-webkit-details-marker { display: none; }
.pm-log summary::before { content: "\25b8 "; }
.pm-log[open] summary::before { content: "\25be "; }
.pm-log pre {
  font-family: var(--mono); font-size: 11.5px; line-height: 1.5; white-space: pre-wrap;
  word-break: break-word; background: var(--paper-2); border: 1px solid var(--rule);
  padding: 0.8rem; margin: 0.6rem 0 0; max-height: 22rem; overflow-y: auto;
}

/* -- motion budget: one 400ms width transition, one 2s pulse, nothing else -- */
@media (prefers-reduced-motion: no-preference) {
  .pm-actual, .pm-tick, .pm-overrun, .pm-cap { transition: width 400ms ease-out, x 400ms ease-out; }
  .pm-pulse { animation: pm-pulse 2s ease-in-out infinite; }
}
@keyframes pm-pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.25; } }
@media (prefers-reduced-motion: reduce) {
  .pm-actual, .pm-tick, .pm-overrun, .pm-cap { transition: none; }
  .pm-pulse { animation: none; opacity: 1; }
}

/* -- responsive: <=600px stacks each docket row to two lines -- */
@media (max-width: 600px) {
  .pm-row {
    grid-template-columns: 2.4rem 1fr; grid-template-areas: "seq name" "bar bar";
  }
  .pm-row .pm-seq { grid-area: seq; }
  .pm-row .pm-name { grid-area: name; }
  .pm-row .pm-status { grid-area: bar; }
  .pm-row .pm-dur { text-align: left; }
  .pm-row .pm-ghost-annotation { text-align: left; }
  .pm-buckets { flex-direction: column; }
  .pm-headline .pm-timegrid { gap: 0.9rem 1.6rem; }
}
"""

PAGE_CSS = TOKENS + BASE

THEME_JS = r"""
(function () {
  var root = document.documentElement;
  var btn = document.getElementById("pm-themebtn");
  if (!btn) return;
  function prefersDark() {
    return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
  }
  function currentIsDark() {
    var set = root.getAttribute("data-theme");
    if (set === "dark") return true;
    if (set === "light") return false;
    return prefersDark();
  }
  function paint() {
    var dark = currentIsDark();
    btn.textContent = dark ? "Light mode" : "Dark mode";
    btn.setAttribute("aria-pressed", dark ? "true" : "false");
  }
  btn.addEventListener("click", function () {
    var next = currentIsDark() ? "light" : "dark";
    root.setAttribute("data-theme", next);
    try { localStorage.setItem("pm-theme", next); } catch (err) {}
    paint();
  });
  try {
    var saved = localStorage.getItem("pm-theme");
    if (saved === "dark" || saved === "light") root.setAttribute("data-theme", saved);
  } catch (err) {}
  paint();
})();
"""

# Polls /api/status every 3s and swaps #pm-main when generated_at moves --
# the page itself only ever renders a Status it was given, so a live refresh
# means "fetch a fresh render", not "reimplement the bar math in JS". The
# staleness note updates in place without a reload so a wedged sampler is
# visible even between polls.
POLL_JS = r"""
(function () {
  // Localize every <time data-utc> into the viewer's own zone. The server
  // renders UTC so the page is correct without scripting; this only improves
  // it. Run on load and after every swap.
  function localize(root) {
    var nodes = (root || document).querySelectorAll("time[data-utc]");
    for (var i = 0; i < nodes.length; i++) {
      var iso = nodes[i].getAttribute("data-utc");
      var d = new Date(iso);
      if (isNaN(d.getTime())) continue;
      var hh = String(d.getHours()).padStart(2, "0");
      var mm = String(d.getMinutes()).padStart(2, "0");
      var ss = String(d.getSeconds()).padStart(2, "0");
      nodes[i].textContent = hh + ":" + mm + ":" + ss;
      nodes[i].setAttribute("title", iso);
    }
  }

  var stamp = document.getElementById("pm-generated-at");
  var known = stamp ? stamp.getAttribute("data-generated-at") : null;
  localize(document);
  if (!known) return;

  // Swap only the main region rather than reloading. A full reload every few
  // seconds throws away scroll position and slams shut the expanded skipped
  // group, which is exactly the state an operator opens on purpose.
  function openKeys() {
    var open = {};
    var d = document.querySelectorAll("#pm-main details");
    for (var i = 0; i < d.length; i++) {
      if (d[i].open) open[d[i].getAttribute("data-key") || String(i)] = true;
    }
    return open;
  }

  function restore(open) {
    var d = document.querySelectorAll("#pm-main details");
    for (var i = 0; i < d.length; i++) {
      if (open[d[i].getAttribute("data-key") || String(i)]) d[i].open = true;
    }
  }

  function swap() {
    fetch(window.location.pathname, { cache: "no-store" }).then(function (r) {
      return r.ok ? r.text() : null;
    }).then(function (html) {
      if (!html) return;
      var doc = new DOMParser().parseFromString(html, "text/html");
      var next = doc.getElementById("pm-main");
      var here = document.getElementById("pm-main");
      if (!next || !here) return;
      var open = openKeys();
      var y = window.scrollY;
      here.innerHTML = next.innerHTML;
      restore(open);
      window.scrollTo(0, y);
      localize(here);
      var s2 = document.getElementById("pm-generated-at");
      if (s2) known = s2.getAttribute("data-generated-at");
    }).catch(function () { /* leave the last good render up */ });
  }

  function tick() {
    fetch("/api/status", { cache: "no-store" }).then(function (r) {
      return r.ok ? r.json() : null;
    }).then(function (status) {
      if (!status || !status.generated_at) return;
      if (status.generated_at !== known) swap();
    }).catch(function () { /* box unreachable: leave the last render up */ });
  }
  setInterval(tick, 3000);
})();
"""

# The log panel is a <details> that lazy-fetches its content on first open --
# a multi-hour verbose R run's log can be long, so it is never inlined into
# the page render itself (render.py has no HTTP client and no log lines to
# put there in the first place; /api/logs is server.py's job).
LOG_JS = r"""
(function () {
  var det = document.querySelector(".pm-log");
  var pre = document.getElementById("pm-log-body");
  if (!det || !pre) return;
  det.addEventListener("toggle", function () {
    if (!det.open || pre.getAttribute("data-loaded") === "true") return;
    fetch("/api/logs?tail=40", { cache: "no-store" }).then(function (r) {
      return r.ok ? r.json() : null;
    }).then(function (data) {
      if (!data || !data.lines) return;
      pre.textContent = data.lines.map(function (l) { return l.text; }).join("\n");
      pre.setAttribute("data-loaded", "true");
    }).catch(function () {
      pre.textContent = "Could not reach the monitor's log endpoint.";
    });
  });
})();
"""
