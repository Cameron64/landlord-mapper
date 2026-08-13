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

/* The warning glyph is a plain <button> now (was a <summary> inside a
   <details>) -- tap/click/keyboard-Enter opens the shared dialog below
   rather than an inline body, because the inline body was the thing that
   overflowed (see render.py's _render_warning). Reset the button chrome
   explicitly, matching .pm-themebtn's approach elsewhere on this page,
   rather than reaching for `all: unset`. */
.pm-warn {
  background: none; border: 0; padding: 0; font: inherit; cursor: pointer;
  -webkit-appearance: none; appearance: none;
}

/* -- warning dialog: the full text that used to sit inline in an expanded
   <details> body now lives here instead, because a 2 KB unbroken R warning
   (no spaces to break on in the worst observed case) has nowhere to go in
   a body sized for a short warning. Both wrap rules are required together:
   `white-space: pre-wrap` alone still lets a single very long run push the
   box wider before wrapping, and `overflow-wrap: anywhere` alone does
   nothing without pre-wrap to also respect the text's own line breaks.
   `max-height` + its own scrollbar is the other half -- it bounds the
   dialog's growth instead of letting a still-longer warning push it past
   the viewport. */
.pm-warn-dialog {
  max-width: min(34rem, 92vw); width: 100%; margin: auto;
  border: 1px solid var(--rule); border-radius: 3px; padding: 0;
  background: var(--paper-2); color: var(--ink);
}
.pm-warn-dialog::backdrop { background: rgba(0, 0, 0, 0.45); }
.pm-warn-dialog-head {
  display: flex; align-items: center; justify-content: space-between;
  gap: 0.8rem; padding: 0.8rem 1rem; border-bottom: 1px solid var(--rule);
}
.pm-warn-dialog-head h2 {
  margin: 0; font-family: var(--sans); font-size: 12px; font-weight: 700;
  letter-spacing: 0.06em; word-break: break-word;
}
.pm-warn-dialog-close {
  background: none; border: 0; padding: 0 0.2rem; color: var(--ink-2);
  font-size: 1.3rem; line-height: 1; cursor: pointer;
}
.pm-warn-dialog-close:hover { color: var(--ink); }
.pm-warn-dialog-body {
  margin: 0; padding: 0.9rem 1rem; font-family: var(--mono); font-size: 12px;
  white-space: pre-wrap; overflow-wrap: anywhere; max-height: 60vh; overflow-y: auto;
}

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

/* Download action, on its own row beneath the file list it acts on.
   Solid rather than outlined: an outlined box at this size read as a link.
   Colours are the ink/paper pair, which the dark-mode block already swaps, so
   the button stays high-contrast in both themes without a second rule. Red is
   deliberately not used -- on this page red means something is running. */
.pm-panel-action {
  margin-top: 1.1rem; padding-top: 0.9rem; border-top: 1px solid var(--rule);
}
.pm-dl {
  display: inline-block; padding: 0.5rem 1rem;
  background: var(--ink); color: var(--paper);
  border: 1px solid var(--ink); border-radius: 2px;
  font-family: var(--sans); font-size: 0.8125rem; font-weight: 600;
  letter-spacing: 0.02em; text-decoration: none;
}
.pm-dl:hover { background: var(--ink-2); border-color: var(--ink-2); }
.pm-dl:focus-visible { outline: 2px solid var(--focus); outline-offset: 2px; }
.pm-dl-note { margin: 0.5rem 0 0; font-size: 0.75rem; color: var(--ink-2); }
.pm-fresh-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 0.35rem; }
.pm-fresh-list li {
  display: flex; flex-wrap: wrap; justify-content: space-between; gap: 0.6rem;
  font-family: var(--mono); font-size: 12.5px; padding-block: 0.15rem;
}
.pm-fresh-list .pm-fresh-name { color: var(--ink); min-width: 0; word-break: break-word; }
.pm-fresh-list .pm-fresh-meta { color: var(--ink-2); white-space: nowrap; }
.pm-fresh-list .pm-fresh-missing .pm-fresh-name { color: var(--ink-2); }

