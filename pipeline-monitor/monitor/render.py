"""HTML page renderer for the pipeline monitor -- PLAN.md §6.

`render_page(status)` is the only entry point another track needs: it takes
a Status dict (PLAN.md §4, frozen) and returns a complete HTML document as a
string. There is no I/O and no clock read here -- every timestamp used below
is already inside the Status dict, and "now" for a still-running target's
elapsed time is derived from `generated_at`, which the sampler stamps at
build time (§3, "Timestamps": the server computes durations, the page never
does wall-clock math against the viewer's clock).

`render_page` never raises. Every field in the §4 contract is nullable by
design ("the monitor runs when no run is active... 'no data' is a normal
state, not an error state"), so every accessor below goes through `.get()`
with a safe default, and idle / unknown / first-run / all-skipped / empty
docket are first-class render paths, not fallbacks bolted on afterward.

The `%`-formatting discipline (see PLAN.md §6 and `web/lm/chrome.py:235-237`):
every template below is a fixed literal containing only intentional `%s`/`%d`
placeholders. Dynamic values -- names, error text, anything that might
contain a literal `%` -- are always the ARGUMENT to a `%` operation, never
pasted into the template string itself, so they can never be misread as a
conversion spec.
"""

import html as _html
from datetime import datetime, timedelta

from monitor import bars
from monitor.styles import LOG_JS, PAGE_CSS, POLL_JS, THEME_JS, WARN_JS

# Target names share long prefixes (…_merged, …_merged_owner,
# …_merged_owner_clean) so a mid-string ellipsis would render three
# different targets identically. Truncate from the head, keep the tail.
NAME_TAIL_KEEP = 28

# The inline warning affordance (glyph + aria-label + title) is bounded on
# CHARACTER count, not lines -- R's warning text arrives from the box as one
# unbroken line with no newlines at all (MEASURED: 2048 chars for pacs_data,
# 550 for wcad_data_parsed, 275 for austin_parcel_data_merged_local, each a
# single line), so a line-based bound like `splitlines()[0]` returns the
# entire blob instead of a short excerpt. 72 sits in the middle of the
# 60-90 char range that reads as "a fragment" without being so short it's
# useless as a hint. See _render_warning.
WARN_SUMMARY_CHARS = 72

_STATE_LABELS = {
    "running": "Running",
    "idle": "Last run",
    "failed": "Failed",
    "unknown": "Unknown",
}

_SKIP_REASON_COPY = {
    "resume-gate-40mb": ("the on-disk owner file already looked complete "
                         "(past the resume threshold), so this run skipped "
                         "straight past it"),
    "nothing-left-to-ask": ("every parcel in scope already had an owner on "
                            "record, so there was nothing left to ask"),
}


def _e(value):
    """HTML-attribute/text escape. Never raises on None or a non-string."""
    if value is None:
        return ""
    return _html.escape(str(value), quote=True)


def _parse_iso(ts):
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError, TypeError):
        return None


def _elapsed_since(started_at_iso, generated_at_iso):
    started = _parse_iso(started_at_iso)
    now = _parse_iso(generated_at_iso)
    if started is None or now is None:
        return None
    return max((now - started).total_seconds(), 0.0)


def _fmt_clock(iso_ts):
    dt = _parse_iso(iso_ts)
    if dt is None:
        return "—"
    return _fmt_clock_dt(dt)


def _fmt_clock_dt(dt):
    # UTC, matching the contract's wire format; the page localizes for display.
    return dt.strftime("%H:%M:%S") + "Z"


def _clock_html(iso_ts):
    """A clock reading the browser rewrites into the viewer's own zone.

    Emits the UTC text so the page stays correct with scripting disabled, and
    carries the full ISO stamp in data-utc for the localize pass. The server
    never does wall-clock math against the viewer's clock, so skew between the
    box and the laptop cannot surface as a negative or jumping duration.
    """
    if not iso_ts:
        return "&mdash;"
    return '<time data-utc="%s">%s</time>' % (_e(str(iso_ts)), _e(_fmt_clock(iso_ts)))


