#!/usr/bin/env python3
"""Build the Landlord Mapper SQLite database from the pipeline's CSV output.

Re-runnable. Reads the same CSVs the in-memory server used to read, reproduces
its load semantics leg for leg, and writes an indexed database the server opens
read-only. Builds to a temp file and renames atomically, so a rebuild never
leaves the server reading a half-written database.

  python3 build-db.py [--data DIR] [--out FILE] [--no-group]

Defaults: --data ~/landlord-mapper-ui/data, --out ~/landlord-mapper-db/lm.sqlite3

THE OWNER-GROUPING JOIN. parcel_group_assign.csv is a sidecar exported out of the
pipeline's situs_group_assignments_final target by export-group-sidecar.R. It
carries one integer label per parcel that folds an entity's name variants into
one owner: six of the top ten rows of the units ranking used to be fragments of
the Housing Authority of the City of Austin. Read the four rules below before
touching it, and read --no-group, which builds the same database with the join
switched off so its effect can be isolated from the data's.

1. group_assign IS A FUNCTION OF THE OWNER, NOT OF THE PARCEL, so the join tries
   the owner-bearing key first: (county, norm_pid, situs_address, norm(owner_name)).
   On (county, situs_pID) alone -- no address -- the sidecar has ten keys carrying
   two different labels, one address and two owners, and joining on that both
   picks a label at random and inflates the roll by 19,029 rows.

   THE OWNER KEY MISSES 42,990 ROLL ROWS ON ITS OWN, so it is not the only key.
   The grouping frame's owner_name has been through the pipeline's cleaning and
   the roll's has not: the roll says AER LAMAR PROPERTY OWNER LLC where the
   sidecar says AER LAMAR PROPERTY LLC, and EXEMPT TRUST FBO S MONKARASH against
   EXEMPT TRUST S MONKARASH. 24,309 of those 42,990 carry a real label, so
   keying on the owner alone silently drops 4% of the fold. Those rows fall back
   to (county, norm_pid, situs_address), which is a bijection with the roll's own
   de-duplication key -- 2,117,593 keys on both sides -- and so covers all but
   the 41 rows the grouping frame never carried. Only 7 address keys hold two
   different labels, every one of them a 0 against a single real group, so the
   sidecar collapses them by preferring the real one. Trying the owner key first
   is what keeps those 7 decided by the owner rather than by that rule.
2. LABEL 0 IS THE "NOT GROUPED" SENTINEL and covers about 1.53 M parcels. Those
   keep exactly today's identity, owner_key(name, address). Giving each of them
   its own entity would be strictly worse than today for 1.5 M parcels.
3. THE LABELS ARE NOT STABLE ACROSS PIPELINE RUNS. They are positions in a
   largest-component-first ordering, so any change in component sizes renumbers
   every group after it. No label reaches the database, a URL or a shareable
   link: a group's owner_id is sha1 over the alphabetically first parcel key in
   the component, which is derived from the membership itself and so survives a
   renumbering.
4. PARCEL AND UNIT TOTALS MUST NOT MOVE when the join is switched on -- it
   changes which owner a parcel is attributed to and nothing else. Owner counts
   and every owner-denominated share DO move, because folding name variants
   shrinks the owner denominator. --no-group exists to prove the first half.

WHY THE LOAD LOGIC IS DUPLICATED HERE rather than imported from server.py: the
server no longer contains it. Every derived value the server used to compute at
load time is computed here once and stored, so the server's job is reduced to
formatting rows it reads back. The formatting functions are unchanged, so the
displayed numbers are unchanged.

Three things in here are load-bearing and must not be "simplified":

1. The parcel key is (county, situs_pID), never situs_pID alone. 466,956 of
   1,172,494 distinct IDs are carried by more than one county roll, so a unique
   index or a join on the bare ID attaches the wrong building.
2. The registry join needs situs_pID AND situs_address together. The ID narrows
   to a handful of candidate parcels and the address decides which one, or
   decides that none of them is it. Joining on the ID alone previously handed
   1,246 owners a franchise filing that was not theirs.
3. Row order is the CSV's order after de-duplication, and rowid is therefore the
   old in-memory index plus one. Search results, unsorted browse order and every
   sort tie-break inherit that order, so preserving it is what keeps the output
   byte-identical.

THE TABLES THIS FILE CREATES ARE STAGING TABLES. SCHEMA below is the all-TEXT
shape the load writes, which is the shape the load semantics are stated in; the
last step of main() hands it to typed.retype(), which converts parcel and owner
to the typed schema the server reads. Both paths therefore produce one schema,
and a refresh-data.sh run cannot quietly rebuild an untyped 1.80 GB database.
Read typed.py for what the typing does and why each step is value-preserving.
"""
import csv
import glob
import hashlib
import json
import os
import sqlite3
import sys
import time

import typed

csv.field_size_limit(10 * 1024 * 1024)

DATA = os.path.expanduser("~/landlord-mapper-ui/data")
OUT = os.path.expanduser("~/landlord-mapper-db/lm.sqlite3")
GROUP = True

args = sys.argv[1:]
while args:
    a = args.pop(0)
    if a == "--data":
        DATA = os.path.expanduser(args.pop(0))
    elif a == "--out":
        OUT = os.path.expanduser(args.pop(0))
    elif a == "--no-group":
        GROUP = False
    else:
        sys.exit("unknown argument %r" % a)

PARCEL_FILES = ("parcel_roll_5county.csv", "austin_parcel_data_merged.csv")
GROUP_FILE = "parcel_group_assign.csv"

PARCEL_COLS = [
    "situs_year", "situs_pID", "situs_address", "situs_zip",
    "totalsqftlivingarea", "property_units", "year_built", "state_code",
    "is_owner_out_of_state", "is_owner_occupied", "is_financialized",
    "is_mom_and_pop", "legallocationdesc", "owner_name", "owner_address",
    "owner_zip", "agent_name", "recent_purchase_date", "totalpropmktvalue",
    "county",
]
P = {name: i for i, name in enumerate(PARCEL_COLS)}

SCRAPE_COLS = [
    "owner_name_scraped", "owner_scraped_title", "owner_address_scraped",
    "owner_active_year", "corp_business_name", "corp_TTN", "corp_mail_address",
    "corp_right_to_transact_business_tx_status", "corp_state_of_formation",
    "corp_sos_registration_status", "corp_effective_sos_registration_date",
    "corp_tx_sos_file_num", "corp_registered_agent_name",
    "corp_registered_agent_mail_add", "scrape_status", "situs_pID",
    "situs_address",
]
S = {name: i for i, name in enumerate(SCRAPE_COLS)}

