# Plan — `pipeline-monitor`: a locally hosted live view of the landlord-mapper pipeline

**Branch:** `grouping-and-living-area-2026-08-05`
**New tracked directory:** `pipeline-monitor/` (top level, sibling of `web/`)
**Written:** 2026-08-06

---

## 0. What this is, in one paragraph

A locally hosted web page, served from the compute box, that shows what the landlord-mapper
pipeline is doing right now: which `targets` step is running, how long it has taken, how much
longer it should take, whether the scrape is mid-flight, and when the data was last refreshed.
The same status data is exposed over an MCP server so agents can ask "is the pipeline still
running" or "when did we last scrape" without SSHing anywhere or being told to poll a log.
It ships as its own tracked project inside the fork repo and **never touches `web/` or Railway.**

---

## 1. The decision that was blocking everything, and why it is now settled

Three shapes were possible, and one had to be chosen before any code was written:

1. richer *static* status page on the public site, off the SQLite `meta` snapshot
2. box publishes JSON, the Railway app reads it (live-ish, public)
3. box-local live dashboard, LAN only

**This plan is option 3, and option 3 only.** The instruction was "a locally hosted page that
displays while scraping/processing takes place" with "an MCP making the data in it accessible to
agents." That is unambiguously the box-local live view, not a public-site change.

Everything that made options 1 and 2 hazardous therefore does not apply here:

- no `LM_DATA_VERSION` change, so no ~400 MB re-seed and no silent-stale-volume failure mode
- no `railway up`, no Railway deploy dir, no serverless container being woken and billed
- **no edit to `web/`**, so the "three copies byte-identical" invariant is satisfied trivially
  rather than by discipline. This is the single most important structural choice in the plan:
  drift between the three web copies caused three separate defects in one day, and the cheapest
  way to not cause a fourth is to not touch that tree at all.

---

## 2. What was verified live on the box (not assumed)

A pipeline run was in flight while this plan was written, which let every load-bearing
assumption be checked against reality instead of inferred.

| Fact | Verified how | Why it matters |
| --- | --- | --- |
| `lm-pipeline` container runs `R -q -e targets::tar_make(callr_function = NULL)`, workdir `/landlord_mapper_etl` | `docker inspect` | targets runs in-process; the meta store is the progress channel |
| `/landlord_mapper_etl` **is** the `lm_work` volume, host path `/media/cam/ImageProcessing/docker/volumes/lm_work/_data` | `docker inspect` mounts | the monitor can read the pipeline's state directly from the host filesystem |
| That host path is **root-owned; `cam` gets Permission denied** | `ls` as `cam` | reads need `sudo -n` (verified passwordless) or `docker exec` |
| `sudo -n true` succeeds | direct test | the sudo read path is available without an interactive prompt |
| `cam` can run `docker ps` / `docker logs` without sudo | direct test | the log channel needs no privilege escalation |
| `_targets/meta/progress` is **plain pipe-delimited text**, header `name\|type\|parent\|branches\|progress` | `cat` mid-run | **no R runtime needed** — a Python parser is sufficient. This is the finding the whole design rests on |
| `progress` is append-only: a target appears as `dispatched`, then again as `completed` | observed 27 lines mid-run | in-flight target = last `dispatched` with no matching later `completed` |
| Observed states so far: `skipped`, `dispatched`, `completed` | live file | `errored` / `canceled` exist in targets and must be handled even though unobserved |
| `_targets/meta/meta` is pipe-delimited with per-target `seconds`, `bytes`, `time`, `warnings`, `error` | `head`/`tail` mid-run | **per-target historical durations exist** — real ETA is possible, not a fake spinner |
| `meta.time` encodes as `t20671.5135357477s` = days-since-epoch with fractional day | decoded and cross-checked against a known 1142 s target that started 12:00 and ended 12:19 UTC | timestamps are recoverable |
| `meta.warnings` holds long free text with non-ASCII (info glyph, typographic quotes) but **no embedded pipe** | field-count histogram: every row is exactly 3 or 18 fields | parse as UTF-8; the real hazard is the 3-vs-18 field mix, **not** delimiters in warnings |
| `_targets/meta/process` gives `pid`, `created`, `version_targets`, `version_r` | `cat` | run start time and identity, without shelling out |
| `docker logs lm-pipeline` works and the driver is `json-file` | `docker inspect` | log tail is available via the docker CLI as `cam` |
| Log emits `✔ <name> completed [19m 2.6s, 125.43 MB]` and `+ <name> dispatched` | live tail | the pattern is **not** "completed target" |
| Log emits `[owner_scrape] status split over 1561 owner keys after 3 pass(es): matched 10, no_record 1512, not_resolved 39` | live tail | the scrape's own outcome line, parseable |
| Log emits `[consolidate_owner_parts] folded 6 part file(s) -> 153538 rows` | live tail | part-file fold is observable |
| `owner_data_part_*.csv` count was **0** at observation time | `ls` in container | part files exist **only during** the scrape; their absence is not an error |
| Box has Python **3.12.3** at `/usr/bin/python3` | `python3 -V` | matches `web/Dockerfile`'s `python:3.12-slim`; target 3.12 |
| Box ports in use include 8787 (Shiny UI), 4080, 3050, 9877, 18004, 15672, 5432, 5672, 6379 | `ss -tlnp` | 8098 is free for the monitor |
| `~/landlord-mapper-ui` on the box runs an **R Shiny** app on 8787 | `docker inspect` | not the Python `web/` tree; do not conflate the two |
| The fork has **one uncommitted change**: `scrape_helper_functions.R` `property_units>5` → `>4` | `git diff` | the rescrape fix is done but **not committed** — flagged, not this task's job |
| The in-flight run already **completed** its scrape (`1561` owner keys, 3 passes) and is now in downstream processing | log tail | the rescrape landed |
| The pipeline declares **27 targets**; target 20 `austin_parcel_data_merged_owner` is the multi-hour scrape | `_targets.R:170-379` | the docket's denominator |
| `targets_total = 27` confirmed independently as the count of distinct `type=="stem"` names in a compacted `meta` | `completed-meta.psv` | `progress` alone **cannot** supply this mid-run — 4 targets had not yet appeared in the mid-run capture |
| The run **completed successfully**, ending `owners_data_total_supp completed`, with `2 targets produced warnings` and an `NAs introduced by coercion` warning | `completed-logs-tail.txt` | gives a real idle-state fixture *and* a real warning fixture, so `problems` is testable without fabrication |

### Four facts from the R source that changed the design

Read out of `scrape_helper_functions.R`; these are the difference between a progress bar that works
during the scrape and one that sits frozen for two hours.

1. **`owner_data_total.csv` is NOT a live signal.** `consolidate_owner_parts()` runs exactly twice
   per scrape — once at entry (folding in crash leftovers, `:2177`) and once at the end (`:2594`).
   In between, the file does not move. A monitor watching it would show a flat line for the entire
   multi-hour scrape and then a single jump. **The live signal is the part files.**
2. **Part files are keyed by worker PID, not by chunk or worker slot.**
   `sprintf('owner_data_part_%d.csv', Sys.getpid())` (`:2472`, `:2551`). The pool is destroyed and
   recreated *every chunk*, so every chunk produces a fresh set of PIDs. Part-file count therefore
   grows with chunks × passes, and is **not** an approximation of worker count. They are appended
   to live (`fwrite(append = file.exists(...))`), header written once.
3. **The scrape's real denominator only exists in the log.** One HTTP lookup happens per *distinct
   owner*, not per parcel. `[owner_scrape] N parcels -> M distinct owners, W workers` (`:2267`)
   emits `M` once at scrape start; the per-pass line (`:2516`) and per-chunk line (`:2494`) emit
   the shrinking `pending`. So scrape sub-progress is **parsed from logs, not from the filesystem.**
   `W` is the *clamped* worker count (`:2239` clamps `SCRAPE_WORKERS` against free R connections),
   so it must be read, not assumed to be 16.