def _clock_html_dt(dt, headline=False):
    """As _clock_html, but built from an already-parsed datetime (the idle
    headline computes `started_at + elapsed_seconds` itself rather than
    parsing a wire timestamp -- see _render_headline).

    `headline=True` adds `data-format="headline"`, an opt-in marker that
    only the headline's own clock sets. `localize()` (styles.py) branches
    on it to add a human-friendly day ("today"/"yesterday"/an explicit
    date) in front of the time -- the reader otherwise has no way to tell
    whether a last-run headline is reporting on today or last week. Every
    other `<time>` this module emits (docket rows, scrape/freshness rows)
    sits inside a single run's page and stays bare on purpose: a repeated
    date on every row would be noise, not information.
    """
    if dt is None:
        return "&mdash;"
    iso = dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    fmt_attr = ' data-format="headline"' if headline else ""
    return '<time data-utc="%s"%s>%s</time>' % (_e(iso), fmt_attr, _e(_fmt_clock_dt(dt)))


def _fmt_int(n):
    if n is None:
        return "—"
    return "{:,}".format(n)


def _fmt_bytes(n):
    if n is None:
        return "—"
    size = float(n)
    if size < 1024:
        return "%d B" % int(size)
    for unit in ("KB", "MB", "GB", "TB"):
        size /= 1024.0
        if size < 1024 or unit == "TB":
            return "%.1f %s" % (size, unit)
    return "%.1f TB" % size


def _head_truncate(name, keep=NAME_TAIL_KEEP):
    name = name or ""
    if len(name) <= keep:
        return name
    return "…" + name[-(keep - 1):]


def render_page(status):
    """Renders the full monitor page from a §4 Status dict."""
    status = status or {}
    run = status.get("run") or {}
    state = run.get("state") or "unknown"

    body = []
    body.append('<a class="pm-skiplink" href="#pm-main">Skip to status</a>')
    body.append(_render_masthead(state))
    body.append('<main id="pm-main" class="pm-wrap">')

    if state == "failed":
        band = _render_failure_band(status)
        if band:
            body.append(band)

    body.append(_render_headline(status))

    docket_panel = _render_docket_panel(status)
    scrape_panel = _render_scrape_panel(status)
    freshness_panel = _render_freshness_panel(status)

    body.append(docket_panel)
    if state == "idle":
        # §6 idle state: freshness is promoted above scrape.
        if freshness_panel:
            body.append(freshness_panel)
        if scrape_panel:
            body.append(scrape_panel)
    else:
        if scrape_panel:
            body.append(scrape_panel)
        if freshness_panel:
            body.append(freshness_panel)

    # Only worth explaining the docket's glyphs when there is a docket to
    # read them in -- an idle/empty/unknown page with no rows has nothing
    # for the legend to gloss.
    if status.get("docket"):
        body.append(_render_legend())
    body.append(_render_log_panel())
    body.append('</main>')
    # Outside #pm-main on purpose -- see _render_warning_dialog's docstring
    # for why a swap-vulnerable dialog is the exact bug 05cad93 already fixed
    # once, in a different element.
    body.append(_render_warning_dialog())

    generated_at = status.get("generated_at")
    body.append('<span id="pm-generated-at" data-generated-at="%s" hidden></span>'
               % _e(generated_at))
    body.append('<script>%s</script>' % THEME_JS)
    body.append('<script>%s</script>' % POLL_JS)
    body.append('<script>%s</script>' % LOG_JS)
    body.append('<script>%s</script>' % WARN_JS)

    return (
        "<!doctype html>\n"
        "<html lang=\"en\">\n"
        "<head>\n"
        "<meta charset=\"utf-8\" />\n"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />\n"
        "<title>Pipeline monitor — landlord-mapper</title>\n"
        "<style>%s</style>\n"
        "</head>\n"
        "<body>\n"
        "%s\n"
        "</body>\n"
        "</html>\n"
    ) % (PAGE_CSS, "".join(body))


def _render_masthead(state):
    live = state == "running"
    cls = "pm-head pm-head--live" if live else "pm-head"
    return (
        '<header class="%s"><div class="pm-wrap"><div class="pm-topline">'
        '<div class="pm-brand"><span class="pm-title">Pipeline monitor</span>'
        '<span class="pm-sub">landlord-mapper</span></div>'
        '<button id="pm-themebtn" class="pm-themebtn" type="button">Dark mode</button>'
        '</div></div></header>'
        % cls
    )