MATCHED = "matched"
NO_RECORD = "no_record"
NOT_RESOLVED = "not_resolved"
NOT_LOOKED_UP = "not_looked_up"
OUT_OF_SCOPE = "out_of_scope"

SCOPE_OCCUPIED = "occupied"
SCOPE_SIZE = "size"
SCOPE_NOSIZE = "nosize"


# --- the helpers, copied unchanged so the derived values are unchanged ------
def norm_pid(v):
    v = (v or "").strip()
    v = v.lstrip("0")
    return v or "0"


def norm_txt(v):
    return " ".join((v or "").upper().split())


def owner_key(name, addr):
    return norm_txt(name) + "\x1f" + norm_txt(addr)


def owner_id(name, addr):
    return hashlib.sha1(owner_key(name, addr).encode("utf-8")).hexdigest()[:12]


def group_key3(cty, pid, addr):
    """The sidecar's address key: county, normalised ID, situs address.

    A bijection with this build's own de-duplication key, so it covers the roll.
    """
    return "\x1f".join((norm_txt(cty), pid, norm_txt(addr)))


def group_key4(k3, name):
    """The sidecar's owner key: the address key plus the normalised owner name.

    Tried first because group_assign is a function of the owner. See rule 1 in
    the module docstring for why it cannot be the only key.
    """
    return k3 + "\x1f" + norm_txt(name)


def group_owner_id(anchor_key):
    """A group's owner_id, derived from the group's own membership.

    anchor_key is the alphabetically first join key in the component, so the id
    changes only when the membership does -- not when a rerun renumbers the
    labels (rule 3). The \\x1e prefix keeps the group ids in a different hash
    namespace from the ungrouped owner_id() ones, which are hashes of
    owner_key() and carry no such prefix.
    """
    return hashlib.sha1(
        ("\x1egroup\x1f" + anchor_key).encode("utf-8")).hexdigest()[:12]


def to_int(v):
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return int(v)
    try:
        return int(float((v or "").strip()))
    except (TypeError, ValueError, AttributeError):
        return 0


def to_float(v):
    try:
        return float((v or "").strip())
    except (TypeError, ValueError, AttributeError):
        return None


def is_true(v):
    return (v or "").strip().upper() in ("TRUE", "T", "1", "YES")


def is_false(v):
    return (v or "").strip().upper() in ("FALSE", "F", "0", "NO")


def fast_int(v):
    return int(v) if v.isdigit() else to_int(v)


def fast_true(v):
    return v[:1] in ("T", "t", "1", "Y", "y")


def datestamp(v):
    v = (v or "").strip()
    if not v or v.upper() in ("NA", "NULL"):
        return ""
    return v.split(" ")[0]


def parcel_in_scope(rec):
    units = to_float(rec[P["property_units"]])
    if units is None or units == 0:
        return False
    if units > 4:
        return True
    return (is_true(rec[P["is_financialized"]])
            and is_false(rec[P["is_owner_occupied"]]))


def mtime(path):
    try:
        return time.strftime("%Y-%m-%d %H:%M",
                             time.localtime(os.path.getmtime(path)))
    except OSError:
        return "n/a"


def parcel_path():
    for name in PARCEL_FILES:
        p = os.path.join(DATA, name)
        if os.path.exists(p):
            return p
    return os.path.join(DATA, PARCEL_FILES[-1])


# --- schema ---------------------------------------------------------------
SCHEMA = """
PRAGMA page_size = 4096;

CREATE TABLE parcel (
  -- STAGING. The twenty roll columns as the exact text the CSV carried, which is
  -- the shape the load semantics below are stated in. typed.retype() converts
  -- this table at the end of main(); it keeps every one of these strings
  -- retrievable byte for byte, because the server's money()/num()/dash()
  -- formatting parses them and /export.csv writes all twenty verbatim
  situs_year TEXT, situs_pID TEXT, situs_address TEXT, situs_zip TEXT,
  totalsqftlivingarea TEXT, property_units TEXT, year_built TEXT,
  state_code TEXT, is_owner_out_of_state TEXT, is_owner_occupied TEXT,
  is_financialized TEXT, is_mom_and_pop TEXT, legallocationdesc TEXT,
  owner_name TEXT, owner_address TEXT, owner_zip TEXT, agent_name TEXT,
  recent_purchase_date TEXT, totalpropmktvalue TEXT, county TEXT,
  -- derived, all of it precomputed so no request re-parses text
  pid_norm TEXT, pid_sort TEXT, county_norm TEXT, addr_upper TEXT,
  owner_name_norm TEXT, zip_trim TEXT, pdate TEXT, owner_id TEXT,
  in_scope INTEGER, f_oos INTEGER, f_occ INTEGER, f_fin INTEGER, f_mom INTEGER,
  n_val INTEGER, n_units INTEGER, n_sqft INTEGER, n_yb INTEGER
);

CREATE TABLE owner (
  owner_id TEXT PRIMARY KEY,
  name TEXT, address TEXT,
  in_scope INTEGER,          -- 1 when any parcel is in the lookup scope
  state TEXT,                -- matched / no_record / not_resolved /
                             -- not_looked_up / out_of_scope
  -- totals over ALL of this owner's parcels: what owner_totals() returned
  n_parcels INTEGER, tot_value INTEGER, tot_sqft INTEGER, tot_units INTEGER,
  median_value INTEGER,
  n_out_of_state INTEGER, n_owner_occupied INTEGER,
  counties_all TEXT,         -- "travis 12\\x1fbexar 3", count desc then first seen
  zips_all TEXT,             -- space joined, sorted
  first_purchase TEXT, last_purchase TEXT,
  -- totals over IN-SCOPE parcels only: what the rankings rank on
  n_parcels_scope INTEGER, scope_units INTEGER, scope_value INTEGER,
  counties_scope TEXT,       -- space joined, sorted
  first_rowid INTEGER,       -- lowest parcel rowid, the row the name came from
  first_scope_rowid INTEGER, -- the ranking tie-break, see below
  -- denormalised from the filing so a ranking row needs no join
  corp_name TEXT, agent TEXT
);

CREATE TABLE filing (
  owner_id TEXT PRIMARY KEY,
  corp_name TEXT, ttn TEXT, mail TEXT, mail_norm TEXT, rtt TEXT,
  formation TEXT, sos_status TEXT, sos_date TEXT, file_num TEXT,
  agent TEXT, agent_norm TEXT, queried_rows INTEGER, raw_status TEXT
);

CREATE TABLE officer (
  owner_id TEXT, ord INTEGER, name TEXT, name_norm TEXT, title TEXT, year TEXT
);

CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT);

-- The pipeline's owner grouping, one row per ungrouped owner_id a group entity
-- absorbed. group_id is the group's own owner_id, never the pipeline's integer
-- label (rule 3). Left empty by --no-group and by a build with no sidecar.
CREATE TABLE owner_group (
  group_id TEXT, owner_id TEXT, role TEXT, confidence REAL, method TEXT
);
"""