4. **The resume gate can skip the scrape silently.** If `owner_data_total.csv` exists and is
   ≥ 40,000,000 bytes, the entire scrape body is skipped (`:2178`) and **no `[owner_scrape]` line
   is ever printed.** Target 20 then completes almost instantly. A monitor must read
   "target 20 running, no scrape line within a few seconds" as **scrape skipped**, not as stalled —
   otherwise the page's most alarming state is also its most common one.

`SCRAPE_WORKERS = 16` (`:1933`), `SCRAPE_CHUNK = 10000` (`:1994`), `SCRAPE_RETRY_PASSES = 3`
(`:2018`) are hardcoded R constants, **not environment-overridable**. The monitor parses observed
values from logs rather than hardcoding a second copy that can silently drift.

Fixtures captured from the live run (perishable, now preserved) in `tests/fixtures/` —
`progress.psv` (27 lines),
`meta.psv` (219 lines), `process.psv` (5 lines), `pipeline-logs-tail.txt` (171 lines).
**These are copied into the project as test fixtures so every parser is testable with no box access.**

---

## 3. Architecture

One rule drives the whole shape: **parse once, serve twice.**

```
     ┌──────────────── the box (cam-cloudripper) ────────────────┐
     │                                                            │
     │   lm_work volume            docker logs lm-pipeline        │
     │   ├─ _targets/meta/progress        │                       │
     │   ├─ _targets/meta/meta            │                       │
     │   ├─ _targets/meta/process         │                       │
     │   ├─ owner_data_part_*.csv         │                       │
     │   └─ owner_data_total.csv          │                       │
     │            │                       │                       │
     │            ▼                       ▼                       │
     │        probe.py  ──────────►  parse.py   (pure functions)  │
     │            │                                               │
     │            ▼                                               │
     │         state.py  ──►  Status  ──►  status.to_json()       │
     │            │                                               │
     │            ▼                                               │
     │   server.py  (stdlib ThreadingHTTPServer, port 8098)       │
     │     GET /            → the page (HTML)                     │
     │     GET /api/status  → the Status JSON  ◄── THE CONTRACT   │
     │     GET /api/logs    → tail JSON                           │
     │     GET /healthz     → liveness                            │
     └────────────────────────────┬───────────────────────────────┘
                                  │ HTTP over LAN
              ┌───────────────────┴────────────────────┐
              ▼                                        ▼
      browser (operator)                     mcp_server.py (laptop, stdio)
      polls /api/status every 3s             6 tools, thin HTTP client
```

`Status` JSON is the single contract. The page and the MCP are both *renderers* of it and share
no other code. That is what lets the implementation fan out in parallel, and it means a future
consumer (a terminal view, a phone card) costs nothing new.

### Why not the alternatives