def _render_failure_band(status):
    docket = status.get("docket") or []
    problems = status.get("problems") or []
    errored = next((e for e in docket if e.get("state") == "errored"), None)

    target = (errored or {}).get("name") or "an unnamed target"
    error_text = (errored or {}).get("error") or ""
    if error_text:
        first_line = error_text.splitlines()[0]
    elif problems:
        first_line = problems[0].get("message") or "no error text recorded"
    else:
        first_line = "no error text recorded"

    elapsed = (errored or {}).get("seconds")
    dur = bars.format_duration_short(elapsed)

    return (
        '<div class="pm-failband"><p class="pm-fail-head">Failed</p>'
        '<p><span class="pm-m">%s</span> failed after <span class="pm-m">%s</span> — %s</p>'
        '</div>'
        % (_e(target), _e(dur), _e(first_line))
    )


def _render_headline(status):
    run = status.get("run") or {}
    progress = status.get("progress") or {}
    state = run.get("state") or "unknown"
    label = _STATE_LABELS.get(state, "Unknown")
    cls = "pm-state pm-state--%s" % state

    if state == "unknown":
        source = status.get("source") or {}
        host = source.get("host") or "the box"
        return (
            '<section class="pm-headline"><p class="%s">%s</p>'
            '<p class="pm-target" aria-live="polite">Cannot reach %s. The page cannot '
            'tell you whether a run is happening -- only that it cannot see one.</p>'
            '</section>'
            % (cls, _e(label), _e(host))
        )

    started_at = run.get("started_at")
    elapsed_seconds = run.get("elapsed_seconds")

    if state == "idle":
        finished_clock = None
        started_dt = _parse_iso(started_at)
        if started_dt is not None and elapsed_seconds is not None:
            finished_clock = _clock_html_dt(
                started_dt + timedelta(seconds=elapsed_seconds), headline=True)
        # headline_html carries markup (the <time> element), so it is assembled
        # from already-safe pieces and passed through unescaped. Escaping it
        # wholesale would print the tag at the reader instead of the time.
        if elapsed_seconds is not None:
            headline_html = "Finished %s, took %s" % (
                finished_clock or "an unknown time",
                _e(bars.format_duration_short(elapsed_seconds)),
            )
        else:
            headline_html = _e("No run recorded yet.")
        return (
            '<section class="pm-headline"><p class="%s">%s</p>'
            '<p class="pm-target" aria-live="polite">%s</p></section>'
            % (cls, _e(label), headline_html)
        )

    docket = status.get("docket") or []
    running_names = progress.get("running") or []
    current = None
    if running_names:
        current = next((e for e in docket if e.get("name") == running_names[0]), None)
    if current is None and docket:
        current = docket[-1]
    target_name = (current or {}).get("name") or "—"

    rows = []
    if started_at:
        rows.append(("Started", _clock_html(started_at)))

    if state == "running":
        rows.append(("Elapsed", bars.format_duration_short(elapsed_seconds)))
        eta_seconds = progress.get("eta_seconds")
        if eta_seconds is not None:
            rows.append(("Remaining", "~" + bars.format_duration_short(eta_seconds)))
        elif progress.get("eta_basis") == "none":
            rows.append(("Remaining", "not estimable yet"))
    else:  # failed
        rows.append(("Ran for", bars.format_duration_short(elapsed_seconds)))

    # Values here are built by this module (durations and _clock_html output),
    # never from probe data, so they go in as HTML rather than being escaped.
    dl_items = "".join(
        '<div><dt>%s</dt><dd class="pm-m">%s</dd></div>' % (_e(k), v)
        for k, v in rows
    )
    return (
        '<section class="pm-headline"><p class="%s">%s</p>'
        '<p class="pm-target" aria-live="polite">%s</p>'
        '<dl class="pm-timegrid">%s</dl></section>'
        % (cls, _e(label), _e(target_name), dl_items)
    )


def _render_empty_panel(title, message):
    return (
        '<section class="pm-panel"><div class="pm-panel-head"><h2>%s</h2></div>'
        '<div class="pm-empty"><p>%s</p></div></section>'
        % (_e(title), _e(message))
    )