INDEXES = """
-- a parcel is identified by county plus ID, never by ID alone
CREATE INDEX ix_p_pid ON parcel(pid_norm);
CREATE INDEX ix_p_county_pid ON parcel(county_norm, pid_norm);
CREATE INDEX ix_p_owner ON parcel(owner_id);
CREATE INDEX ix_p_addr ON parcel(addr_upper);
-- the browse and export orderings
CREATE INDEX ix_p_scope_val ON parcel(n_val) WHERE in_scope = 1;
CREATE INDEX ix_p_scope_units ON parcel(n_units) WHERE in_scope = 1;
CREATE INDEX ix_p_scope_county ON parcel(county_norm, n_val) WHERE in_scope = 1;
CREATE INDEX ix_p_scope_zip ON parcel(zip_trim, n_val) WHERE in_scope = 1;
CREATE INDEX ix_p_county_zip ON parcel(county_norm, zip_trim);
CREATE INDEX ix_p_val ON parcel(n_val);
-- A full index for every /explore sort column, so an unfiltered sort over
-- the whole roll is an indexed read and not a 2.1 M row external sort. The
-- server has no sorting cap any more and relies on these existing: measured,
-- page 1 of an unfiltered sort goes from 0.37-0.52s (up to 7.70s deep) to
-- effectively zero. Costs about 262 MB of database.
CREATE INDEX ix_p_units ON parcel(n_units);
CREATE INDEX ix_p_sqft ON parcel(n_sqft);
CREATE INDEX ix_p_yb ON parcel(n_yb);
CREATE INDEX ix_p_county_raw ON parcel(county);
CREATE INDEX ix_p_zip_raw ON parcel(situs_zip);
CREATE INDEX ix_p_owner_name ON parcel(owner_name_norm);
CREATE INDEX ix_p_pid_sort ON parcel(pid_sort);
CREATE INDEX ix_p_pdate ON parcel(pdate);
-- The row COUNT behind the "N parcels match" denominator on /explore was
-- the dominant cost left once sorting became indexed: the page query is 40
-- indexed rows and costs nothing, while the count had to read the facet
-- columns out of the table for every candidate row. This makes those counts
-- index-only. Measured 1.491s -> 0.192s across eight facet combinations,
-- for 33 MB of database.
CREATE INDEX ix_p_facets ON parcel(in_scope, f_fin, f_occ, f_mom, f_oos, n_yb, n_units);
CREATE INDEX ix_og_group ON owner_group(group_id);
CREATE INDEX ix_og_owner ON owner_group(owner_id);
CREATE INDEX ix_officer_owner ON officer(owner_id, ord);
CREATE INDEX ix_officer_name ON officer(name_norm);
CREATE INDEX ix_filing_agent ON filing(agent_norm) WHERE agent_norm <> '';
CREATE INDEX ix_filing_mail ON filing(mail_norm) WHERE mail_norm <> '';
-- the three ranking orders, partial so they only carry owners a ranking can
-- show. name then first_scope_rowid reproduces the old Python tie-break: a
-- stable sort over a dict built in ascending in-scope parcel order.
CREATE INDEX ix_owner_rank_value ON owner(scope_value DESC, name, first_scope_rowid)
  WHERE in_scope = 1;
CREATE INDEX ix_owner_rank_units ON owner(scope_units DESC, name, first_scope_rowid)
  WHERE in_scope = 1;
CREATE INDEX ix_owner_rank_parcels ON owner(n_parcels_scope DESC, name, first_scope_rowid)
  WHERE in_scope = 1;
"""


def log(msg):
    sys.stderr.write("[build-db] %s\n" % msg)
    sys.stderr.flush()


