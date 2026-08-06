# pipeline-monitor

A locally hosted, read-only page and API that show what the landlord-mapper
`targets` pipeline is doing right now: which step is running, how long it has
taken, how much longer it should take, whether the owner-registry scrape is
mid-flight, and when the data was last refreshed. The same status is exposed
over an MCP server so agents can ask "is the pipeline still running" without
SSHing anywhere or being told to poll a log.

It is its own tracked project inside this fork, sits next to `web/`, and
never touches it or Railway. Full design rationale, the frozen `Status`
contract, and the reasoning behind every decision below live in
[`PLAN.md`](./PLAN.md) — this file is the how-to, not the why.

## What it reads

Everything is read-only and comes from two places on the box:

- The `lm_work` docker volume (mounted into the pipeline container at
  `/landlord_mapper_etl`): `_targets/meta/progress` (which target is running),
  `_targets/meta/meta` (per-target durations, for ETA and history),
  `_targets/meta/process` (run identity and start time), and the
  `owner_data_part_<pid>.csv` files that appear only while the owner-registry
  scrape is in flight.
- `docker logs lm-pipeline`, anchored to the current run's start time, for the
  scrape's own outcome lines (`[owner_scrape] ...`, `[consolidate_owner_parts]
  ...`) and the `+ dispatched` / `✔ completed` target lines — information that
  exists only in the log stream, not on disk.

The monitor never writes anything back and cannot start, stop, or otherwise
disturb a run.

## Running it on the box

The box is Ubuntu with Python 3.12.3 at `/usr/bin/python3`.

```sh
cd pipeline-monitor
./run.sh
```

This starts `python3 -m monitor.server`, binding `LM_MONITOR_HOST` (default
`0.0.0.0`, for LAN access) on `LM_MONITOR_PORT` (default `8098` — verified
free; the web app uses 8099, a Shiny app uses 8787).

To run it as a systemd user service instead of in a terminal:

```sh
mkdir -p ~/.config/systemd/user
cp lm-monitor.service ~/.config/systemd/user/
# edit WorkingDirectory in the copy if pipeline-monitor/ lives somewhere
# other than ~/landlord-mapper-fork/pipeline-monitor
systemctl --user daemon-reload
systemctl --user enable --now lm-monitor.service
```

The unit restarts on failure (`RestartSec=5`). If it dies and stays dead,
that's visible two ways: the page's `generated_at` timestamp goes stale, and
any MCP tool call starts returning an explicit "unreachable" error instead of
a status.

## One-time setup: the ACL grant

The `lm_work` volume's host path is root-owned, so `cam` can't read it by
default. The preferred fix is a one-time ACL grant, after which the monitor
runs fully unprivileged — no `sudo` in its request path.

```sh
V=/media/cam/ImageProcessing/docker/volumes/lm_work/_data
sudo setfacl -R    -m u:cam:rX "$V" "$V/_targets"   # existing files
sudo setfacl -R -d -m u:cam:rX "$V" "$V/_targets"   # DEFAULT ACL: inherited by NEW files
```

Both commands matter. The second (`-d`, the default ACL) is not optional:
`targets` recreates `_targets/meta/progress` as a new file at the start of
every run, and every `owner_data_part_<pid>.csv` is a brand-new root-created
file mid-scrape. Neither inherits an access-only grant. Skip the `-d` line and
the monitor will look fine against files that already exist, then go
permission-denied the moment a real run starts.

`run.sh` checks for the default-ACL entry itself (via `getfacl -d`) before
starting, and refuses to launch — printing the exact commands above — if the
grant is missing, rather than silently falling back to `sudo`. `sudo -n`
reads are supported as a fallback path, not the default.

## Pointing the MCP client at it

The MCP server runs on the operator machine, over stdio, and talks to the
monitor over plain HTTP on the LAN. Point it at the box with `LM_MONITOR_URL`
(default `http://cam-cloudripper.local:8098`).

Print the exact config block:

```sh
./run.sh --print-mcp-config
```

which emits (also documented in `PLAN.md` §7):

```jsonc
// .mcp.json
{ "mcpServers": {
    "lm-pipeline": {
      "command": "python",          // Windows laptop: "python3" often hits the Store alias stub
      "args": ["-m", "lm_mcp.mcp_server"],
      "cwd": "<abs path to>/landlord-mapper-fork/pipeline-monitor",
      "env": { "LM_MONITOR_URL": "http://cam-cloudripper.local:8098" }
    } } }
```

Add that block to `.mcp.json`, fixing the `cwd` to the actual checkout path on
the laptop.

### The six MCP tools

| tool | answers |
| --- | --- |
| `pipeline_status` | Is it running? What step, how long, how much longer, did anything fail? |
| `pipeline_docket` | Per-target table: state, seconds this run, seconds last run, delta |
| `pipeline_scrape` | Last scrape: owner keys, passes, workers, outcome split, part files, resume gate |
| `pipeline_logs` | Tail N lines, optional substring filter |
| `pipeline_freshness` | When each artifact was last written; how old the shipped data is |
| `pipeline_history` | Prior run durations — "how long does a full run take" |

All six are read-only: none of them can start, stop, or modify a run. If the
monitor is unreachable, the tool call returns a clear error naming the URL it
tried — never a silent empty result that could be misread as "the pipeline is
idle."

## Env vars

| var | side | default | meaning |
| --- | --- | --- | --- |
| `LM_MONITOR_PORT` | box | `8098` | port the monitor listens on |
| `LM_MONITOR_HOST` | box | `0.0.0.0` | bind address; use `127.0.0.1` if reaching it only via an SSH tunnel |
| `LM_MONITOR_URL` | laptop | `http://cam-cloudripper.local:8098` | base URL the MCP client uses to reach the monitor |

## Syncing to the box

```sh
./sync-to-box.sh          # dry run — shows what would transfer
./sync-to-box.sh --apply  # actually transfers
```

`sync-to-box.sh` only ever syncs this directory (`pipeline-monitor/`) to the
matching directory on the box, and refuses to run if its source or
destination path mentions `web/` or `lm_work` — this project does not go near
either. Override the target host/path/key with `LM_BOX_HOST`, `LM_BOX_DEST`,
`LM_SSH_KEY` if needed.

## Running the tests

Stdlib `unittest`, no dependencies:

```sh
python3 -m unittest discover tests
```

This test suite is scoped to `pipeline-monitor/` only; the rest of the repo
has no test suite and this doesn't propose adding one repo-wide.

## More detail

`PLAN.md` in this directory has the full design: the `Status` JSON contract,
why the scrape is reported as three disjoint buckets instead of a percentage,
the security posture for a LAN service that can fall back to `sudo`, and the
page's visual design. Read it before changing behavior described here.
