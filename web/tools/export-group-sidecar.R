# Export the Phase 3d owner-grouping labels out of the lm_work volume as a
# deduplicated sidecar the SQLite build can join to the parcel roll.
#
# /d is the lm_work docker volume, mounted read-only. Nothing here writes to it.
# /out is the UI's data directory on the host.
#
# situs_group_assignments_final is the last target in the grouping chain and is
# the only object that carries group_assign. targets stores it in the qs2 format
# on this image, so it is read with qs2::qs_read() and not readRDS().
#
# THE KEY IS FOUR COLUMNS. (county, situs_pID) alone is not unique on this frame:
# 2,133,448 rows sit on 2,116,562 distinct pairs, and ten of those pairs carry
# two different group_assign values because they carry two different owners at
# one address. group_assign is a function of the OWNER, not of the parcel, so the
# owner name has to be in the key or the join picks a group at random and
# duplicates rows besides. With owner_name in the key there are no conflicts.
#
# The key columns are written already normalised the way build-db.py normalises
# the roll (norm_pid: strip, drop leading zeros, "" -> "0"; norm_txt: upper-case
# and collapse whitespace) so the Python side does one dict lookup and no
# re-parsing. A normalisation drift between the two languages would show up as
# uncovered roll rows, which the build reports.
obj <- "/d/_targets/objects/situs_group_assignments_final"
out <- "/out/parcel_group_assign.csv"
tmp <- paste0(out, ".partial")

if (!file.exists(obj)) {
  stop("no saved situs_group_assignments_final object at ", obj)
}

norm_pid <- function(v) {
  v <- sub("^0+", "", trimws(as.character(v)))
  v[v == "" | is.na(v)] <- "0"
  v
}
norm_txt <- function(v) {
  v <- toupper(as.character(v))
  v[is.na(v)] <- ""
  trimws(gsub("[[:space:]]+", " ", v))
}

x <- qs2::qs_read(obj)
cat("read", nrow(x), "rows x", ncol(x), "cols\n")
stopifnot(all(c("county", "situs_pID", "situs_address",
                "owner_name", "group_assign") %in% names(x)))

d <- data.frame(
  k_county = norm_txt(x$county),
  k_pid    = norm_pid(x$situs_pID),
  k_addr   = norm_txt(x$situs_address),
  k_owner  = norm_txt(x$owner_name),
  group_assign = as.integer(x$group_assign),
  stringsAsFactors = FALSE
)
rm(x); invisible(gc())

cat("group_assign: zeros", sum(d$group_assign == 0L),
    "nonzero", sum(d$group_assign != 0L),
    "distinct labels", length(unique(d$group_assign)), "\n")

key <- paste(d$k_county, d$k_pid, d$k_addr, d$k_owner, sep = "\x1f")
cat("distinct 4-part keys:", length(unique(key)), "\n")

# A conflict is one key carrying two different labels. Counted before the
# de-duplication so the number is reported rather than hidden by it.
agg <- tapply(d$group_assign, key, function(g) length(unique(g)))
n_conflict <- sum(agg > 1L)
cat("keys with >1 distinct group_assign:", n_conflict, "\n")
if (n_conflict > 0L) {
  cat("conflicting keys (first 10):\n")
  print(utils::head(names(agg)[agg > 1L], 10))
}
rm(agg); invisible(gc())

# One row per key. Sorting the non-zero labels first means the kept row carries
# a real group whenever any row for the key does, which is the same tie-break
# build-db.py would apply and makes the choice independent of frame order.
ord <- order(key, d$group_assign == 0L)
d   <- d[ord, ]
key <- key[ord]
d   <- d[!duplicated(key), ]
rm(key, ord); invisible(gc())
cat("sidecar rows:", nrow(d), "\n")
cat("sidecar zeros:", sum(d$group_assign == 0L),
    "nonzero:", sum(d$group_assign != 0L), "\n")

if (requireNamespace("data.table", quietly = TRUE)) {
  data.table::fwrite(d, tmp, row.names = FALSE, na = "", quote = "auto")
} else {
  write.csv(d, tmp, row.names = FALSE, na = "")
}
if (!file.rename(tmp, out)) {
  stop("could not move ", tmp, " into place")
}
cat("wrote", out, file.info(out)$size, "bytes\n")