def load_groups(st):
    """Read the owner-grouping sidecar.

    Returns three maps: owner key -> label, address key -> label, and
    label -> owner_id. Label 0 is carried in both key maps on purpose: without it
    there is no way to tell a parcel the pipeline left ungrouped from a parcel the
    sidecar never mentioned, and only the second of those is a join failure.
    Returns (None, None, None) when the join is off or the file is absent, which
    is what makes the whole grouping a no-op rather than an error.

    The address map keeps the real label when a key carries both a real one and a
    0; there are 7 such keys and no key carries two different real labels.

    A group's owner_id is the sha1 of its alphabetically first owner key, so it
    is computed here, where the whole component is visible, and not per parcel.
    """
    if not GROUP:
        st["group_join"] = False
        st["group_off_reason"] = "--no-group"
        log("owner grouping OFF (--no-group)")
        return None, None, None
    path = os.path.join(DATA, GROUP_FILE)
    if not os.path.exists(path):
        st["group_join"] = False
        st["group_off_reason"] = "no %s in %s" % (GROUP_FILE, DATA)
        log("owner grouping OFF (no %s)" % GROUP_FILE)
        return None, None, None
    log("reading %s" % path)
    by_owner = {}
    by_addr = {}
    anchor = {}
    n = 0
    dupes = 0
    addr_collapsed = 0
    with open(path, newline="", encoding="utf-8", errors="replace") as f:
        r = csv.reader(f)
        head = next(r)
        need = ("k_county", "k_pid", "k_addr", "k_owner", "group_assign")
        missing = [c for c in need if c not in head]
        if missing:
            st["errors"].append(
                "%s is missing %r; header was %r" % (GROUP_FILE, missing, head))
            raise SystemExit(2)
        ic, ip, ia, io, ig = (head.index(c) for c in need)
        for raw in r:
            k3 = "\x1f".join((raw[ic], raw[ip], raw[ia]))
            k4 = k3 + "\x1f" + raw[io]
            g = int(raw[ig] or 0)
            if k4 in by_owner:
                # The sidecar is written one row per owner key, so this is a
                # contract violation rather than something to resolve quietly.
                # Reported, and the real label wins, which is the export's rule.
                dupes += 1
                if g == 0:
                    continue
            else:
                n += 1
            by_owner[k4] = g
            old = by_addr.get(k3)
            if old is None:
                by_addr[k3] = g
            elif old != g:
                addr_collapsed += 1
                if old == 0:
                    by_addr[k3] = g
                elif g != 0:
                    raise SystemExit(
                        "load_groups: address key %r carries two different real "
                        "labels (%d and %d); preferring either would be a guess "
                        "and the owner key is the only thing that can decide it"
                        % (k3, old, g))
            if g and (g not in anchor or k4 < anchor[g]):
                anchor[g] = k4
    gid = {g: group_owner_id(k) for g, k in anchor.items()}
    if len(set(gid.values())) != len(gid):
        raise SystemExit(
            "load_groups: two groups hashed to the same owner_id; the anchor "
            "keys are not distinct and the fold would merge unrelated entities")
    st["group_join"] = True
    st["group_file"] = GROUP_FILE
    st["group_mtime"] = mtime(path)
    st["group_sidecar_rows"] = n
    st["group_sidecar_addr_keys"] = len(by_addr)
    st["group_sidecar_dupe_keys"] = dupes
    # Rows, not keys: a key holding 0, real, 0 collapses twice.
    st["group_sidecar_addr_collapses"] = addr_collapsed
    st["group_labels"] = len(gid)
    st["group_sidecar_grouped"] = sum(1 for g in by_owner.values() if g)
    log("%s sidecar rows on %s address keys, %s labels, %s grouped keys, "
        "%s duplicate owner keys, %s rows collapsed onto an address key"
        % (format(n, ",d"), format(len(by_addr), ",d"), format(len(gid), ",d"),
           format(st["group_sidecar_grouped"], ",d"), format(dupes, ",d"),
           format(addr_collapsed, ",d")))
    return by_owner, by_addr, gid


def load_parcels(cx, st, by_owner=None, by_addr=None, gid=None):
    path = parcel_path()
    log("reading %s" % path)
    dupes = 0
    counties = {}
    scope_counties = {}
    years = {}
    occ = 0
    n_scope = 0
    n = 0
    zips = {}
    n_grouped = 0         # rows the sidecar gave a real (non-zero) label
    n_ungrouped = 0       # rows the sidecar labelled 0, which keep today's id
    n_nogroup_row = 0     # rows the sidecar does not mention at all: a join miss
    n_by_owner = 0        # rows matched on the owner key
    n_by_addr = 0         # rows matched on the address key after an owner miss
    n_addr_real = 0       # of those, the ones that recovered a real label
    fold = set()          # (group owner_id, the ungrouped owner_id it absorbed)
    seen_key = set()      # (pid_norm, county, situs_address) - the de-dupe rule
    batch = []
    ncols = len(cx.execute("SELECT * FROM parcel LIMIT 0").description)
    ins = ("INSERT INTO parcel VALUES (" + ",".join("?" * ncols) + ")")
    with open(path, newline="", encoding="utf-8", errors="replace") as f:
        r = csv.reader(f)
        head = next(r)
        for name in PARCEL_COLS:
            if name not in head:
                st["errors"].append(
                    "parcel file is missing column %r; header was %r"
                    % (name, head))
                raise SystemExit(2)
        pos = {name: head.index(name) for name in PARCEL_COLS}
        picks = [pos[name] for name in PARCEL_COLS]
        width = len(head)
        for raw in r:
            if len(raw) != width:
                st["parcel_bad_width"] = st.get("parcel_bad_width", 0) + 1
                continue
            pid = norm_pid(raw[pos["situs_pID"]])
            cty = raw[pos["county"]]
            addr = raw[pos["situs_address"]]
            # Dropped only when this county roll already gave us this ID at this
            # address, which is a repeated record. The same ID in a second county
            # is a different building.
            k = (pid, cty, addr)
            if k in seen_key:
                dupes += 1
                continue
            seen_key.add(k)
            rec = tuple(raw[i] for i in picks)
            sc = parcel_in_scope(rec)
            units = fast_int(rec[P["property_units"]])
            val = fast_int(rec[P["totalpropmktvalue"]])
            sqft = fast_int(rec[P["totalsqftlivingarea"]])
            y = rec[P["year_built"]]
            yb = int(y) if len(y) == 4 and y.isdigit() else 0
            oid = owner_id(rec[P["owner_name"]], rec[P["owner_address"]])
            if by_owner is not None:
                # The fold: a grouped parcel is attributed to the group's entity
                # instead of to this one spelling of the owner's name. Ungrouped
                # parcels (label 0) and any parcel the sidecar does not mention
                # keep oid exactly as computed above, which is today's behaviour.
                k3 = group_key3(cty, pid, addr)
                g = by_owner.get(group_key4(k3, rec[P["owner_name"]]))
                if g is None:
                    # The roll's owner_name did not survive the pipeline's
                    # cleaning; the address key decides. Rule 1 in the docstring.
                    g = by_addr.get(k3)
                    if g is not None:
                        n_by_addr += 1
                        if g:
                            n_addr_real += 1
                else:
                    n_by_owner += 1
                if g is None:
                    n_nogroup_row += 1
                elif g == 0:
                    n_ungrouped += 1
                else:
                    n_grouped += 1
                    ggid = gid[g]
                    fold.add((ggid, oid))
                    oid = ggid
            ztrim = rec[P["situs_zip"]].strip()
            batch.append(rec + (
                pid, pid.rjust(14, "0"), norm_txt(cty),
                rec[P["situs_address"]].upper(),
                norm_txt(rec[P["owner_name"]]), ztrim,
                datestamp(rec[P["recent_purchase_date"]]), oid,
                1 if sc else 0,
                1 if fast_true(rec[P["is_owner_out_of_state"]]) else 0,
                1 if fast_true(rec[P["is_owner_occupied"]]) else 0,
                1 if fast_true(rec[P["is_financialized"]]) else 0,
                1 if fast_true(rec[P["is_mom_and_pop"]]) else 0,
                val, units, sqft, yb))
            n += 1
            counties[cty] = counties.get(cty, 0) + 1
            yr = rec[P["situs_year"]].strip()
            if yr:
                years[yr] = years.get(yr, 0) + 1
            if sc:
                n_scope += 1
                scope_counties[cty] = scope_counties.get(cty, 0) + 1
                if ztrim:
                    zips[ztrim] = zips.get(ztrim, 0) + 1
            if is_true(rec[P["is_owner_occupied"]]):
                occ += 1
            if len(batch) >= 20000:
                cx.executemany(ins, batch)
                batch = []
                if n % 500000 == 0:
                    log("  %s parcel rows" % format(n, ",d"))
    if batch:
        cx.executemany(ins, batch)
    del seen_key
    st["parcel_rows"] = n
    st["parcel_dupes_dropped"] = dupes
    st["counties"] = counties
    st["roll_years"] = years
    st["scope_counties"] = scope_counties
    st["parcels_owner_occupied"] = occ
    st["parcels_in_scope"] = n_scope
    st["parcels_out_of_scope"] = n - n_scope
    st["parcel_file"] = os.path.basename(path)
    st["parcel_mtime"] = mtime(path)
    st["scope_zips"] = zips
    log("%s parcel rows, %s repeated records dropped, %s in scope"
        % (format(n, ",d"), format(dupes, ",d"), format(n_scope, ",d")))
    if by_owner is not None:
        # The join is a per-row lookup that rewrites owner_id and nothing else,
        # so every kept row lands in exactly one of the three buckets and the
        # row count cannot move (rule 4). A slip in the branch ladder above is
        # the one way that stops being true, so it is checked rather than argued.
        if n_grouped + n_ungrouped + n_nogroup_row != n:
            raise SystemExit(
                "load_parcels: %d grouped + %d ungrouped + %d unmatched != %d "
                "rows loaded; the grouping join lost or double-counted rows"
                % (n_grouped, n_ungrouped, n_nogroup_row, n))
        st["parcels_grouped"] = n_grouped
        st["parcels_ungrouped"] = n_ungrouped
        st["parcels_no_group_row"] = n_nogroup_row
        st["parcels_keyed_by_owner"] = n_by_owner
        st["parcels_keyed_by_address"] = n_by_addr
        st["parcels_keyed_by_address_real"] = n_addr_real
        st["group_entities_used"] = len(set(g for g, _o in fold))
        st["group_fold_pairs"] = len(fold)
        log("grouping: %s parcels grouped into %s entities, %s ungrouped "
            "(label 0), %s not in the sidecar, %s folded owner ids"
            % (format(n_grouped, ",d"),
               format(st["group_entities_used"], ",d"),
               format(n_ungrouped, ",d"), format(n_nogroup_row, ",d"),
               format(len(fold), ",d")))
        log("  keys: %s matched on the owner key, %s on the address key after "
            "an owner miss (%s of them recovering a real label)"
            % (format(n_by_owner, ",d"), format(n_by_addr, ",d"),
               format(n_addr_real, ",d")))
    return fold