def _render_docket_panel(status):
    docket = status.get("docket") or []
    progress = status.get("progress") or {}
    generated_at = status.get("generated_at")

    if not docket:
        return _render_empty_panel(
            "Docket",
            "No run recorded. The monitor reads /landlord_mapper_etl/_targets "
            "-- start a run and this fills in.",
        )

    axis_max = bars.compute_axis_max(docket)

    rows_html = []
    i, n = 0, len(docket)
    while i < n:
        entry = docket[i]
        if entry.get("state") == "skipped":
            j = i
            while j < n and docket[j].get("state") == "skipped":
                j += 1
            group = docket[i:j]
            if len(group) > 1:
                rows_html.append(_render_skip_group(group))
            else:
                rows_html.append(_render_row(group[0], axis_max, generated_at))
            i = j
        else:
            rows_html.append(_render_row(entry, axis_max, generated_at))
            i += 1

    targets_total = progress.get("targets_total")
    count_label = "%s entries" % (targets_total if targets_total is not None else len(docket))
    return (
        '<section class="pm-panel"><div class="pm-panel-head"><h2>Docket</h2>'
        '<span class="pm-count pm-m">%s</span></div>'
        '<div class="pm-docket">%s</div></section>'
        % (_e(count_label), "".join(rows_html))
    )


def _render_skip_group(group):
    first_seq = group[0].get("seq") or 0
    last_seq = group[-1].get("seq") or 0
    names = "".join(
        '<div class="pm-skiprow">%s  %s</div>'
        % (_e("%02d" % (e.get("seq") or 0)), _e(_head_truncate(e.get("name"))))
        for e in group
    )
    header = "%s–%s · %s entries skipped, unchanged since the last run" % (
        "%02d" % first_seq, "%02d" % last_seq, len(group),
    )
    # data-key survives the poll swap so an expanded group stays expanded.
    return '<details class="pm-skipgroup" data-key="skip-%s"><summary>%s</summary>%s</details>' % (
        first_seq, _e(header), names,
    )


def _char_truncate(text, keep=WARN_SUMMARY_CHARS):
    """Truncate on character count with an ellipsis. Never on lines -- see
    WARN_SUMMARY_CHARS for why a line-based bound (the defect this
    replaces) cannot work for this data."""
    text = text or ""
    if len(text) <= keep:
        return text
    return text[:keep].rstrip() + "…"


def _render_warning(entry):
    """The warning glyph, bounded regardless of how long the warning is.

    The previous fix (`warning_text.splitlines()[0]`) reused this page's
    `<details>` disclosure pattern (skip groups, the log panel) and assumed
    a warning's "first line" would be short. MEASURED against the live box,
    it is not: R emits each warning as a single unbroken line -- 2048 chars
    for pacs_data, with no newline anywhere in it -- so `splitlines()[0]`
    returned the entire blob rather than an excerpt, and that full blob
    both was the disclosure's collapsed `<summary>`/`title`/`aria-label`
    text (inflating the docket row) and had nowhere to wrap inside a
    `<details>` body sized for a short warning.

    Fix: bound the inline affordance -- summary glyph, aria-label, and
    title alike -- on CHARACTER COUNT via `_char_truncate` (there is no
    line structure to bound on), and move the FULL, unbounded text out of
    the row entirely and into a `<dialog>` this button opens on click.
    `<dialog>`/`showModal()` gives focus trapping, ESC-to-close, and a
    backdrop for free -- worth preferring here over another `<details>`
    because the body genuinely does not fit inline no matter how it wraps.

    The full text rides on this button as `data-warning-full`, never as
    visible text or as an unbounded `title` -- a data attribute has no
    layout or native-tooltip cost no matter how long the value is. WARN_JS
    (styles.py) reads it back out only at the moment the dialog opens, and
    populates the ONE shared dialog rendered once per page by
    `_render_warning_dialog` -- see that function for why the dialog itself
    lives outside `#pm-main` rather than being rendered per-row here.
    """
    warning_text = entry.get("warning_text") or "warning (no detail recorded)"
    summary = _char_truncate(warning_text)
    seq_key = entry.get("seq") if entry.get("seq") is not None else "x"
    name = entry.get("name") or "this target"
    return (
        ' <button type="button" class="pm-warn" data-key="warn-%s" '
        'data-warning-name="%s" data-warning-full="%s" '
        'aria-haspopup="dialog" aria-label="warning: %s" title="%s">⚠</button>'
        % (_e(seq_key), _e(name), _e(warning_text), _e(summary), _e(summary))
    )