- **Run the monitor in a container with `lm_work` mounted read-only.** Cleaner privilege story,
  but it loses `docker logs` (the scrape's own outcome lines live only there), and mounting the
  docker socket into a LAN-exposed service to get them back is a worse trade than `sudo -n` on a
  directory allowlist plus a filename regex. Rejected, with the mitigation below.
- **Have the pipeline write its own status JSON.** Requires editing `scrape_helper_functions.R`,
  which is mid-rescrape and already carries an uncommitted change. Adding a writer to a hot R file
  to serve a read-only dashboard inverts the risk. The pipeline should stay unaware it is watched.
  **Nothing in this plan modifies a single R file.**
- **Use the `mcp` Python SDK.** It would be the repo's first dependency, in a repo that has
  deliberately zero. MCP's stdio surface needed here is `initialize` / `tools/list` / `tools/call`,
  which is a few hundred lines of stdlib JSON-RPC. Flagged in §9 as the plan's most debatable call.

### Security posture (a LAN service that calls `sudo`)

This is the sharp edge and it gets treated as such.

**Preferred posture: grant read access once, then never use sudo.** `cam` has blanket passwordless
sudo, so an app-level path allowlist is courtesy, not containment — anything that gets code
execution in a LAN-bound monitor process has root anyway. The structurally better fix is a one-time
root action that makes the volume readable, after which the monitor runs unprivileged forever:

```sh
V=/media/cam/ImageProcessing/docker/volumes/lm_work/_data

# Traverse first -- see the note below; without this the rest is unreachable.
sudo setfacl -m u:cam:x /media/cam
sudo setfacl -m u:cam:x /media/cam/ImageProcessing/docker

sudo setfacl -R    -m u:cam:rX "$V" "$V/_targets"   # existing files
sudo setfacl -R -d -m u:cam:rX "$V" "$V/_targets"   # DEFAULT ACL: inherited by NEW files
```

**The traverse grants are not optional, and omitting them fails silently.** The volume sits beneath
root-only directories (`/media/cam` at `750 root:root`, and `.../docker` at `drwx--x---`), so
without execute on each of them the leaf grant cannot be reached at all. `setfacl` on the leaf still
**exits 0**, and `getfacl` on it returns "Permission denied", which reads exactly like the ACL never
applied. Verified on the box: after adding the two traverse grants, `cam` reads
`_targets/meta/progress` with no sudo, and a newly created root-owned file inherits `user:cam:r-x`.

**The `-d` default ACL is not optional and is the whole reason this works.** An access-only ACL
covers the files that exist at the moment it is run. `targets` recreates `_targets/meta/progress`
at run start (new inode) and every `owner_data_part_<pid>.csv` is a brand-new root-created file —
none of which inherit an access-only ACL. Without `-d`, the monitor's two headline sources go
Permission-denied the moment a run begins, and the page degrades to `unknown` at exactly the moment
it is supposed to be useful. This is a failure mode that would pass every test run against a stale
volume and fail every real one.

`run.sh` verifies readability by **creating nothing and checking the default-ACL entry exists**
(`getfacl -d`), not merely by reading today's files — a check that only reads existing files cannot
distinguish the working configuration from the broken one. If the grant is missing it prints the
exact commands above for an operator to run, rather than silently escalating. **`sudo -n` reads are the
documented fallback**, not the default.

Whichever path is used:

- The allowlist is **directories plus a filename regex**, not a fixed list of absolute files —
  because part files are named `owner_data_part_<pid>.csv` with unpredictable PIDs, so a fixed
  import-time file list cannot express them. Listing is `sudo -n ls <allowlisted dir>`; every
  returned name is validated against `^owner_data_part_\d+\.csv$` before it is read.
  This is called out explicitly because it is exactly the spot where an implementer reaches for
  `shell=True` to glob.
- All subprocess calls use a **fixed argv list**. Never `shell=True`, never string interpolation,
  never a glob expanded by a shell.
- The server binds to `LM_MONITOR_HOST`, default `0.0.0.0` for LAN use, with `127.0.0.1` documented
  as the option for reaching it over an SSH tunnel instead.
- Every endpoint is `GET` and read-only. No route writes, deletes, or runs anything. The monitor
  cannot start, stop, or disturb a run — by construction, not by policy.
- `cpa_key.txt` sits in the same directory and is **never** readable through the monitor. A test
  asserts no allowlisted read can resolve to a name matching `key|secret|cred|token`.

### A background sampler, not per-request probing

The browser polls every 3 s and every MCP tool call would otherwise re-probe. Each probe means
several subprocesses plus a `docker logs` read of a log file that grows unboundedly over a
multi-hour verbose R run — that cost cannot sit on the request path.

**One sampler thread refreshes a cached `Status` every 3 s; every endpoint serves the cache.**
`generated_at` already exposes cache age, and the page shows it when the cache goes stale (sampler
wedged or box unreachable). The sampler backs off to 30 s when no run is active, since nothing is
moving.

### Anchoring the log to the current run

`docker logs` on a container that has been restarted rather than recreated contains the *previous*
run's `[owner_scrape]` section. Without anchoring, the monitor would present a finished scrape from
a prior run as the current one. **All log reads use `docker logs --since <process.created>`**, and
the parser additionally discards everything before the last `tar_make(` banner as a belt-and-braces
second anchor.

### Deriving `run.state`

Never inferred from one signal. If R dies hard, `progress` keeps a `dispatched` row with no
`completed` forever, so the filesystem alone says "running" indefinitely.

| container | newest progress row | → `run.state` |
| --- | --- | --- |
| running | a `dispatched` with no later `completed` | `running` |
| running | all rows terminal | `running` (between targets) |
| exited 0 | all rows terminal | `idle` |
| exited non-zero, or any `errored` row | any | `failed` |
| exited 0 | a dangling `dispatched` | `failed` (died mid-target) |
| container absent | any | `idle`, with the docket shown as the last known run |
| volume unreadable | — | `unknown`, and the page says it cannot see the box |

`unknown` and `idle` are **never** conflated. "I cannot see it" and "it is not running" are
different sentences and the page prints different ones.

### Timestamps

Every timestamp inside the pipeline is **UTC** (`process.created` is naive-UTC; `meta.time` decodes
to UTC). The contract carries UTC ISO-8601 with a `Z`. **The server computes all durations**
(`elapsed_seconds`, `eta_seconds`); the page never does wall-clock math against the viewer's clock,
so laptop/box skew cannot produce negative or jumping durations. The page localizes for display
only.

---

## 4. The `Status` contract (frozen — every parallel track builds against this)

```jsonc
{
  "schema": 1,
  "generated_at": "2026-08-06T12:52:03Z",     // when the monitor built this
  "source": { "host": "cam-cloudripper", "volume": "/landlord_mapper_etl", "reachable": true },

  "run": {
    "state": "running",          // running | idle | failed | unknown
    "pid": 1,
    "started_at": "2026-08-06T12:00:01Z",
    "elapsed_seconds": 3122,
    "targets_version": "1.11.4",
    "r_version": "4.5.2",
    "container": { "name": "lm-pipeline", "up": true, "started_at": "2026-08-06T12:00:01Z" }
  },

  "docket": [                     // one entry per target, in first-appearance order
    {
      "seq": 20,
      "name": "austin_parcel_data_merged_owner",
      "state": "completed",       // skipped | dispatched | completed | errored | canceled
      "seconds": 1142.579,        // this run; null if not finished
      "seconds_prior": 1268.0,    // same target, previous run, from meta history; null if unknown
      "bytes": 125428535,
      "started_at": "2026-08-06T12:00:04Z",
      "finished_at": "2026-08-06T12:19:07Z",
      "warning": true,            // meta.warnings non-empty
      "error": null
    }
  ],

  "progress": {
    // DISJOINT: completed + skipped + running + waiting == targets_total (27)
    "targets_total": 27,          // distinct type=="stem" names in meta
    "completed": 3,               // ran to completion THIS run (excludes skipped)
    "skipped": 19,
    "waiting": 4,                 // not yet dispatched
    "running": ["austin_parcel_data_merged_owner_clean"],   // length 1 here
    "fraction": 0.81,             // by ESTIMATED TIME, not target count — see §5
    "eta_seconds": 3840,          // null when not estimable
    "eta_basis": "prior-run-durations-provisional"
    // one of: "prior-run-durations" | "prior-run-durations-provisional" | "none"
  },

  // NOT null merely because no scrape was observed: `waiting` and `skipped` are states that
  // must carry a phase so the page can explain them. Null ONLY when run.state == "unknown".
  "scrape": {
    "phase": "done",              // waiting | running | skipped | done | unknown
    "skip_reason": null,          // "resume-gate-40mb" | "nothing-left-to-ask" | null

    "owner_keys": 1561,           // denominator, from ':2267'. null until that line appears

    // Three DISJOINT buckets that sum to owner_keys. This replaces "fraction".
    // See §5b — a single percentage cannot be stated honestly here.
    "matched": 10,                // determined: a filing was found
    "no_record": 1512,            // determined: Texas has no filing. A FINDING, not a miss
    "not_resolved": 39,           // determined-final: our query failed. Only known at the end
    "outstanding": 0,             // not yet determined; still being asked
    "buckets_complete": true,     // false while pass 1 runs (no_record not yet separable)

    "pass": 3, "passes_total": 3, // observed, not assumed from the R constant
    "chunk": 1, "chunks_total": 1,
    "workers_used": 16,           // the CLAMPED value from the log, not SCRAPE_WORKERS
    "part_files": 0,              // independent liveness signal: owner_data_part_<pid>.csv
    "part_bytes": 0,
    "rss_gb": 2.9,                // newest value parsed from a chunk line
    "consolidated_rows": 153538,
    "owner_data_total": { "bytes": 27855434, "mtime": "2026-08-06T12:16:00Z" },
    "resume_gate": { "threshold_bytes": 40000000, "would_skip": false }
  },

  "freshness": [                  // artifact → when it was last written; missing files appear
    { "name": "owner_data_total.csv", "bytes": 27855434, "mtime": "...", "present": true },
    { "name": "owner_scrape_unresolved.csv", "bytes": 0, "mtime": null, "present": false },
    { "name": "austin_parcel_data_merged.csv", "bytes": 106302979, "mtime": "..." },
    { "name": "parcel_roll_5county.csv", "bytes": null, "mtime": null, "present": false },
    { "name": "parcel_group_assign.csv", "bytes": null, "mtime": null, "present": false }
  ],

  "problems": [                   // anything the operator should look at, newest first
    { "level": "warning", "target": "austin_parcel_data_merged_owner",
      "message": "UNRELIABLE VALUE: ... doFuture ... random numbers ..." }
  ]
}
```

Rules that are part of the contract, not suggestions:

- **Every field is nullable and the renderers must survive nulls.** The monitor runs when no run
  is active, when the container is gone, when `_targets/` does not exist yet, and when the volume
  is unreadable. "No data" is a normal state, not an error state.
- **The scrape buckets are never merged.** `no_record` means Texas has no filing — a finding.
  `not_resolved` means our query failed — an unknown. Folding them was a shipped defect once
  already (fixed in `7ac8424`). Both the page and the MCP must keep them distinct and label them
  in those terms.
- `schema: 1` is present from day one so a future change is detectable by a consumer.

---

## 4b. Function signatures and the other two endpoints (frozen)

§10 fans out five tracks against "A's signatures". Those signatures are here. **A track may not
change a signature in this section without the change being propagated first** — this is the
document's integration contract, and it exists because tracks A and B otherwise collide on exactly
the three ambiguities this plan got wrong in its first draft.

### `monitor/parse.py` — pure, no I/O, no clock

```python
def parse_progress(text: str) -> list[dict]:
    """`_targets/meta/progress`. Returns rows in file order, dropping a trailing partial line.
    Each: {"name": str, "type": str, "parent": str, "branches": int, "progress": str}
    `progress` is one of: skipped|dispatched|completed|errored|canceled (unknown values pass
    through verbatim rather than raising)."""

def latest_progress_by_name(rows: list[dict]) -> dict[str, str]:
    """Append-only file collapsed to last-state-wins. name -> progress."""

def parse_meta(text: str) -> list[dict]:
    """`_targets/meta/meta`. Accepts rows of 3 fields (name|type|data, for type in
    {object, function}) and 18 fields (full records); anything else is skipped, not raised.
    3-field rows return {"name","type","data"} only.
    18-field rows additionally return: seconds: float|None, bytes: int|None,
    time: datetime|None (UTC, decoded), warnings: str|None, error: str|None, format: str.
    Input is decoded UTF-8; warning text contains non-ASCII."""

def decode_targets_time(token: str) -> datetime | None:
    """'t20671.5135357477s' -> aware UTC datetime. Fractional DAYS since the Unix epoch.
    Returns None for '' or an unparseable token."""

def parse_process(text: str) -> dict:
    """`_targets/meta/process`. {"pid": int, "created": datetime(UTC),
    "version_targets": str, "version_r": str}. Missing keys -> None."""

def split_meta_runs(meta_rows: list[dict], created: datetime) -> dict[str, dict]:
    """THE load-bearing selector. meta is append-only with multiple rows per target.
    Returns name -> {"current": row|None, "prior": row|None} where a row is `current`
    iff row["time"] >= created, and `prior` is the newest row strictly older than `created`.
    Only type == "stem" rows are considered."""
```

### `monitor/logparse.py` — pure, no I/O

```python
def parse_log(text: str, since_marker: str | None = None) -> dict:
    """Parses a docker-logs blob into events. If `since_marker` is given, everything before its
    last occurrence is discarded (run-boundary anchoring)."""

# FROZEN return schema. This sits exactly on the A/B boundary and carries the
# per-chunk-vs-global distinction, so it is spelled out rather than elided.
{
  "targets": [ {"name": str, "event": "dispatched"|"completed",
                "seconds": float|None,      # parsed from "[19m 2.6s, 125.43 MB]"
                "bytes": int|None} ],
  "scrape": {                                # None only if no [owner_scrape] line at all
      "owner_keys": int|None,                # from ':2267'
      "workers_used": int|None,              # CLAMPED value from ':2267'
      "passes_total": int|None,              # from the first pass-open line
      "passes": [ {                          # one entry per pass-open line seen
          "pass": int,
          "open_n": int,                     # owners to look up this pass
          "held_no_record": int|None,        # from "(holding N no_record)"; None on pass 1
          "chunks_total": int|None,
          "chunks": [ {"chunk": int, "resolved": int, "pending": int, "rss_gb": float|None} ]
          #          ^^ PER-CHUNK counts. Never read `pending` as a global figure.
      } ],
      "final": {"owner_keys": int, "passes_used": int,
                "matched": int, "no_record": int, "not_resolved": int} | None,
      "consolidated": {"part_files": int, "rows": int} | None
  },
  "lines": [ {"n": int, "text": str} ],
  "truncated": bool                          # since_marker was given but never found
}
```

Line grammar (every pattern verified character-for-character against
`fixtures/pipeline-logs-tail.txt`; note the Unicode `✔`, and that there are **no ANSI codes**):

| pattern | emits |
| --- | --- |
| `+ <name> dispatched` | target dispatched |
| `✔ <name> completed [<dur>, <size>]` | target completed |
| `[owner_scrape] <P> parcels -> <M> distinct owners, <W> workers` | denominator, clamped workers |
| `[owner_scrape] pass <p>/<t>: <N> owners to look up( \(holding <H> no_record\))?` | pass open, held count |
| `[owner_scrape] pass <p> chunk <c>/<T>: resolved <R>, still pending <Q> (RSS <G> GiB after teardown)` | live position |
| `[owner_scrape] pass <p>: resolved <R>, still pending <Q>` | pass rollup |
| `[owner_scrape] status split over <M> owner keys after <p> pass(es): matched <A>, no_record <B>, not_resolved <C>` | final outcome |
| `[consolidate_owner_parts] folded <N> part file(s) -> <R> rows` | fold |

**Duration grammar** (only `19m 2.6s` was observed; the others are inferred from R's formatter and
must be handled): `1.5s` · `19m 2.6s` · `1h 4m 12s`. Parser returns float seconds.

### `monitor/probe.py` — all I/O lives here and nowhere else

```python
class Probe:
    def read_text(self, name: str) -> str | None      # allowlisted logical name, not a path
    def list_part_files(self) -> list[tuple[str, int]]  # (filename, bytes), regex-validated
    def stat(self, name: str) -> tuple[int, datetime] | None
    def docker_logs(self, since: datetime | None, tail: int) -> str | None
    def container(self) -> dict | None   # {"up": bool, "exit_code": int|None, "started_at": dt}
    def reachable(self) -> bool
```

Every method returns `None` on failure and **never raises** — unreachability is a normal state that
the contract represents, not an exception the server has to catch.

### `monitor/state.py`

```python
def build_status(probe: Probe, now: datetime) -> dict   # returns the §4 Status dict, always valid
```
`now` is injected so tests are deterministic.

### The other two endpoints, frozen

```jsonc
// GET /api/logs?tail=200&filter=owner_scrape
{ "schema": 1, "generated_at": "...", "truncated": false, "matched": 16,
  "lines": [ { "n": 132, "text": "[owner_scrape] 1597 parcels -> ..." } ] }

// GET /healthz   -- about the MONITOR, not the pipeline. 200 even when the box is unreachable.
{ "ok": true, "schema": 1, "sampler_age_seconds": 2.4, "source_reachable": true }
```

`/healthz` deliberately reports the monitor's own health. A monitor that returns 503 because the
thing it monitors is idle is a monitor that pages you for good news.

## 4c. The failure path

The page's whole reason to exist is the bad day, so `errored` is specified end to end rather than
left to an implementer:

1. `parse_progress` yields a row with `progress == "errored"`.
2. `parse_meta` gives that target's `error` text (18-field rows carry it).
3. `state.build_status` sets `run.state = "failed"`, the docket entry's `state = "errored"` with
   its `error` string, and pushes `{level: "error", target, message}` onto `problems`.
4. The page pins a **failure band directly under the header** naming the target and the first line
   of the error, renders that docket row as a hatched oxide bar with a stamped `FAILED` box, and
   **stops showing an ETA entirely** — there is nothing left to estimate.
5. `pipeline_status`'s `summary` leads with the failure: `"FAILED at austin_parcel_data_merged_owner
   after 19m — <first line of error>"`, so an agent reports the problem rather than "not running".

`warnings` (present in the captured run — the `doFuture` RNG warning) are `level: "warning"`,
listed in `problems`, and marked with a `⚠` on the docket row. A warning never turns the run red;
conflating "it complained" with "it broke" would make the red band stop meaning anything.

## 5. Deriving progress honestly

A percentage that is really "targets done / targets total" is a lie when one target takes 19
minutes and another takes 1.5 seconds. The `meta` file makes the honest version cheap:

**Reading `meta` correctly — this is the part that will be got wrong if it is not written down.**
Verified against `fixtures/meta.psv`:

- The file mixes row shapes: **186 rows have 3 fields** (`name|type|data`, for `type` of `object`
  or `function`, e.g. `SCRAPE_WORKERS|object|1c7a09e5108e897f`) and **33 rows have 18 fields**
  (real target records). A parser that assumes 18 fields chokes on 85% of the file.
  **Rule: accept NF ∈ {3, 18}; the docket is `type == "stem"` only.**
- **`meta` accumulates duplicate rows during a run and is COMPACTED when the run ends.** This was
  established by capturing the same run twice — mid-run and after completion:

  | | mid-run (`meta.psv`) | completed (`completed-meta.psv`) |
  | --- | --- | --- |
  | total rows | 219 | 124 |
  | 3-field / 18-field | 186 / 33 | 94 / 30 |
  | duplicated stems | 3 (`austin_parcel_data_merged_owner`, `situs_owner_strings`, `situs_group_assignments` — exactly the targets that had re-run) | **none** |

  So `meta` is append-during-run, rewrite-at-end. **Both shapes are normal and a parser must handle
  both.** An implementer who tests only against the completed fixture will conclude duplicates never
  happen and write a selector that silently returns the wrong row mid-run; one who tests only
  against the mid-run fixture will assume duplicates are guaranteed. Both fixtures ship, and
  `test_parse.py` must exercise both.

- **Rule: a row belongs to the current run iff its decoded `time` ≥ `process.created`.**
  `seconds` = newest current-run row; `seconds_prior` = newest row strictly older than
  `process.created`. Anything else silently compares a target against itself.

- This rule keeps working across compaction, which is what makes ETA possible at all: at the start
  of run N+1 the compacted file holds exactly one row per target carrying **run N's** durations,
  and every one of them is older than run N+1's `process.created` — so they all read as
  `seconds_prior`, which is exactly right. Verified in the fixture: `hhi_data` still carries
  `time=t20668.84` (Aug 5) after the Aug 6 run, because it was skipped and its row was never
  rewritten. **A target whose `time` predates `process.created` was skipped or is untouched this
  run** — the same comparison answers both questions.

- Post-compaction, every stem carries a non-empty `seconds` (verified: zero stems missing it), so
  the ghost data for the next run is always complete after a successful run.
- Decode `time` as `t<days>s` where `<days>` is fractional days since the Unix epoch:
  `20671.5135357477 → 2026-08-06T12:19:29Z`. Verified against a target that ran 1142 s from a
  12:00:01 start.
- Read as **UTF-8** — warning text contains `ℹ` and typographic quotes.
- Drop a trailing partial line: the file is appended to while being read.

**The math:**

1. `seconds_prior[t]` for every target, selected by the rule above.
2. `fraction = Σ seconds_prior[finished this run] / Σ seconds_prior[expected to run]`.
3. `eta_seconds = Σ seconds_prior[not yet finished] − elapsed_in_current_target`, **clamped at 0**.
   When the current target passes its prior duration, ETA does not go negative — the page switches
   to `overrunning by 4m 12s`, which is more useful than a countdown that has already expired.
4. `elapsed_in_current_target` = now − (decoded `time` of the most recently completed target).
   `progress` carries no timestamps, so this is the only available anchor; it is stated here so both
   tracks derive it identically. **When the first target of a run is executing there is no completed
   target yet — fall back to `process.created`.**

   Two properties of `meta.time` worth knowing before someone "fixes" them: it lands ~20 s after the
   log's `✔` line (12:19:29 vs 12:19:07 for target 20), which is harmless for run-splitting; and the
   `time >= created` rule assumes **no `format = "file"` targets**, since those store the tracked
   file's timestamp rather than the write time and could misfile a current-run row as prior. Verified
   for this pipeline: the fixture's 18-field rows are 30x `qs` and 2x empty format, **zero `file`
   targets**. If a `file` target is ever added, this rule needs revisiting.

**The denominator is genuinely unknowable mid-run, and the page says so rather than hiding it.**
`targets` decides at dispatch time whether a target is skipped, so targets not yet reached are
neither skipped nor pending. The rule: **assume every not-yet-seen target will run** (the
conservative direction — it over-estimates remaining time and the bar catches up rather than
sliding backwards), and mark `eta_basis: "prior-run-durations-provisional"` until every target has
been dispatched or skipped, at which point it becomes `"prior-run-durations"`. A bar that only ever
moves forward is worth more than a marginally tighter estimate that reverses.

5. If targets covering **more than 20% of the expected time-weight** have no prior duration, set
   `fraction: null`, `eta_seconds: null`, `eta_basis: "none"`. **The page then shows no percentage
   and no ETA at all** rather than a made-up one. An honest blank beats a confident guess on a page
   whose whole job is to be trusted about provenance.

Skipped targets contribute zero to both numerator and denominator — a resumed run that skips 19 of
27 targets should read as "mostly done", because it is.

`progress.completed` and `progress.skipped` are **disjoint**: `completed` counts only targets that
actually ran to completion this run; `skipped` counts skips; `targets_total` = count of distinct
`type == "stem"` names in `meta` (= 27, verified). `completed + skipped + running + waiting =
targets_total`. This is spelled out because the page and the MCP each render a "N of 27" summary
and must not disagree.

## 5b. The scrape: why it gets buckets and not a percentage

Target 20 is a multi-hour black box to `targets` — it reports only "running". Its internals are
recoverable **only from the log stream**. Five lines matter (all verified character-for-character
against `fixtures/pipeline-logs-tail.txt`):

| line | gives |
| --- | --- |
| `[owner_scrape] 1597 parcels -> 1561 distinct owners, 16 workers` (`:2267`) | denominator `M` + **clamped** worker count, once per scrape |
| `[owner_scrape] pass 2/3: 39 owners to look up (holding 1512 no_record)` (`:2309`) | **the `no_record` count — available nowhere else mid-run** |
| `[owner_scrape] pass 1 chunk 1/1: resolved 10, still pending 1551 (RSS 2.9 GiB after teardown)` (`:2494`) | **PER-CHUNK** counts — see the warning below |
| `[owner_scrape] pass 1: resolved 10, still pending 1551` (`:2516`) | per-pass rollup, **global to the pass** |
| `[owner_scrape] status split over 1561 owner keys after 3 pass(es): matched 10, no_record 1512, not_resolved 39` (`:2586`) | final outcome |

### The chunk counters are per-chunk, and every fixture we own hides it

`scrape_helper_functions.R:2493-2497` prints `resolved` as `length(chunk_keys) - length(chunk_result)`
and `still pending` as `length(chunk_result)` — **both scoped to that chunk's ≤ 10,000 keys.** The
pass rollup at `:2516` is different: `resolved_this_pass <- length(ask_now) - length(still_pending)`,
which *is* global to the pass.

The captured run had `chunks_total = 1` on every pass (1,561 owners, `SCRAPE_CHUNK = 10000`), so
per-chunk and global coincided and any wrong reading walks the fixture flawlessly. **On the real
~95,000-owner scrape there are ~10 chunks**, and reading `still pending 1800` from chunk 3/10 as a
global figure would report 1,800 outstanding out of 95,000 when the true number is far higher.

Correct mid-pass arithmetic:

```
outstanding_in_pass = pass_open_N − Σ(resolved from chunk lines seen so far this pass)
matched             = matched_before_this_pass + Σ(resolved this pass)
```

Only reset the chunk accumulator on a pass-open line. Never read a chunk line's `still pending` as
a global count.

**The pass-open resync identity — the model's self-healing rule.** At every pass-open line:

```
matched == owner_keys − open_N − held_no_record        # 1561 − 39 − 1512 = 10  (verified)
```

**Set `matched` from this identity at each pass boundary rather than trusting the accumulator.**
If chunk lines fell outside the log window, the accumulator silently undercounts; the identity
re-grounds the model from an independent line once per pass, so any drift is bounded to a single
pass instead of compounding across the whole scrape.

**The assumption everything rests on, named so nobody "corrects" it:** a chunk line's `resolved`
counts **matched only**. Keys labelled `no_record` stay inside `pending` until they are held at the
next pass-open (`:2518-2520`). The fixture confirms it — pass 1's `resolved 10` equals the final
`matched 10`, and its `pending 1551` is `1512 + 39`. An implementer who "fixes" `resolved` to
include `no_record` breaks every bucket.

**Track A must build a hand-authored multi-chunk log fixture** (`multichunk-logs.txt`: one pass,
`chunk 1/10` … `chunk 10/10`, plausible counts) and test against it. This is a named deliverable,
not an optional edge case — it is the only defence against the one bug our real fixture structurally
cannot catch, and it is the cheapest insurance in the plan.

### Two line shapes that must be deliberately skipped

These exist in the real log and must not false-match:

- `[owner_scrape] pass 1 chunk 1/1: 1561 owners to look up, 16 fresh workers (RSS 1.9 GiB before pool)`
  (`:2375`) — the chunk-*open* line. **The pass-open pattern must anchor `pass \d+/\d+:`** so that
  `pass 1 chunk 1/1:` cannot match it.
- `[owner_scrape] writing 1551 unresolved owners as empty rows` (`:2540`) — that 1551 is
  `no_record + not_resolved` and would corrupt any bucket that grabbed it.

Unknown `[owner_scrape]` lines are skipped silently; these two are skipped deliberately and are
named here so nobody "fixes" the parser by making them match.

### The trap, from the real captured run

```
pass 1: resolved 10, still pending 1551          <- pending = 1551
pass 2/3: 39 owners to look up (holding 1512 no_record)   <- pending = 39
```

Between those two lines **no owner was scraped**. 1,512 owners were reclassified from "pending" to
"held, no filing on record". A naive `(owner_keys - pending) / owner_keys` therefore jumps from
**0.6% to 97.5% instantly**, and — because the final state is `pending 39`, not 0 — **never reaches
100%** even on a fully successful scrape. It would be wrong at both ends.

Worse, it would be wrong in the specific way this project has already shipped a defect once
(`7ac8424`): it would silently treat `no_record` (Texas has no filing — *a finding*) as if it were
the same kind of thing as work not yet done.

### The correct model: three disjoint buckets, no single percentage

`matched + no_record + not_resolved + outstanding = owner_keys`, rendered as a **segmented bar**
that fills as information arrives rather than a percentage that pretends to be one number:

| phase | what is knowable | what the page shows |
| --- | --- | --- |
| pass 1 running | only `matched` (the `resolved` count); `no_record` is **not separable yet** — it is inside `pending` | matched segment + a single grey `outstanding` segment. `buckets_complete: false`, and the page labels it *"still asking"* — it does **not** imply the remainder is a failure |
| pass 2+ open | `no_record` becomes known from the `(holding N no_record)` clause | the `no_record` segment appears. This jump is **real information arriving**, not an artifact, and reads correctly as such |
| final line seen | all three buckets final | full segmented bar, `buckets_complete: true` |

Retry passes 2 and 3 re-query the same 39 owners and produce **zero bucket movement** while
consuming real wall-clock time. That is why pass position (`pass 2/3`) is displayed as its own
readout beside the bar and is **never folded into it** — a bar that sits still for two passes with
a visible "pass 2 of 3, re-asking 39" label is honest; one that fakes motion is not.

`not_resolved` is only final at the end; mid-run those owners are inside `outstanding`.

### Phase resolution, including both silent-skip cases

- target 20 not dispatched → `waiting`
- `:2267` denominator line seen for **this run** → `running`
- target 20 completed **and no `[owner_scrape]` line for this run** → `skipped`, with `skip_reason`:
  - `owner_data_total.csv` ≥ 40 MB → `resume-gate-40mb`
  - otherwise → `nothing-left-to-ask` (the resume filter dropped every already-recorded parcel,
    leaving `nrow(target_properties) == 0`; `scrape_helper_functions.R:2189-2197`. **This case
    prints nothing at all**, and an earlier draft of this plan misfiled it as stalled)
- target 20 running, chunk lines present but the `:2267` line is **outside** the log window →
  `running` with `owner_keys: null`, `buckets_complete: false`, and **no bar at all** — the panel
  shows pass/chunk position and part-file counts only
- target 20 running, nothing parseable → `unknown`

Both skip cases are normal, correct outcomes and must render as calm statements of fact, not as
faults. Part-file count and bytes are read from the filesystem as an **independent** liveness
signal: if part files are growing, work is happening even when no new log line has landed.

---

## 6. Design

Worked as a design brief, per the frontend-design skill, not as "put the numbers on a page."

### Subject, audience, job

A batch pipeline that reads Texas county appraisal rolls and interrogates the state registry about
who owns rental housing. It runs for hours on a 128-core machine in a house. Audience: one
operator, plus agents. The page's single job: **what is it doing, how much longer, and did
anything break** — and when nothing is running, **when did it last run and is the data fresh.**

The subject's vernacular is not SaaS. It is appraisal rolls, filings, stamped dates, docket
sheets, bureaucratic record-keeping. That is the material worth stealing from, and it is also
honest: this page *is* a record of a proceeding.

### Direction: the run as a docket

A stamped record sheet. Sequence number, entry, disposition, elapsed. Numbering is used because
`targets` genuinely **is** a strict dependency sequence — order carries information the reader
needs — not as decoration.

### Color

Reuse the app's existing `dsa` skin tokens **verbatim**, so the monitor and the site read as
siblings without importing a line of each other's code. These values are already the DSA national
palette, so following the design guide and matching the existing app are the same act:

| token | light | dark | role here |
| --- | --- | --- | --- |
| `--paper` | `#f6f4f3` | `#191617` | page |
| `--paper-2` | `#ffffff` | `#231f20` | panel surface |
| `--paper-3` | `#ece8e7` | `#1e1a1b` | docket header, footer band |
| `--ink` | `#231f20` | `#f6f4f3` | text (DSA Black — warm, not pure black) |
| `--ink-2` | `#605c5c` | `#a9a4a4` | labels, completed bars |
| `--rule` | `#8c8989` | `#494545` | hairlines |
| `--survey` | `#ec1f27` | `#ec1f27` | **DSA Red — live only** |
| `--survey-w` | `#fbd2d4` | `#3a1416` | ghost bars |
| `--oxide` | `#a00a10` | `#f5726f` | failure ink |
| `--ochre` | `#6d5300` | `#e8c34a` | caution / unknown |

**State is encoded by form, not hue** — bar fill, hatch, stamp shape — so it survives colorblindness
and greyscale. This is required by the design guide's accessibility section and it is also just
better instrumentation.

### Type

- **Data in `--mono`** (the app's existing stack: `ui-monospace, "Cascadia Mono", "SF Mono", …`).
  Every value on this page is a count, a duration, or a timestamp; monospace makes durations
  compare visually down a column without a chart. The type treatment is doing measurement work.
- **Labels and headings in a tight system sans.** Small-caps eyebrows at 11px with `+0.13em`
  tracking, per the design guide's note that very small sizes want ≈ +130 tracking.
- **No new font binaries are vendored.** The monitor uses system font stacks and is
  visually complete without any webfont, which also keeps it dependency-free and offline-safe.
- Scale: eyebrow 11, label 12, body 14, target name 15 mono, run headline 40.

### Layout

```
┌───────────────────────────────────────────────────────────────┐
│▌PIPELINE MONITOR              landlord-mapper    ◐ theme      │  ← 3px red rule ONLY when live
├───────────────────────────────────────────────────────────────┤
│                                                               │
│  RUNNING                              started  07:00:01 CDT   │
│  austin_parcel_data_merged_owner_clean                        │
│  elapsed 51m 12s                      remaining  ~1h 04m      │
│                                                               │
├───────────────────────────────────────────────────────────────┤
│ DOCKET                                            27 entries  │
│                                                               │
│ 01  hhi_data                        ·  skipped                │
│ ⋮                                                             │
│ 20  austin_parcel_data_merged_owner ████████████▌ 19m 02s  ⚠  │
│                                     ░░░░░░░░░░░░░░ was 21m 08s│
│ 23  austin_parcel_…_owner_clean     ██████▓▓▓▏ running 12m 41s│
│                                     ░░░░░░░░░░░░ was 28m 12s  │
│                                                               │
├───────────────────────────────────────────────────────────────┤
│ SCRAPE            (present only when observed)                │
│ 1,561 owner keys · 3 passes · 16 workers · 6 part files       │
│ matched 10 │ no filing on record 1,512 │ our query failed 39  │
├───────────────────────────────────────────────────────────────┤
│ FRESHNESS   owner_data_total.csv 27.9 MB · 07:16 · 34m ago    │
├───────────────────────────────────────────────────────────────┤
│ ▸ LOG   last 40 lines                        (collapsed)      │
└───────────────────────────────────────────────────────────────┘
```

### Signature element: the ghost bar

Each docket row carries a horizontal bar. The **ghost** is that target's duration on the *previous*
run (`--survey-w`). The **actual** is drawn over it: `--ink-2` when complete, DSA Red while in
flight, growing on each poll. Past the ghost's end, the overrun continues in a hatch.

**The axis is shared absolute time**, and its scale is **frozen for the run** at
`max(ghosts ∪ completed actuals)` over the visible (non-collapsed) rows. This was
arrived at by building and discarding the alternative, which is worth recording because the
discarded version looked more sophisticated and was worse.

A previous draft used an "elapsed ÷ expected" ratio axis with a 1.0 hairline. It is self-defeating:
if the ghost is by definition the prior duration, then on a ratio axis **every ghost ends at exactly
1.0**. All 27 ghosts become the same length, the ghost and the hairline become the same object drawn
twice, and the signature collapses into an ordinary bullet chart. Worse, it throws away the single
most interesting thing in the data.

On a shared time axis the ghosts vary, and their variation *is* the insight: **three targets consume
essentially all the wall clock and the other twenty-four are noise.** In the captured run, target 20
took 19m 02s and target 21 took 17m 57s while `situs_group_assignments_final` took 10.4s and
`hhi_data` took 1.5s. A reader learns where the pipeline's time actually goes by looking at the
shape of the column, and learns it without reading a single number. The ratio axis erases that
completely — it makes a 1.5-second target and a 19-minute target look equally important, which is
the opposite of true.

"Is this step behind?" is still answered, and answered where it matters: the red actual extending
past the pale ghost's right edge. For the three targets that dominate the clock this is unmissable.
For a 1.5s target running 2× slow it is invisible — which is correct, because nobody should be told
about 1.4 seconds.

Handling the small-bar problem honestly, rather than pretending it away:

- **The scale must not float.** If the axis tracked "longest bar including the live one", then the
  moment the running bar became the longest, every other bar would shrink on every 3 s poll — the
  whole docket would breathe, contradicting the motion budget, and "clipped at the column edge"
  would be circular because the clipped bar defines the edge. Freezing the scale at
  `max(ghosts ∪ completed actuals)` fixes both: live bars grow against a stationary ruler, and
  anything past it gets the hatched cap and printed ratio. Ghosts of collapsed skipped rows are
  **excluded** from the scale set, so expanding that group never rescales the column.
- **Minimum bar width 3 px**, rendered as a distinct **tick** (a 3 px stub, no ghost, no fill
  gradient) rather than a tiny proportional bar. A sub-pixel bar is a rendering bug; a tick is an
  honest "too small to depict" and reads as categorically different from a measured bar, which is
  what makes the printed number obviously the information for those rows. This is a visible
  treatment on the page, not a footnote in this plan.
- The 19 collapsed skipped rows are already gone from the visible set, so the axis is scaled to the
  targets that actually ran — a ~114× range in the captured run, not ~760×.
- The printed duration in mono sits beside every bar and is the precise value. The bar is for
  proportion; the number is for magnitude. Neither is asked to do the other's job.
- Where a bar is at minimum width, the number is the information. That is stated, not hidden.

A target with no prior duration gets a bar but **no ghost** — there is nothing to draw it against,
and a full-length ghost would invent an expectation. A target whose prior duration is under 5 s gets
no overrun treatment at all; ratio noise on a two-second task is not a signal.

Where an overrun exceeds the axis, the bar is clipped at the column edge with a hatched cap and the
ratio is printed (`2.7×`), so a code change that moves a target by an order of magnitude reads as
an order of magnitude rather than pinning silently at the edge. This is a real case here: the
`property_units > 5` → `> 4` change moved the scrape's workload materially.

The result: the pipeline visibly races its own history, on one honest scale, and its time profile is
legible at a glance. This is only possible because `_targets/meta/meta` records real per-target
seconds — the signature comes from the subject's own instrument, which is the point.

Bars are inline SVG generated in Python, following `web/lm/netdiagram.py`'s established idiom:
`viewBox` with no fixed width/height, `class` attributes only, **zero color literals in the Python**
— all fill/stroke defined in CSS with `var(--…)`, so theming and dark mode come free. Each bar
carries `role="img"` and an `aria-label` giving the prose equivalent ("ran 19 minutes 2 seconds,
10% under the previous run's 21 minutes 8 seconds").

### The two reds, stated honestly

An earlier draft claimed "red means now — nothing else on the page is red." That was a slogan that
collapses on contact with the failure state, because `--oxide` (`#a00a10`) is red to any human eye.
The real rule, written the way it actually behaves:

> **Bright DSA Red (`--survey`, `#ec1f27`) = happening now. Dark oxide red (`--oxide`) = failed.
> They are told apart by fill pattern and stamp, never by hue alone.**

Live is a solid bright fill with a pulsing leading edge. Failed is a **hatched dark bar with a
stamped `FAILED` rule-box** in oxide. In greyscale, at a glance, or with any form of colour
vision deficiency, the two remain unambiguous because one is solid and moving and the other is
hatched and stamped. Everything that is neither — completed, skipped, waiting — stays grey.

The across-the-room glance still works, and now it works for the reason claimed.

### The 19 skipped rows — the most common state, designed

A resumed run skips most of the pipeline. In the captured run, **19 of 27 targets were skipped**,
so a naive docket opens with 19 near-identical `· skipped` lines before anything worth reading.
That is the page's most frequent real state and it gets a rule, not an ellipsis:

**Contiguous skipped targets collapse into one row** — `01–19  ·  19 entries skipped, unchanged
since the last run` — with a disclosure triangle that expands to the individual names. Skipped work
is genuinely not news; it is the pipeline correctly declining to redo itself. Collapsing it is not
hiding it, it is ranking it. Non-contiguous skips collapse per contiguous group so sequence
position stays truthful.

### Long names and narrow screens

`austin_parcel_data_merged_owner_clean` is 37 characters, and the target names share long prefixes
(`…_merged`, `…_merged_owner`, `…_merged_owner_clean`), so **mid-string ellipsis would collide** —
three different targets could render identically. Rule: **truncate from the head, keep the tail**
(`…merged_owner_clean`), since the distinguishing information is always at the end. Full name in a
`title` attribute and in the `aria-label`.

At ≤ 600 px the docket becomes two lines per entry — `seq + name` on the first, `bar + duration` on
the second — the collapsed-skip row stays one line, the `was …` ghost annotation moves under the
bar, and the scrape panel's three buckets stack vertically with their labels rather than sitting in
a row. Nothing is dropped at narrow widths; only the arrangement changes.

### The accessory that gets removed

An earlier draft had a big overall progress bar in the header. It is cut: the docket *is* the
progress display, and a summary bar above it says the same thing twice. The header keeps only what
the docket cannot show at a glance — state, current target, elapsed, remaining.

### Motion

Bar widths transition 400 ms ease-out on poll. The live row's leading edge carries a 2 px red
marker that pulses on a 2 s cycle. Nothing else animates. `@media (prefers-reduced-motion: reduce)`
removes both the transition and the pulse. That is the whole motion budget, and it is spent on the
one thing that is actually changing.

### Idle state

Most of the time nothing is running, so the idle state is a first-class design, not a fallback.
The headline becomes `LAST RUN — finished 14:32, took 3h 09m`; the docket shows that run complete
with actual against ghost; FRESHNESS is promoted above SCRAPE. Empty states are directive, per the
skill: not "no data", but e.g. `No run recorded. The monitor reads /landlord_mapper_etl/_targets —
start a run and this fills in.`

### Copy

Plain and direct, matching the site's voice after its 696 → 111 word cut. No marketing, no walls of
text. Interface labels name what the operator recognizes, not the implementation: the scrape panel
says **"no filing on record"** and **"our query failed"**, not `no_record` and `not_resolved` —
which is both better copy and the load-bearing epistemic distinction the site already regressed on
once. Machine keys stay machine-readable in the JSON; humans get the sentence.

### Quality floor

Responsive to mobile (the docket collapses to name-over-bar), visible keyboard focus using
`--focus`, reduced motion respected, SVG bars carry `role="img"` + `aria-label` with the prose
equivalent, live region announces target transitions politely, and the page is fully legible with
CSS disabled in the sense that all values are real text.

---

## 7. The MCP server

Runs on the operator's laptop over stdio; a thin HTTP client to the monitor's JSON API. Base URL from
`LM_MONITOR_URL`, default `http://cam-cloudripper.local:8098`. Hand-rolled stdlib JSON-RPC 2.0
implementing `initialize`, `tools/list`, `tools/call` — no SDK, no dependency.

| tool | answers |
| --- | --- |
| `pipeline_status` | Is it running? What step, how long, how much longer, did anything fail? |
| `pipeline_docket` | Per-target table: state, seconds this run, seconds last run, delta |
| `pipeline_scrape` | Last scrape: owner keys, passes, workers, outcome split, part files, resume gate |
| `pipeline_logs` | Tail N lines, optional substring filter |
| `pipeline_freshness` | When each artifact was last written; how old the shipped data is |
| `pipeline_history` | Prior run durations — "how long does a full run take" |

Design rules:

- Tool descriptions state **when to call**, so an agent does not have to guess.
- Responses are compact JSON, not prose — but `pipeline_status` also returns a one-line
  `summary` string, because the overwhelmingly common use is an agent reporting status to a human.
- **Unreachable monitor returns a clear, actionable error naming the URL tried** — never a stack
  trace, never a silent empty result that reads like "the pipeline is idle." Confusing "I cannot
  see it" with "it is not running" is the one failure mode that would make this tool worse than
  useless, and it gets an explicit test.
- Read-only. No tool starts, stops, or modifies anything.

### Protocol details that are where hand-rolled servers actually break

- **Framing is newline-delimited JSON on stdin/stdout — not `Content-Length` headers.** Write one
  compact JSON object per line, flush after every write.
- **Nothing may ever be printed to stdout except protocol messages.** All logging goes to stderr.
  A stray `print()` corrupts the stream and the failure looks like a hang.
- Handle, without erroring: the `notifications/initialized` notification (no response — it has no
  `id`), `ping`, and unknown methods such as `resources/list` / `prompts/list`, which must return a
  JSON-RPC "method not found" error rather than crashing or hanging.
- Echo back the client's `protocolVersion` from `initialize`, and declare only `{"tools": {}}` in
  capabilities.
- Requests carrying no `id` are notifications: act, never reply.

### Registration

`README.md` (track E) ships the exact block to add, and `run.sh --print-mcp-config` emits it:

```jsonc
// .mcp.json
{ "mcpServers": {
    "lm-pipeline": {
      "command": "python",          // Windows laptop: "python3" often hits the Store alias stub
      "args": ["-m", "mcp.mcp_server"],
      "cwd": "<abs path to>/landlord-mapper-fork/pipeline-monitor",
      "env": { "LM_MONITOR_URL": "http://cam-cloudripper.local:8098" }
    } } }
```

---

## 8. Files

```
pipeline-monitor/
├── README.md                  what it is, how to run it, how to point the MCP at it
├── monitor/
│   ├── __init__.py
│   ├── parse.py               PURE functions: text → dicts. No I/O. Fully unit tested
│   │                          progress/meta/process files + the targets time encoding
│   ├── logparse.py            PURE: the 5 parsed [owner_scrape] lines (+2 deliberately
│   │                          skipped), ✔/+ target lines,
│   │                          [consolidate_owner_parts]. Regex table + tests per line
│   ├── probe.py               all I/O: sudo/docker reads, path allowlist, subprocess argv
│   ├── state.py               assembles Status; ETA/fraction math; to_json()
│   ├── render.py              HTML page from a Status dict
│   ├── bars.py                inline-SVG ghost/actual bars (netdiagram.py idiom)
│   ├── styles.py              CSS: dsa skin tokens + monitor-specific structure
│   ├── sampler.py             background thread; refreshes the cached Status (3s / 30s idle)
│   └── server.py              ThreadingHTTPServer; /, /api/status, /api/logs, /healthz
│                              ** owned by the INTEGRATION pass, not by any track **
├── mcp/
│   ├── __init__.py
│   └── mcp_server.py          stdlib JSON-RPC over stdio; 6 tools; HTTP client
├── tests/
│   ├── fixtures/              REAL captured files from the 2026-08-06 run
│   │   ├── progress.psv  meta.psv  process.psv  pipeline-logs-tail.txt   (mid-run)
│   │   ├── completed-progress.psv  completed-meta.psv  completed-logs-tail.txt
│   │   │                                       (SAME run, after completion + compaction)
│   │   ├── multichunk-logs.txt   HAND-BUILT, REQUIRED: one pass, chunk 1/10..10/10.
│   │   │                         The only defence against the per-chunk/global bug that
│   │   │                         the real single-chunk fixtures structurally cannot catch
│   │   └── progress-errored.psv  progress-firstrun.psv        (hand-built edge cases)
│   ├── test_parse.py          3-field vs 18-field rows, time decoding, run splitting
│   ├── test_logparse.py       every line grammar; the pass-2 held-no_record transition
│   ├── test_state.py          ETA math, null-safety, skipped-target handling
│   ├── test_render.py         renders every state without raising; no color literals; % trap
│   ├── test_probe.py          path allowlist; no secret paths; fixed argv
│   └── test_mcp.py            protocol handshake; unreachable-monitor error is explicit
├── run.sh                     start on the box
├── lm-monitor.service         optional systemd unit (user service)
└── sync-to-box.sh             rsync this dir to the box; never touches web/
```

Entry point: `python3 -m monitor.server`. Port `LM_MONITOR_PORT` default **8098** (verified free
on the box; the web app uses 8099, Shiny holds 8787).

Tests: stdlib `unittest`, run with `python3 -m unittest discover tests`. The repo has no test
suite today; this adds one **scoped strictly to `pipeline-monitor/`** rather than proposing a
repo-wide convention nobody asked for.

---

## 9. Known-debatable calls, stated up front

1. **Hand-rolled MCP instead of the SDK.** Keeps the repo at zero dependencies and makes the
   project self-contained, at the cost of owning a protocol implementation. The alternative is one
   `pip install` in a repo that has never had one.
2. **`sudo -n` from a LAN-bound service.** Now only a fallback: the preferred posture is a one-time
   `setfacl` grant (incl. `-d` default ACLs) after which the monitor runs unprivileged. Where sudo
   is still used it is mitigated by a directory allowlist + filename regex, fixed argv, and
   read-only endpoints, but it is real. The container-with-read-only-mount alternative is cleaner
   on this axis and worse on the log axis.
3. **Polling at 3 s.** Cheap on LAN, but it is polling. SSE would be nicer; stdlib
   `BaseHTTPRequestHandler` with threads can do it, at the cost of connection-lifecycle code.
4. **ETA from a single prior run** rather than a median of several. Simple and usually right;
   wrong right after a code change that alters a target's cost.

---

## 10. Build order

The `Status` contract (§4), the signatures (§4b), and the failure path (§4c) are frozen first,
which is what lets the rest fan out. Five tracks, disjoint file ownership, no ordering between
them. **No track owns `server.py`** — it is wired in the integration pass.

| track | owns | reads |
| --- | --- | --- |
| **A** parsers | `monitor/parse.py`, `monitor/logparse.py`, `tests/test_parse.py`, `tests/test_logparse.py`, `tests/fixtures/` | §4b signatures, §5 meta rules, §5b log grammar |
| **B** probe + state | `monitor/probe.py`, `monitor/state.py`, `monitor/sampler.py`, `tests/test_probe.py`, `tests/test_state.py` | §3 security + sampler + run.state, §4, §4b, §5, §5b |
| **C** page | `monitor/render.py`, `monitor/bars.py`, `monitor/styles.py`, `tests/test_render.py` | §4, §4c, §6 |
| **D** MCP | `mcp/mcp_server.py`, `tests/test_mcp.py` | §4, §4b endpoints, §4c, §7 |
| **E** ops | `README.md`, `run.sh`, `lm-monitor.service`, `sync-to-box.sh` | §3 ACL grant, §7 registration, §8 |

B stubs A's module against the §4b signatures rather than waiting for it; C and D build against the
§4 example payload, which is a **valid, complete** Status by construction so it works as a fixture.
Copy all **seven** real fixture files (four mid-run, three completed) — §5 requires tests against
both captures. The zero-byte `err*.txt` capture artifacts are not fixtures and have been deleted.

**Deferred verification, recorded so it is not forgotten.** The riskiest surviving assumption is
that a chunk line's `resolved` means matched-only *at production scale* — verified here against one
run whose every pass had a single chunk, plus a source reading. The pass-open resync identity bounds
any error to one pass. The real test is the next full-scale run: **compare the monitor's final
buckets against that run's own `status split over N owner keys` line.** If they disagree, the
chunk-line semantics drifted and §5b needs revisiting.

Restart policy for the systemd unit is `Restart=on-failure` with `RestartSec=5`; a dead monitor is
visible to agents through the MCP's unreachable error and to a human through the page's stale
`generated_at` banner, which is why the page renders cache age rather than hiding it.

Then a single integration pass: wire `server.py`, run the suite, run it against the box, verify
against the live run if one is still in flight, and check `git diff --stat` against stated intent
before any commit (a commit that claimed a one-line filter
change once deleted 281 lines and silently reverted a feature).

---

## 11. Open items

1. **Where the monitor runs.** This plan assumes the compute box, LAN-visible on 8098, with the
   MCP server on a laptop pointed at it. Bind to `127.0.0.1` and reach it over an SSH tunnel if a
   LAN-visible service is not wanted.
2. **Ghosts and metadata compaction.** `targets` compacts its metadata when a run ends, so
   immediately after a completed run there is no prior duration to draw a ghost against. Ghosts
   reappear as soon as the next run starts, which is when they matter. If ghosts are wanted in the
   idle view, the monitor would need to persist its own duration history — deliberately not done
   here, so that the monitor stays a pure reader.
3. **Deferred verification.** The scrape bucket model is verified against a run whose every pass
   had a single chunk. Compare the monitor's final buckets against the next full-scale run's own
   `status split over N owner keys` line; see §10.