def index_parcels(cx):
    log("indexing parcel")
    for stmt in INDEXES.strip().split(";"):
        s = stmt.strip()
        if s and "ON parcel" in s:
            cx.execute(s)
    cx.commit()


def load_scrape(cx, st):
    files = []
    total = os.path.join(DATA, "owner_data_total.csv")
    if os.path.exists(total):
        files.append(total)
    files += sorted(glob.glob(os.path.join(DATA, "owner_data_part_*.csv")))
    extra = os.environ.get("LM_EXTRA_OWNER_CSV", "").strip()
    if extra:
        files += [p for p in (x.strip() for x in extra.split(",")) if p]
    by_parcel = {}
    nrows = kept = no_parcel = addr_clash = 0
    clash_examples = []
    seen = set()
    status_rows = {}
    newest = 0.0
    cand_cache = {}
    cur = cx.cursor()
    for path in files:
        log("reading %s" % os.path.basename(path))
        newest = max(newest, os.path.getmtime(path))
        try:
            fh = open(path, newline="", encoding="utf-8", errors="replace")
        except OSError as ex:
            st["errors"].append("could not read %s: %s" % (path, ex))
            continue
        with fh as f:
            r = csv.reader(f)
            try:
                head = next(r)
            except StopIteration:
                continue
            if "situs_pID" not in head:
                st["errors"].append(
                    "scrape file %s has no situs_pID column; header %r"
                    % (os.path.basename(path), head))
                continue
            missing = [c for c in SCRAPE_COLS if c not in head]
            if missing:
                st["errors"].append(
                    "scrape file %s is missing %r"
                    % (os.path.basename(path), missing))
            pos = {n: (head.index(n) if n in head else -1) for n in SCRAPE_COLS}
            width = len(head)
            for raw in r:
                if len(raw) != width:
                    st["scrape_bad_width"] = st.get("scrape_bad_width", 0) + 1
                    continue
                rec = tuple((raw[pos[n]] if pos[n] >= 0 else "")
                            for n in SCRAPE_COLS)
                if rec in seen:
                    continue
                seen.add(rec)
                nrows += 1
                # The join is pID plus address. The ID narrows the row to a
                # handful of candidate buildings across the county rolls and the
                # situs address decides which one, or decides that none is it.
                pid = norm_pid(rec[S["situs_pID"]])
                cands = cand_cache.get(pid)
                if cands is None:
                    cands = cur.execute(
                        "SELECT rowid, situs_address FROM parcel "
                        "WHERE pid_norm = ? ORDER BY rowid", (pid,)).fetchall()
                    cands = [(rid, norm_txt(a)) for rid, a in cands]
                    if len(cand_cache) < 400000:
                        cand_cache[pid] = cands
                if not cands:
                    no_parcel += 1
                    continue
                want = norm_txt(rec[S["situs_address"]])
                pi = None
                for rid, a in cands:
                    if a == want:
                        pi = rid
                        break
                if pi is None:
                    addr_clash += 1
                    if len(clash_examples) < 3:
                        clash_examples.append(
                            "%s: scrape says %s, roll says %s"
                            % (rec[S["situs_pID"]].strip(),
                               rec[S["situs_address"]].strip(),
                               " / ".join(
                                   cur.execute(
                                       "SELECT situs_address FROM parcel "
                                       "WHERE rowid = ?", (rid,)).fetchone()[0].strip()
                                   for rid, _a in cands[:3])))
                    continue
                kept += 1
                stt = rec[S["scrape_status"]].strip()
                status_rows[stt] = status_rows.get(stt, 0) + 1
                by_parcel.setdefault(pi, []).append(rec)
    st["scrape_files"] = len(files)
    st["scrape_rows"] = nrows
    st["scrape_rows_joined"] = kept
    st["scrape_rows_no_parcel"] = no_parcel
    st["scrape_rows_addr_clash"] = addr_clash
    st["scrape_clash_examples"] = clash_examples
    st["scrape_status_rows"] = status_rows
    st["scrape_parcels"] = len(by_parcel)
    st["scrape_newest_mtime"] = (
        time.strftime("%Y-%m-%d %H:%M", time.localtime(newest))
        if newest else "n/a")
    log("%s scrape rows read, %s joined, %s no parcel, %s address clash"
        % (format(nrows, ",d"), format(kept, ",d"), no_parcel, addr_clash))
    return by_parcel


