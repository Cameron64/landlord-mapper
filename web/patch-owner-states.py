#!/usr/bin/env python3
"""Rewrite meta.owner_states so it is counted over the same owners the pages
divide it by.

build-db.py used to compute the histogram over every owner row while every page
divides it by owners_in_scope, and 7,345 out-of-scope owners carry a real
registry answer, so matched/no_record/not_resolved were each overstated: the
three published shares summed to 110.5% and "no Texas filing" read 61.9%
instead of 51.8%. OUT_OF_SCOPE was understated by the same 7,345, because those
answered owners had their state overwritten.

build-db.py is fixed, but owner_states is persisted in meta, and rebuilding the
database to move one row would cost hours. This patches the row in place. It
recounts from the owner table rather than editing the numbers, so the result is
what a fresh build now produces.

Idempotent, and refuses to write anything that is not a clean partition.

    python3 tools/patch-owner-states.py /data/lm.sqlite3
"""
import json
import sqlite3
import sys

IN_SCOPE_STATES = ("matched", "no_record", "not_resolved", "not_looked_up")
OUT_OF_SCOPE = "out_of_scope"

def main(path):
    cx = sqlite3.connect(path)
    label = dict(cx.execute("SELECT c, t FROM d_ostate"))
    hist = {}
    for code, n in cx.execute(
            "SELECT state, COUNT(*) FROM owner WHERE in_scope = 1 GROUP BY state"):
        hist[label[code]] = n
    for k in IN_SCOPE_STATES:
        hist.setdefault(k, 0)
    owners = cx.execute("SELECT COUNT(*) FROM owner").fetchone()[0]
    in_scope = cx.execute(
        "SELECT COUNT(*) FROM owner WHERE in_scope = 1").fetchone()[0]

    scoped_sum = sum(hist[k] for k in IN_SCOPE_STATES)
    if scoped_sum != in_scope:
        sys.exit("refusing to write: in-scope states sum to %d, not %d"
                 % (scoped_sum, in_scope))
    if set(hist) - set(IN_SCOPE_STATES):
        sys.exit("refusing to write: unexpected state on an in-scope owner: %s"
                 % sorted(set(hist) - set(IN_SCOPE_STATES)))
    hist[OUT_OF_SCOPE] = owners - in_scope

    was = cx.execute("SELECT v FROM meta WHERE k = 'owner_states'").fetchone()
    print("was: %s" % (was[0] if was else "(absent)"))
    print("now: %s" % json.dumps(hist, sort_keys=True))
    cx.execute("UPDATE meta SET v = ? WHERE k = 'owner_states'",
               (json.dumps(hist, sort_keys=True),))
    cx.commit()
    print("no_record share of owners in scope: %.2f%%"
          % (100.0 * hist["no_record"] / in_scope))

if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
