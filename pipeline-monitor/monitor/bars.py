"""Inline-SVG ghost/actual bars for docket rows -- PLAN.md §6, "the signature
element".

Follows `web/lm/netdiagram.py`'s idiom: `viewBox` with no fixed width or
height, elements carry `class` attributes only, and zero colour literals
appear below -- every fill and stroke is `var(--...)` in `monitor/styles.py`,
so theming and dark mode come free. Nothing here reads a clock or does I/O;
callers pass in whatever "now" they already computed.

The axis is deliberately NOT re-derived per bar. `compute_axis_max` is called
once by the caller (render.py) over the whole visible docket and the same
`axis_max` is threaded into every `docket_bar_svg` call, which is what keeps
the column frozen for the run instead of rescaling on every live bar's growth
(PLAN.md §6, "the axis must not float").
"""

import html

# viewBox is a coordinate space, not pixels -- CSS scales the rendered <svg>
# to the row's actual width, so "3px minimum" (PLAN.md §6) is expressed here
# as a fraction of this constant rather than a literal pixel count.
VB_W = 200
VB_H = 24
BAR_Y = 4
BAR_H = 14
TICK_WIDTH = 3          # viewBox units -- the "distinct tick" width
TICK_MIN_FRAC = TICK_WIDTH / VB_W
CAP_WIDTH = 3           # hatched cap width drawn at the axis edge on overrun
OVERRUN_MIN_PRIOR_SECONDS = 5   # "no overrun treatment where prior < 5s"

_TERMINAL_ACTUAL_STATES = ("completed", "errored", "canceled")


def format_duration_short(seconds):
    """'19m 02s' / '1h 04m 12s' / '42s' / '1.5s'. None -> an em dash."""
    if seconds is None:
        return "—"
    seconds = max(0.0, float(seconds))
    if seconds < 10:
        return "%.1fs" % seconds
    total = int(round(seconds))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return "%dh %02dm %02ds" % (hours, minutes, secs)
    if minutes:
        return "%dm %02ds" % (minutes, secs)
    return "%ds" % secs


def format_duration_prose(seconds):
    """'19 minutes 2 seconds' / '42 seconds' / '1.5 seconds'. None -> 'unknown'."""
    if seconds is None:
        return "unknown"
    seconds = max(0.0, float(seconds))
    if seconds < 10:
        return "%.1f seconds" % seconds
    total = int(round(seconds))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    parts = []
    if hours:
        parts.append("%d hour%s" % (hours, "" if hours == 1 else "s"))
    if minutes:
        parts.append("%d minute%s" % (minutes, "" if minutes == 1 else "s"))
    if secs or not parts:
        parts.append("%d second%s" % (secs, "" if secs == 1 else "s"))
    return " ".join(parts)


def compute_axis_max(docket):
    """max(ghosts ∪ completed actuals) over non-skipped rows.

    Skipped rows contribute nothing -- they carry no actual this run and the
    design deliberately excludes their ghosts too, so expanding the collapsed
    skip-group summary never rescales the column. Returns None when nothing
    in the docket is measurable (e.g. a first run with no prior durations and
    nothing finished yet).
    """
    candidates = []
    for entry in docket or []:
        if entry.get("state") == "skipped":
            continue
        prior = entry.get("seconds_prior")
        if prior is not None and prior > 0:
            candidates.append(prior)
        if entry.get("state") in _TERMINAL_ACTUAL_STATES:
            seconds = entry.get("seconds")
            if seconds is not None and seconds > 0:
                candidates.append(seconds)
    if not candidates:
        return None
    return max(candidates)


def _clamp_frac(frac):
    return min(max(frac, 0.0), 1.0)


def _aria_label(entry, actual_seconds, is_live, is_failed):
    name = entry.get("name") or "this target"
    prior = entry.get("seconds_prior")

    if is_failed:
        first_line = (entry.get("error") or "").splitlines()[:1]
        err = first_line[0] if first_line else "no error text recorded"
        return "%s failed after %s: %s" % (name, format_duration_prose(actual_seconds), err)

    verb = "running" if is_live else "ran"
    base = "%s %s %s" % (name, verb, format_duration_prose(actual_seconds))
    if prior is None:
        return base + ", no previous run to compare against"
    if actual_seconds is None:
        return base + (", previous run took %s" % format_duration_prose(prior))
    delta = actual_seconds - prior
    if abs(delta) < 1:
        return base + (", matching the previous run's %s" % format_duration_prose(prior))
    if delta > 0:
        return base + (", %s over the previous run's %s"
                       % (format_duration_prose(delta), format_duration_prose(prior)))
    return base + (", %s under the previous run's %s"
                   % (format_duration_prose(-delta), format_duration_prose(prior)))