def build_owners(cx, st):
    """Owner rows straight out of the parcel table.

    Everything the old owner_totals() and owner_profile_band() computed by
    walking a Python list is a GROUP BY here, computed once at build time.
    """
    log("aggregating owners")
    cx.execute("""
        INSERT INTO owner (owner_id, name, address, in_scope, state,
            n_parcels, tot_value, tot_sqft, tot_units, median_value,
            n_out_of_state, n_owner_occupied, counties_all, zips_all,
            first_purchase, last_purchase, n_parcels_scope, scope_units,
            scope_value, counties_scope, first_rowid, first_scope_rowid,
            corp_name, agent)
        SELECT owner_id, '', '',
               CASE WHEN SUM(in_scope) > 0 THEN 1 ELSE 0 END, '',
               COUNT(*), SUM(n_val), SUM(n_sqft), SUM(n_units), 0,
               SUM(f_oos), SUM(f_occ), '', '',
               COALESCE(MIN(NULLIF(pdate, '')), ''),
               COALESCE(MAX(NULLIF(pdate, '')), ''),
               SUM(in_scope), SUM(CASE WHEN in_scope THEN n_units ELSE 0 END),
               SUM(CASE WHEN in_scope THEN n_val ELSE 0 END), '',
               MIN(rowid), MIN(CASE WHEN in_scope THEN rowid END), '', ''
        FROM parcel GROUP BY owner_id
    """)
    cx.commit()
    # name and address as printed on the roll. The old dict kept the spelling
    # from the first parcel it saw for that owner, so this takes the same row.
    cx.execute("""
        UPDATE owner SET
          name = (SELECT owner_name FROM parcel WHERE rowid = owner.first_rowid),
          address = (SELECT owner_address FROM parcel WHERE rowid = owner.first_rowid)
    """)
    cx.commit()
    log("  %s owners" % format(cx.execute("SELECT COUNT(*) FROM owner").fetchone()[0], ",d"))

    # median market value over all of an owner's parcels. The old code sorted the
    # values and took the middle one, averaging the two middles on an even count
    # with floor division; values are never negative so SQLite's truncating
    # division agrees.
    log("  median values")
    cx.execute("""
        WITH r AS (
          SELECT owner_id, n_val,
                 ROW_NUMBER() OVER (PARTITION BY owner_id ORDER BY n_val) rn,
                 COUNT(*) OVER (PARTITION BY owner_id) c
          FROM parcel
        ), m AS (
          SELECT owner_id,
                 CASE WHEN c % 2 = 1
                      THEN MAX(CASE WHEN rn = (c + 1) / 2 THEN n_val END)
                      ELSE (MAX(CASE WHEN rn = c / 2 THEN n_val END)
                            + MAX(CASE WHEN rn = c / 2 + 1 THEN n_val END)) / 2
                 END AS med
          FROM r GROUP BY owner_id
        )
        UPDATE owner SET median_value = (SELECT med FROM m WHERE m.owner_id = owner.owner_id)
    """)
    cx.commit()

    # county spread, in the two shapes the pages ask for:
    #   counties_all   count descending then first seen, for the portfolio band
    #   counties_scope alphabetical names, for the rankings row and the export
    # ORDER BY inside group_concat needs SQLite 3.44+; the ordering is the whole
    # point here, so it is asserted rather than hoped for.
    log("  county and zip spread")
    cx.execute("""
        WITH c AS (
          SELECT owner_id, TRIM(county) cty, COUNT(*) n, MIN(rowid) mr
          FROM parcel GROUP BY owner_id, TRIM(county)
        ), o AS (
          SELECT owner_id,
                 GROUP_CONCAT(cty || ' ' || n, char(31) ORDER BY n DESC, mr) AS lst
          FROM c GROUP BY owner_id
        )
        UPDATE owner SET counties_all =
          COALESCE((SELECT lst FROM o WHERE o.owner_id = owner.owner_id), '')
    """)
    cx.execute("""
        WITH c AS (
          SELECT DISTINCT owner_id, TRIM(county) cty FROM parcel WHERE in_scope = 1
        ), o AS (
          SELECT owner_id, GROUP_CONCAT(cty, ' ' ORDER BY cty) AS lst
          FROM c GROUP BY owner_id
        )
        UPDATE owner SET counties_scope =
          COALESCE((SELECT lst FROM o WHERE o.owner_id = owner.owner_id), '')
    """)
    cx.execute("""
        WITH z AS (
          SELECT DISTINCT owner_id, zip_trim FROM parcel WHERE zip_trim <> ''
        ), o AS (
          SELECT owner_id, GROUP_CONCAT(zip_trim, ' ' ORDER BY zip_trim) AS lst
          FROM z GROUP BY owner_id
        )
        UPDATE owner SET zips_all =
          COALESCE((SELECT lst FROM o WHERE o.owner_id = owner.owner_id), '')
    """)
    cx.commit()
    st["owners"] = cx.execute("SELECT COUNT(*) FROM owner").fetchone()[0]
    st["owners_in_scope"] = cx.execute(
        "SELECT COUNT(*) FROM owner WHERE in_scope = 1").fetchone()[0]


# The four figures the unfiltered /rankings page states as its denominators, in
# the order server.py's rank_owners_count() returns them.
RANK_TOTALS_KEY = "rank_totals_in_scope"
RANK_TOTALS_SQL = ("SELECT COUNT(*), SUM(n_parcels_scope), SUM(scope_units), "
                   "SUM(scope_value) FROM owner WHERE in_scope = 1")


