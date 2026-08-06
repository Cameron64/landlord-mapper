# Export the pipeline's whole-region parcel frame out of the lm_work volume.
#
# /d is the lm_work docker volume, mounted read-only. Nothing here writes to it.
# /out is the UI's data directory on the host.
#
# austin_parcel_data_merged is the rbind of every county roll (the local Travis
# roll, pacs_data, wcad_data_parsed, hays_data) and is the exact frame
# owner_scrape_actual() filters. The CSV the pipeline writes to disk under the
# same name is a Travis-only side effect, which is why this exists.
#
# targets saves the object in its "qs" storage format, which on this image means
# the qs2 package, so it is read with qs2::qs_read() and not readRDS().
#
# row.names = FALSE on purpose: the older austin_parcel_data_merged.csv came
# from write.csv() with row names, so its column 0 is an unnamed row-number
# column and a positional read of it is off by one. This file has no such
# column, and the UI reads both by header name regardless.

obj <- "/d/_targets/objects/austin_parcel_data_merged"
out <- "/out/parcel_roll_5county.csv"
tmp <- paste0(out, ".partial")

if (!file.exists(obj)) {
  stop("no saved austin_parcel_data_merged object at ", obj)
}

x <- qs2::qs_read(obj)
cat("read", nrow(x), "rows x", ncol(x), "cols\n")
print(table(x$county, useNA = "ifany"))

# Write to a temporary name and rename, so a refresh interrupted halfway can
# never leave the UI a truncated roll to load.
if (requireNamespace("data.table", quietly = TRUE)) {
  data.table::fwrite(x, tmp, row.names = FALSE, na = "NA", quote = "auto")
} else {
  write.csv(x, tmp, row.names = FALSE, na = "NA")
}
if (!file.rename(tmp, out)) {
  stop("could not move ", tmp, " into place")
}
cat("wrote", out, file.info(out)$size, "bytes\n")