def docket_bar_svg(entry, axis_max, live_elapsed_seconds=None):
    """Renders one docket row's ghost/actual bar, or None if there is nothing
    to draw (no shared axis yet, or the row has no seconds at all).

    entry: a PLAN.md §4 docket item (name, state, seconds, seconds_prior,
        warning, error). state is one of skipped|dispatched|completed|
        errored|canceled -- skipped rows never reach this function, the
        caller renders them as plain text.
    axis_max: the frozen shared-axis maximum in seconds (see
        `compute_axis_max`), or None.
    live_elapsed_seconds: elapsed time for a still-running (`dispatched`)
        target. Ignored for finished states, where entry["seconds"] is
        authoritative.
    """
    if axis_max is None or axis_max <= 0:
        return None

    state = entry.get("state")
    seconds_prior = entry.get("seconds_prior")
    seconds = entry.get("seconds")
    is_failed = state == "errored"
    is_live = state == "dispatched"

    if state in _TERMINAL_ACTUAL_STATES:
        actual_seconds = seconds
    elif is_live:
        actual_seconds = live_elapsed_seconds
    else:
        actual_seconds = None

    ghost_frac = None
    if seconds_prior is not None and seconds_prior > 0:
        ghost_frac = _clamp_frac(seconds_prior / axis_max)

    if actual_seconds is None and ghost_frac is None:
        return None

    parts = []
    aria = _aria_label(entry, actual_seconds, is_live, is_failed)
    parts.append('<svg class="pm-bar" viewBox="0 0 %d %d" role="img" aria-label="%s">'
                 % (VB_W, VB_H, html.escape(aria, quote=True)))

    if ghost_frac is not None:
        ghost_w = ghost_frac * VB_W
        parts.append('<rect class="pm-ghost" x="0" y="%d" width="%.2f" height="%d" />'
                     % (BAR_Y, ghost_w, BAR_H))

    if actual_seconds is not None and actual_seconds > 0:
        actual_frac_raw = actual_seconds / axis_max
        capped_frac = min(actual_frac_raw, 1.0)
        overrun_ok = (seconds_prior is not None
                     and seconds_prior >= OVERRUN_MIN_PRIOR_SECONDS
                     and ghost_frac is not None)
        state_cls = "pm-failed" if is_failed else ("pm-live" if is_live else "pm-done")

        base_frac = min(capped_frac, ghost_frac) if overrun_ok else capped_frac
        base_w = base_frac * VB_W

        if base_w < TICK_WIDTH:
            parts.append('<rect class="pm-tick %s" x="0" y="%d" width="%d" height="%d" />'
                         % (state_cls, BAR_Y, TICK_WIDTH, BAR_H))
        else:
            parts.append('<rect class="pm-actual %s" x="0" y="%d" width="%.2f" height="%d" />'
                         % (state_cls, BAR_Y, base_w, BAR_H))
            if is_live:
                pulse_x = max(base_w - 2, 0)
                parts.append('<rect class="pm-pulse" x="%.2f" y="%d" width="2" height="%d" />'
                             % (pulse_x, BAR_Y, BAR_H))

        if overrun_ok and capped_frac > ghost_frac:
            over_start = ghost_frac * VB_W
            over_end = capped_frac * VB_W
            parts.append('<rect class="pm-overrun %s" x="%.2f" y="%d" width="%.2f" height="%d" />'
                         % (state_cls, over_start, BAR_Y, max(over_end - over_start, 0), BAR_H))

        if actual_frac_raw > 1.0:
            parts.append('<rect class="pm-cap %s" x="%d" y="%d" width="%d" height="%d" />'
                         % (state_cls, VB_W - CAP_WIDTH, BAR_Y, CAP_WIDTH, BAR_H))
            if seconds_prior:
                ratio = actual_seconds / seconds_prior
                parts.append('<text class="pm-ratio" x="%d" y="%d">%s</text>'
                             % (VB_W, VB_H - 2, "%.1f×" % ratio))

    if is_failed:
        stamp_y = 0
        parts.append(
            '<g class="pm-stamp">'
            '<rect class="pm-stamp-box" x="0" y="%d" width="46" height="11" />'
            '<text class="pm-stamp-text" x="4" y="%d">FAILED</text>'
            '</g>'
            % (stamp_y, stamp_y + 8)
        )

    parts.append('</svg>')
    return "".join(parts)