def rank_totals(cx, st):
    """Precompute the unfiltered ranking totals into `meta`.

    The server used to run this aggregate on every /rankings request. It has no
    filter in it, so it is a constant of the finished database, and computing it
    at request time meant reading the whole 101 MB owner table off the volume --
    the measured dominant term in a 21 s cold /rankings. It costs one scan here,
    once, next to a dozen other scans this build already does.

    Two of the four are computed independently elsewhere in this build, so they
    are asserted rather than trusted: owners_in_scope from the same COUNT at line
    620, and parcels_in_scope counted row by row while reading the CSV. They must
    agree, because n_parcels_scope is SUM(in_scope) per owner and every parcel
    belongs to exactly one owner. If they ever disagree, one of the two is wrong
    and the ranking page would state a denominator that contradicts /health.
    """
    log("ranking totals")
    t = cx.execute(RANK_TOTALS_SQL).fetchone()
    tot = [int(x or 0) for x in t]
    st[RANK_TOTALS_KEY] = tot
    for name, mine in (("owners_in_scope", tot[0]),
                       ("parcels_in_scope", tot[1])):
        theirs = st.get(name)
        if theirs is not None and int(theirs) != mine:
            raise SystemExit(
                "rank_totals: %s is %r counted directly but %r summed off the "
                "owner table; the two ways of counting the same population "
                "disagree and the rankings page would contradict /health"
                % (name, theirs, mine))
    log("  owners %s, in-scope parcels %s, units %s, value %s"
        % tuple(format(x, ",d") for x in tot))


def build_filings(cx, st, by_parcel):
    """Attach the franchise filing and officers to each owner.

    Reproduces the old _build_filings() exactly, including the order the rows
    are gathered in: ascending parcel index, then file order inside a parcel.
    That order decides which matched row becomes the filing of record.
    """
    log("building filings")
    cur = cx.cursor()
    parcel_ids = sorted(by_parcel)
    owner_of = {}
    B = 20000
    for i in range(0, len(parcel_ids), B):
        chunk = parcel_ids[i:i + B]
        qs = ",".join("?" * len(chunk))
        for rid, oid in cur.execute(
                "SELECT rowid, owner_id FROM parcel WHERE rowid IN (%s)" % qs,
                chunk):
            owner_of[rid] = oid
    owner_rows = {}
    for rid in parcel_ids:                      # ascending parcel index
        owner_rows.setdefault(owner_of[rid], []).extend(by_parcel[rid])

    filings = []
    officers = []
    state_of = []
    joined = 0
    joined_in_scope = 0
    # one lookup pass for just the owners that have rows
    oids = list(owner_rows)
    scope_flag = {}
    for i in range(0, len(oids), B):
        chunk = oids[i:i + B]
        qs = ",".join("?" * len(chunk))
        for oid, insc in cur.execute(
                "SELECT owner_id, in_scope FROM owner WHERE owner_id IN (%s)" % qs,
                chunk):
            scope_flag[oid] = insc

    for oid, rows in owner_rows.items():
        joined += 1
        if scope_flag.get(oid):
            joined_in_scope += 1
        statuses = set(r[S["scrape_status"]].strip() for r in rows)
        if MATCHED in statuses:
            state = MATCHED
        elif NO_RECORD in statuses:
            state = NO_RECORD
        elif NOT_RESOLVED in statuses:
            state = NOT_RESOLVED
        else:
            state = NOT_LOOKED_UP
        state_of.append((state, oid))
        if state != MATCHED:
            filings.append((oid, "", "", "", "", "", "", "", "", "", "", "",
                            len(rows), json.dumps(sorted(s for s in statuses))))
            continue
        mrows = [r for r in rows if r[S["scrape_status"]].strip() == MATCHED]
        base = mrows[0]
        seen_of = set()
        ordered = []
        for r in mrows:
            nm = norm_txt(r[S["owner_name_scraped"]])
            ttl = norm_txt(r[S["owner_scraped_title"]])
            if not nm or not ttl:
                continue
            if norm_txt(r[S["corp_business_name"]]) == nm:
                continue
            if (nm, ttl) in seen_of:
                continue
            seen_of.add((nm, ttl))
            ordered.append({"name": r[S["owner_name_scraped"]].strip(),
                            "title": r[S["owner_scraped_title"]].strip(),
                            "year": r[S["owner_active_year"]].strip()})
        ordered.sort(key=lambda d: (d["title"], d["name"]))
        for n, of in enumerate(ordered):
            officers.append((oid, n, of["name"], norm_txt(of["name"]),
                             of["title"], of["year"]))
        agent = base[S["corp_registered_agent_name"]].strip()
        mail = base[S["corp_mail_address"]].strip()
        filings.append((
            oid, base[S["corp_business_name"]].strip(),
            base[S["corp_TTN"]].strip(), mail, norm_txt(mail),
            base[S["corp_right_to_transact_business_tx_status"]].strip(),
            base[S["corp_state_of_formation"]].strip(),
            base[S["corp_sos_registration_status"]].strip(),
            base[S["corp_effective_sos_registration_date"]].strip(),
            base[S["corp_tx_sos_file_num"]].strip(),
            agent, norm_txt(agent), len(rows), json.dumps([MATCHED])))

    cx.executemany("INSERT INTO filing VALUES (" + ",".join("?" * 14) + ")", filings)
    cx.executemany("INSERT INTO officer VALUES (?,?,?,?,?,?)", officers)
    # every owner starts at the scope decision, then the answered ones are set
    cx.execute("UPDATE owner SET state = CASE WHEN in_scope = 1 THEN ? ELSE ? END",
               (NOT_LOOKED_UP, OUT_OF_SCOPE))
    cx.executemany("UPDATE owner SET state = ? WHERE owner_id = ?", state_of)
    cx.execute("""
        UPDATE owner SET corp_name = COALESCE(
            (SELECT corp_name FROM filing WHERE filing.owner_id = owner.owner_id), ''),
            agent = COALESCE(
            (SELECT agent FROM filing WHERE filing.owner_id = owner.owner_id), '')
    """)
    cx.commit()
    # in_scope = 1, because every page divides these counts by owners_in_scope.
    # The line above sets out-of-scope owners to OUT_OF_SCOPE and the line after
    # it overwrites the state of ANY owner the scrape answered for, scope
    # regardless, so an unfiltered histogram counted answered out-of-scope
    # owners into matched/no_record/not_resolved while the denominator stayed
    # in-scope only. The three published shares then summed to over 100%.
    states = dict(cx.execute(
        "SELECT state, COUNT(*) FROM owner WHERE in_scope = 1 GROUP BY state"))
    for k in (MATCHED, NO_RECORD, NOT_RESOLVED, NOT_LOOKED_UP):
        states.setdefault(k, 0)
    # Not a histogram bucket now: every owner outside the scope predicate,
    # answered or not. The pages that quote it mean exactly that.
    states[OUT_OF_SCOPE] = st["owners"] - st["owners_in_scope"]
    st["owner_states"] = states
    st["owners_with_scrape_rows"] = joined
    st["owners_in_scope_answered"] = joined_in_scope
    st["owners_answered_out_of_scope"] = joined - joined_in_scope
    st["network_officers"] = cx.execute(
        "SELECT COUNT(DISTINCT name_norm) FROM officer").fetchone()[0]
    st["network_agents"] = cx.execute(
        "SELECT COUNT(DISTINCT agent_norm) FROM filing WHERE agent_norm <> '' "
        "AND corp_name <> ''").fetchone()[0]
    st["network_mail"] = cx.execute(
        "SELECT COUNT(DISTINCT mail_norm) FROM filing WHERE mail_norm <> '' "
        "AND corp_name <> ''").fetchone()[0]
    log("filings %s, officers %s, states %r"
        % (format(len(filings), ",d"), format(len(officers), ",d"), states))