def _render_warning_dialog():
    """One shared `<dialog>` for every docket row's warning button, rendered
    once per page and placed OUTSIDE `#pm-main` (see render_page).

    `styles.py`'s POLL_JS `swap()` replaces `#pm-main`'s entire innerHTML
    every ~3 seconds -- the exact mechanism that broke the log panel
    (05cad93). A dialog that lived inside `#pm-main` would be destroyed
    mid-read the instant a poll landed while a reader had it open, silently
    closing the modal out from under them. Living outside `#pm-main`
    (alongside the `pm-generated-at` span and the `<script>` tags, which
    are already placed after `</main>` for the same reason -- nothing there
    is meant to be swap-churned) means the swap structurally cannot reach
    it: there is no timing window to get wrong, and no restore-after-swap
    logic to write or to get wrong later.

    Empty and closed by default; WARN_JS fills in the title/body and calls
    `showModal()` only when a `.pm-warn` button is clicked, so this costs
    nothing on a page with no warnings at all.
    """
    return (
        '<dialog id="pm-warn-dialog" class="pm-warn-dialog" '
        'aria-labelledby="pm-warn-dialog-title">'
        '<div class="pm-warn-dialog-head">'
        '<h2 id="pm-warn-dialog-title"></h2>'
        '<button type="button" class="pm-warn-dialog-close" data-warn-close '
        'aria-label="Close">&times;</button>'
        '</div>'
        '<div id="pm-warn-dialog-body" class="pm-warn-dialog-body"></div>'
        '</dialog>'
    )


def _render_row(entry, axis_max, generated_at):
    seq = entry.get("seq")
    name = entry.get("name") or ""
    state = entry.get("state") or "unknown"
    seq_text = "%02d" % seq if seq is not None else "—"
    short_name = _head_truncate(name)

    if state == "skipped":
        return (
            '<div class="pm-row"><span class="pm-seq pm-m">%s</span>'
            '<span class="pm-name pm-m" title="%s">%s</span>'
            '<span class="pm-status"><span class="pm-skipword">· skipped</span></span></div>'
            % (_e(seq_text), _e(name), _e(short_name))
        )

    live_elapsed = None
    if state == "dispatched" and entry.get("started_at"):
        live_elapsed = _elapsed_since(entry.get("started_at"), generated_at)

    bar_svg = bars.docket_bar_svg(entry, axis_max, live_elapsed)

    if state in ("completed", "errored", "canceled"):
        actual_seconds = entry.get("seconds")
    elif state == "dispatched":
        actual_seconds = live_elapsed
    else:
        actual_seconds = None

    dur_text = bars.format_duration_short(actual_seconds)
    if state == "dispatched":
        dur_text = "running " + dur_text

    warn_html = _render_warning(entry) if entry.get("warning") else ""

    prior = entry.get("seconds_prior")
    ghost_line = ""
    if prior is not None:
        ghost_line = '<span class="pm-ghost-annotation pm-m">was %s</span>' % _e(
            bars.format_duration_short(prior))

    status_inner = (
        '<span class="pm-status-row">%s<span class="pm-dur pm-m">%s%s</span></span>%s'
        % (bar_svg or "", dur_text, warn_html, ghost_line)
    )

    return (
        '<div class="pm-row"><span class="pm-seq pm-m">%s</span>'
        '<span class="pm-name pm-m" title="%s">%s</span>'
        '<span class="pm-status">%s</span></div>'
        % (_e(seq_text), _e(name), _e(short_name), status_inner)
    )