/* -- glyph legend -- */
.pm-legend { margin: 0.9rem 0 0; color: var(--ink-2); font-size: 11px; }
.pm-legend .pm-warn { margin: 0 0.1em; }

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
  var MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                      "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

  // Whole-calendar-day difference in the viewer's own zone (not a 24h/86400s
  // divide, which would misclassify "yesterday, late" vs "today, early" by a
  // few hours depending on the two clock-of-day values).
  function dayDiff(d, now) {
    var a = new Date(d.getFullYear(), d.getMonth(), d.getDate());
    var b = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    return Math.round((b - a) / 86400000);
  }

  // Relative phrasing answers "which day" fastest for the two cases a reader
  // actually asks about (today, yesterday); anything older degrades to an
  // explicit date rather than "3 days ago" -- the reader would still have to
  // do the subtraction themselves, so a date is no less readable and stays
  // correct arbitrarily far back. Year is only shown when it isn't the
  // current one, matching how a person would say the date out loud.
  function headlineDay(d, now) {
    var diff = dayDiff(d, now);
    if (diff === 0) return "today";
    if (diff === 1) return "yesterday";
    var s = MONTH_NAMES[d.getMonth()] + " " + d.getDate();
    if (d.getFullYear() !== now.getFullYear()) s += ", " + d.getFullYear();
    return s;
  }

  // Localize every <time data-utc> into the viewer's own zone. The server
  // renders UTC so the page is correct without scripting; this only improves
  // it. Run on load and after every swap.
  //
  // `data-format="headline"` is an opt-in marker render.py sets on exactly
  // one <time> (the idle "Finished ..." headline) and nowhere else -- the
  // docket rows and scrape/freshness rows all sit within a single run, where
  // a bare clock is correct and a repeated date would be noise. Without a
  // date at all, the headline could not tell today's finish from last
  // week's, which was the reported bug: the reader had no way to know which
  // day a "last run" result was even from.
  function localize(root) {
    var nodes = (root || document).querySelectorAll("time[data-utc]");
    var now = new Date();
    for (var i = 0; i < nodes.length; i++) {
      var iso = nodes[i].getAttribute("data-utc");
      var d = new Date(iso);
      if (isNaN(d.getTime())) continue;
      var hh = String(d.getHours()).padStart(2, "0");
      var mm = String(d.getMinutes()).padStart(2, "0");
      var ss = String(d.getSeconds()).padStart(2, "0");
      var clock = hh + ":" + mm + ":" + ss;
      if (nodes[i].getAttribute("data-format") === "headline") {
        nodes[i].textContent = headlineDay(d, now) + " at " + clock;
      } else {
        nodes[i].textContent = clock;
      }
      // The exact ISO instant stays reachable via hover/long-press
      // regardless of which text is shown above.
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
#
# This used to bind a "toggle" listener directly to the specific `.pm-log` /
# `#pm-log-body` node references captured at script-load time. That breaks
# the moment POLL_JS's swap() runs (every ~3s): `swap()` does
# `here.innerHTML = next.innerHTML`, which destroys the old `.pm-log` node
# wholesale and inserts a brand-new one with no listener attached at all --
# the captured `det`/`pre` variables are now pointing at detached nodes that
# will never fire again. The symptom was exactly the reported one: the log
# loads once, then a poll swap silently strips the panel's ability to ever
# load again, and a subsequent expand just shows the static placeholder
# forever.
#
# Fix: delegate on `document` in the CAPTURE phase instead of binding to a
# node reference, so there is nothing to go stale -- whichever `.pm-log`
# element is live in the DOM at toggle time is the one this sees, swap or no
# swap. Capture (not the default bubble phase) is required here for a
# non-obvious reason: the native "toggle" event does NOT bubble, so a
# delegated listener in the bubble phase on an ancestor would never see it
# fire on a descendant. Capture dispatch, by contrast, walks from the root
# down to whatever element the event actually targets, which happens
# independently of whether the event bubbles back up -- so it still reaches
# a `document`-level listener. This is bound exactly once, at script load
# (this file is not re-executed by a swap; only `#pm-main`'s innerHTML is
# replaced), so there is no risk of double-binding on repeated opens.
#
# The other half of the original bug was `render.py`'s `<details class="
# pm-log">` carrying no `data-key`, so POLL_JS's openKeys()/restore() fell
# back to a positional index that is unstable as skip-groups collapse and
# expand -- fixed on that side by giving it `data-key="log"`. Because the
# server always re-renders the log panel with `data-loaded="false"` (it has
# no way to know the client already fetched once), restoring `.open = true`
# after a swap fires a fresh native "toggle" event on the newly-inserted
# node, which this listener catches and uses to refetch immediately --
# rather than trying to smuggle the old text across the innerHTML swap, the
# simplest correct fix is to treat "reopened after a swap" the same as
# "opened for the first time" and just ask the server again.
LOG_JS = r"""
(function () {
  function loadLog(pre) {
    if (!pre || pre.getAttribute("data-loaded") === "true") return;
    fetch("/api/logs?tail=40", { cache: "no-store" }).then(function (r) {
      return r.ok ? r.json() : null;
    }).then(function (data) {
      if (!data || !data.lines) return;
      pre.textContent = data.lines.map(function (l) { return l.text; }).join("\n");
      pre.setAttribute("data-loaded", "true");
    }).catch(function () {
      pre.textContent = "Could not reach the monitor's log endpoint.";
    });
  }

  document.addEventListener("toggle", function (e) {
    var det = e.target;
    if (!det || !det.classList || !det.classList.contains("pm-log") || !det.open) return;
    loadLog(det.querySelector("#pm-log-body"));
  }, true);
})();
"""

# Opens/closes the single shared warning dialog (render.py's
# _render_warning / _render_warning_dialog). The dialog itself is rendered
# once, outside #pm-main, so POLL_JS's swap() (which only ever touches
# #pm-main's innerHTML) structurally cannot destroy it -- unlike the log
# panel (05cad93), there is no swap-timing window to get wrong here, so
# there is nothing for this script to coordinate with POLL_JS about.
#
# The warning BUTTON, by contrast, lives inside #pm-main and is recreated
# by every swap exactly like every other docket-row element -- so the click
# listener below delegates on `document`, which a swap never touches,
# rather than binding to any specific button. Unlike the log panel's
# "toggle" event, "click" bubbles normally, so a plain bubble-phase listener
# (no capture needed) still sees it fire on whichever `.pm-warn` button is
# live in the DOM at click time.
WARN_JS = r"""
(function () {
  var dialog = document.getElementById("pm-warn-dialog");
  if (!dialog) return;
  var titleEl = document.getElementById("pm-warn-dialog-title");
  var bodyEl = document.getElementById("pm-warn-dialog-body");

  document.addEventListener("click", function (e) {
    var btn = e.target.closest && e.target.closest(".pm-warn");
    if (btn) {
      titleEl.textContent = btn.getAttribute("data-warning-name") || "Warning";
      // textContent, not innerHTML -- the full warning is untrusted R
      // output and must never be interpreted as markup.
      bodyEl.textContent = btn.getAttribute("data-warning-full") || "";
      dialog.showModal();
      return;
    }
    if (e.target.hasAttribute && e.target.hasAttribute("data-warn-close")) {
      dialog.close();
    }
  });

  // A native <dialog>'s click target IS the <dialog> element itself when the
  // click lands outside its content box (the ::backdrop pseudo-element is
  // not directly targetable) -- so this only fires for a genuine backdrop
  // click, never for a click inside the dialog's own content.
  dialog.addEventListener("click", function (e) {
    if (e.target === dialog) dialog.close();
  });
})();
"""