def build_owner_group(cx, st, fold):
    """Record which ungrouped owner ids each group entity absorbed.

    owner_group is the table this build has always created empty. It is the only
    place the fold is written down, and it is what a page would read to show that
    one entity is three spellings of a name. The rows point at owner ids that no
    longer have an owner row of their own, which is the point: the owner row is
    the group now, and the name it displays is the one on the parcel at
    owner.first_rowid.

    No group label goes in here (rule 3). group_id is the group's own owner_id.
    """
    if not fold:
        st["owner_group_rows"] = 0
        return
    log("recording the owner fold")
    cx.executemany(
        "INSERT INTO owner_group (group_id, owner_id, role, confidence, method) "
        "VALUES (?,?,'member',1.0,'situs_group_assignments_final')",
        sorted(fold))
    cx.commit()
    st["owner_group_rows"] = len(fold)
    st["owner_group_groups"] = cx.execute(
        "SELECT COUNT(DISTINCT group_id) FROM owner_group").fetchone()[0]
    # A group that absorbed exactly one owner id changed no owner's identity but
    # its own; the multi-member ones are the consolidation the fold was for.
    st["owner_group_multi"] = cx.execute(
        "SELECT COUNT(*) FROM (SELECT group_id FROM owner_group "
        "GROUP BY group_id HAVING COUNT(*) > 1)").fetchone()[0]
    log("  %s fold rows over %s entities, %s of them multi-owner"
        % (format(st["owner_group_rows"], ",d"),
           format(st["owner_group_groups"], ",d"),
           format(st["owner_group_multi"], ",d")))


def main():
    t0 = time.time()
    tmp = OUT + ".building"
    outdir = os.path.dirname(OUT)
    if outdir and not os.path.isdir(outdir):
        os.makedirs(outdir, exist_ok=True)
    for junk in (tmp, tmp + "-journal", tmp + "-wal", tmp + "-shm"):
        if os.path.exists(junk):
            os.unlink(junk)
    cx = sqlite3.connect(tmp)
    cx.executescript("PRAGMA journal_mode = OFF; PRAGMA synchronous = OFF;")
    cx.executescript(SCHEMA)
    st = {"errors": []}
    g_owner, g_addr, gid = load_groups(st)
    fold = load_parcels(cx, st, g_owner, g_addr, gid)
    del g_owner, g_addr, gid
    cx.commit()
    index_parcels(cx)
    st["parcel_pids"] = cx.execute(
        "SELECT COUNT(DISTINCT pid_norm) FROM parcel").fetchone()[0]
    st["parcel_pids_shared"] = cx.execute(
        "SELECT COUNT(*) FROM (SELECT pid_norm FROM parcel GROUP BY pid_norm "
        "HAVING COUNT(*) > 1)").fetchone()[0]
    by_parcel = load_scrape(cx, st)
    build_owners(cx, st)
    build_filings(cx, st, by_parcel)
    del by_parcel
    build_owner_group(cx, st, fold)
    del fold
    # The parcel and owner indexes belong to the typed tables and are created by
    # typed.retype(); building them here would index staging rows that are about
    # to be dropped.
    log("remaining indexes")
    for stmt in INDEXES.strip().split(";"):
        s = stmt.strip()
        if s and "ON parcel" not in s and "ON owner(" not in s:
            cx.execute(s)
    rank_totals(cx, st)
    st["load_seconds"] = round(time.time() - t0, 1)
    st["built_at"] = time.time()
    st["data_dir"] = DATA
    st["parcel_file_preference"] = list(PARCEL_FILES)
    for k, v in st.items():
        cx.execute("INSERT OR REPLACE INTO meta VALUES (?,?)",
                   (k, json.dumps(v)))
    cx.commit()
    # Everything above wrote the all-TEXT staging tables. This turns parcel and
    # owner into the typed ones and creates their indexes; the VACUUM after it is
    # what reclaims the pages the dropped columns freed and is not optional.
    typed.retype(cx, log=log)
    log("analyze")
    cx.execute("ANALYZE")
    cx.commit()
    log("vacuum")
    cx.execute("VACUUM")
    cx.commit()
    cx.close()
    os.replace(tmp, OUT)
    os.chmod(OUT, 0o644)
    log("wrote %s, %.1f MB, in %.1fs"
        % (OUT, os.path.getsize(OUT) / 1048576.0, time.time() - t0))
    for k in ("parcel_rows", "owners", "owners_in_scope", "parcels_in_scope",
              "scrape_rows", "scrape_rows_joined", "scrape_rows_addr_clash",
              "owner_states", "group_join", "parcels_grouped",
              "parcels_no_group_row", "owner_group_rows"):
        log("  %s = %r" % (k, st.get(k)))


if __name__ == "__main__":
    main()