def _render_scrape_panel(status):
    scrape = status.get("scrape")
    if scrape is None:
        # Only null when run.state == "unknown" (§4) -- the page is already
        # saying "cannot reach the box" in the headline, so there is nothing
        # honest to add here.
        return None

    phase = scrape.get("phase") or "unknown"

    if phase == "waiting":
        return _render_empty_panel("Scrape", "Not reached yet this run.")

    if phase == "unknown":
        return _render_empty_panel(
            "Scrape", "Scrape state unknown -- nothing parseable in the log window.")

    if phase == "skipped":
        reason_copy = _SKIP_REASON_COPY.get(
            scrape.get("skip_reason"), "the scrape was skipped this run")
        return _render_empty_panel("Scrape", "Skipped -- %s." % reason_copy)

    owner_keys = scrape.get("owner_keys")
    if owner_keys is None:
        # target 20 is running but the denominator line ('N parcels -> M
        # distinct owners') is outside the log window -- §5b: no bar at all,
        # position and part-file counts only.
        bits = []
        if scrape.get("pass") is not None and scrape.get("passes_total") is not None:
            bits.append("pass %s/%s" % (scrape.get("pass"), scrape.get("passes_total")))
        if scrape.get("chunk") is not None and scrape.get("chunks_total") is not None:
            bits.append("chunk %s/%s" % (scrape.get("chunk"), scrape.get("chunks_total")))
        part_files = scrape.get("part_files")
        if part_files is not None:
            bits.append("%s part file%s" % (_fmt_int(part_files), "" if part_files == 1 else "s"))
        summary = "Scrape running -- " + (", ".join(bits) if bits else "position not yet known") + "."
        return (
            '<section class="pm-panel"><div class="pm-panel-head"><h2>Scrape</h2></div>'
            '<p class="pm-scrape-summary">%s</p></section>' % _e(summary)
        )

    matched = scrape.get("matched") or 0
    no_record = scrape.get("no_record") or 0
    not_resolved = scrape.get("not_resolved") or 0
    outstanding = scrape.get("outstanding") or 0
    buckets_complete = bool(scrape.get("buckets_complete"))

    summary_bits = ["%s owner keys" % _fmt_int(owner_keys)]
    passes_total = scrape.get("passes_total")
    if passes_total is not None:
        summary_bits.append("%s pass%s" % (passes_total, "" if passes_total == 1 else "es"))
    workers_used = scrape.get("workers_used")
    if workers_used is not None:
        summary_bits.append("%s workers" % _fmt_int(workers_used))
    part_files = scrape.get("part_files")
    if part_files is not None:
        summary_bits.append("%s part file%s" % (_fmt_int(part_files), "" if part_files == 1 else "s"))
    summary = " · ".join(summary_bits)

    buckets = [
        '<div class="pm-bucket pm-bucket--matched"><span class="pm-bucket-v pm-m">%s</span>'
        '<span class="pm-bucket-k">Matched</span></div>' % _fmt_int(matched)
    ]

    note = ""
    if buckets_complete:
        buckets.append(
            '<div class="pm-bucket pm-bucket--norecord"><span class="pm-bucket-v pm-m">%s</span>'
            '<span class="pm-bucket-k">No filing on record</span></div>' % _fmt_int(no_record))
        buckets.append(
            '<div class="pm-bucket pm-bucket--failed"><span class="pm-bucket-v pm-m">%s</span>'
            '<span class="pm-bucket-k">Our query failed</span></div>' % _fmt_int(not_resolved))
        if outstanding:
            buckets.append(
                '<div class="pm-bucket pm-bucket--waiting"><span class="pm-bucket-v pm-m">%s</span>'
                '<span class="pm-bucket-k">Still asking</span></div>' % _fmt_int(outstanding))
    else:
        buckets.append(
            '<div class="pm-bucket pm-bucket--waiting"><span class="pm-bucket-v pm-m">%s</span>'
            '<span class="pm-bucket-k">Still asking</span></div>' % _fmt_int(outstanding))
        note = ('<p class="pm-scrape-note">Still on the first pass -- a filing-vs-no-filing '
               'split is not knowable yet. That is not the same as slow.</p>')

    pass_note = ""
    scrape_pass = scrape.get("pass")
    if scrape_pass is not None and passes_total is not None and scrape_pass > 1:
        pass_note = (
            '<p class="pm-scrape-note">Pass %s of %s -- re-asking owners the registry did '
            'not answer yet. The buckets above will not move while this runs.</p>'
            % (_e(scrape_pass), _e(passes_total))
        )

    return (
        '<section class="pm-panel"><div class="pm-panel-head"><h2>Scrape</h2></div>'
        '<p class="pm-scrape-summary">%s</p>'
        '<div class="pm-buckets">%s</div>%s%s</section>'
        % (_e(summary), "".join(buckets), note, pass_note)
    )


def _render_freshness_panel(status):
    freshness = status.get("freshness") or []
    if not freshness:
        return None

    items = []
    for f in freshness:
        name = f.get("name") or ""
        present = f.get("present")
        if present is None:
            present = f.get("bytes") is not None
        if not present:
            items.append(
                '<li class="pm-fresh-missing"><span class="pm-fresh-name pm-m">%s</span>'
                '<span class="pm-fresh-meta pm-m">not present</span></li>' % _e(name))
            continue
        size = _fmt_bytes(f.get("bytes"))
        mtime = f.get("mtime")
        clock = _clock_html(mtime) if mtime else "&mdash;"
        items.append(
            '<li><span class="pm-fresh-name pm-m">%s</span>'
            '<span class="pm-fresh-meta pm-m">%s · %s</span></li>'
            % (_e(name), _e(size), clock)
        )

    # The download carries exactly the files listed above that are present, so
    # the panel doubles as the manifest -- there is no second, invisible set.
    present = [f for f in freshness if f.get("present")]
    if present:
        total = sum(f.get("bytes") or 0 for f in present)
        # The size is the files as they sit on disk, not the size of the
        # download. Saying "before compression" read as an offer to skip
        # compressing; the note now states what happens instead of naming a
        # choice the reader does not have.
        noun = "file" if len(present) == 1 else "files"
        # Sits below the list rather than inside the panel head. In the head it
        # was an outlined box wedged against its own caption, which read as a
        # link rather than a control; on its own row under the files it acts
        # on, it has room to look like the button it is.
        action = (
            '<div class="pm-panel-action">'
            '<a class="pm-dl" href="/download/data.zip" '
            'title="Zipped as it downloads, so the file you receive is '
            'smaller than the figure shown">'
            'Download %d %s</a>'
            '<p class="pm-dl-note pm-m">%s of data, zipped as it '
            'downloads</p>'
            '</div>' % (len(present), noun, _e(_fmt_bytes(total)))
        )
    else:
        action = ''

    return (
        '<section class="pm-panel"><div class="pm-panel-head"><h2>Freshness</h2></div>'
        '<ul class="pm-fresh-list">%s</ul>%s</section>' % ("".join(items), action)
    )


def _render_log_panel():
    # data-key="log" is load-bearing, not decorative: POLL_JS's
    # openKeys()/restore() falls back to a positional index among
    # `#pm-main details` when an element has no data-key, and that index is
    # unstable here because the number of skip-group <details> elements
    # changes as the run progresses. Without a stable key, a poll swap can
    # restore "open" onto the wrong element -- or none -- which is exactly
    # how the log panel used to end up open-but-inert after a refresh.
    return (
        '<details class="pm-log" data-key="log"><summary>Log · last 40 lines</summary>'
        '<pre id="pm-log-body" data-loaded="false">Expand to load the tail of '
        'docker logs lm-pipeline.</pre></details>'
    )


def _render_legend():
    """A compact, always-visible key for the page's non-ASCII glyphs.

    Audited every one `render.py` emits: `⚠` (now self-explaining per-row,
    see `_render_warning`, but the glyph alone still means nothing to a
    first-time reader), the head-truncation `…` (already carries the full
    name in `title`, but nothing on the page says truncation is happening at
    all), and the placeholder `—`/`&mdash;` used wherever a value is simply
    not known yet (`bars.format_duration_short`, `_fmt_int`, `_clock_html`).
    The docket's `·` separators and `–` range dash (skip-group headers,
    scrape summary) are left unglossed on purpose -- they sit in running
    prose next to the words they separate ("· skipped", "01–19 · 19 entries
    skipped") and are self-evident from that context; explaining a plain
    separator would be the glyph-legend equivalent of a filler word.
    """
    return (
        '<p class="pm-legend pm-m">Key: '
        '<span class="pm-warn">⚠</span> has a warning, tap to open the full text '
        '&nbsp;·&nbsp; '
        '&hellip; name shortened, full name on hover or tap &nbsp;·&nbsp; '
        '&mdash; not known yet'
        '</p>'
    )
