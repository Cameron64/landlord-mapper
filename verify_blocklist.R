#!/usr/bin/env Rscript
# Verification gates for the blocklist.csv wiring. Read-only: touches no store.
#   Rscript bw-blocklist-verify.R <src_dir>
# <src_dir> must hold final_output_helper_functions.R and blocklist.csv.

args    <- commandArgs(trailingOnly = TRUE)
src_dir <- if (length(args)) args[[1]] else '.'
setwd(src_dir)

source('final_output_helper_functions.R')

fails <- 0L
ok <- function(label, passed, detail = '') {
  cat(sprintf('[%s] %s%s\n',
              if (passed) 'PASS' else 'FAIL',
              label,
              if (nzchar(detail)) paste0('  -- ', detail) else ''))
  if (!passed) fails <<- fails + 1L
}

# --------------------------------------------------------------------------
# The literals exactly as they stood in reg_agent_string_gen at ba6a238.
# --------------------------------------------------------------------------
old_misc_name_string <- list(paste(c('D3 REAL ESTATE CONSULTANTS',
                                     'GILL, DENSON & COMPANY',
                                     'L L CASEY & CO',
                                     '^US$',
                                     'KE ANDREWS',
                                     'COMMERCIAL',
                                     'UNAVAILABLE',
                                     'FBO',
                                     'EQUITY TRUST COMPANY',
                                     'TAX EXEMPT',
                                     'NONE',
                                     '00000',
                                     'UNKNOWN',
                                     'OWNER',
                                     'ADDRESS',
                                     'CUSTODIAN',
                                     'UNKNOWN CITY',
                                     'UNKNOWN STATE',
                                     'ZIP',
                                     'PROPERTY TAX DEPARTMENT',
                                     'ATTN',
                                     'AVAILABLE UPON REQUEST',
                                     'MICHEL ROGERS & MALONEY, PC'),
                                   collapse = '|'))

old_misc_add_string <- list(paste(c('815 BRAZOS.+AUSTIN TX 78701',
                                    '2595 DALLAS PKWY.+FRISCO TX 75034',
                                    '401 TOM LANDRY HWY.+DALLAS TX 75266',
                                    'PO BOX 4090 SCOTTSDALE AZ 85261',
                                    'PO BOX 592226 SAN ANTONIO TX 78259',
                                    '901.+MOPAC.+AUSTIN TX 78746',
                                    '901.+MO PAC.+AUSTIN TX 78746',
                                    '3225 MCLEOD DR.+LAS VEGAS NV 89121',
                                    '17350 STATE H.+HOUSTON TX 77064'),
                                  collapse = '|'))

bl_raw  <- blocklist_read()
# 'cell' is the mode that must reproduce the old literals byte for byte: the
# literals were only ever applied to individual cells.
bl      <- blocklist_strings(blocklist = bl_raw, consumer = 'cell')
bl_blob <- blocklist_strings(blocklist = bl_raw, consumer = 'blob')

cat('\n== file composition ==\n')
print(table(bl_raw$status))
print(table(bl_raw$category, bl_raw$status))
print(table(bl_raw$target, bl_raw$action))
cat('rows:', nrow(bl_raw), '\n')

cat('\n== GATE 1: the 32 misc_* rows reproduce the literals byte for byte ==\n')
new_misc_name <- bl$names[['misc_name']]
new_misc_add  <- bl$addresses[['misc_address']]

ok('misc_name alternation identical to old literal',
   identical(new_misc_name, old_misc_name_string[[1]]),
   sprintf('nchar old=%d new=%d',
           nchar(old_misc_name_string[[1]]), nchar(new_misc_name)))
ok('misc_address alternation identical to old literal',
   identical(new_misc_add, old_misc_add_string[[1]]),
   sprintf('nchar old=%d new=%d',
           nchar(old_misc_add_string[[1]]), nchar(new_misc_add)))
ok('misc row count is 32',
   sum(bl_raw$category %in% c('misc_name', 'misc_address')) == 32L,
   sprintf('%d rows',
           sum(bl_raw$category %in% c('misc_name', 'misc_address'))))

# Behavioural equivalence on real and adversarial inputs.
probe_names <- c('D3 REAL ESTATE CONSULTANTS LLC',
                 'GILL, DENSON & COMPANY TAX AGENTS',
                 'US',
                 'THE US BANK NA',
                 'KE ANDREWS ATTN PROPERTY TAX DEPARTMENT',
                 'UNKNOWN CITY UNKNOWN STATE ZIP 00000',
                 'EQUITY TRUST COMPANY CUSTODIAN FBO JANE DOE',
                 'MICHEL ROGERS & MALONEY, PC',
                 'AVAILABLE UPON REQUEST',
                 'SMITH FAMILY TRUST',
                 'TAX EXEMPT NONE',
                 'JOHN OWNER ADDRESS UNAVAILABLE',
                 '',
                 'COMMERCIAL PROPERTIES OF TEXAS LTD',
                 'L L CASEY & CO INC')
probe_adds <- c('815 BRAZOS ST STE 200 AUSTIN TX 78701',
                '2595 DALLAS PKWY STE 300 FRISCO TX 75034',
                '401 TOM LANDRY HWY DALLAS TX 75266',
                'PO BOX 4090 SCOTTSDALE AZ 85261',
                'PO BOX 592226 SAN ANTONIO TX 78259',
                '901 S MOPAC EXPY BLDG 4 AUSTIN TX 78746',
                '901 S MO PAC EXPY BLDG 4 AUSTIN TX 78746',
                '3225 MCLEOD DR STE 100 LAS VEGAS NV 89121',
                '17350 STATE HWY 249 STE 220 HOUSTON TX 77064',
                '1600 BARTON SPRINGS RD AUSTIN TX 78704',
                '',
                'UNKNOWN US',
                '   UNKNOWN ADDRESS   ',
                '815 BRAZOS AND LATER AUSTIN TX 78701 AUSTIN TX 78701')

ok('agent_string_sub(names) identical old vs new on probe vector',
   identical(agent_string_sub(probe_names, old_misc_name_string),
             agent_string_sub(probe_names, list(new_misc_name))))
ok('agent_string_sub(addresses) identical old vs new on probe vector',
   identical(agent_string_sub(probe_adds, old_misc_add_string),
             agent_string_sub(probe_adds, list(new_misc_add))))

# Same test against real column values if a sample dump is present.
sample_file <- Sys.getenv('LM_BLOCKLIST_SAMPLE', unset = '')
if (nzchar(sample_file) && file.exists(sample_file)) {
  smp <- readRDS(sample_file)
  ok(sprintf('agent_string_sub(names) identical on %d real name values',
             length(smp$names)),
     identical(agent_string_sub(smp$names, old_misc_name_string),
               agent_string_sub(smp$names, list(new_misc_name))))
  ok(sprintf('agent_string_sub(addresses) identical on %d real address values',
             length(smp$addresses)),
     identical(agent_string_sub(smp$addresses, old_misc_add_string),
               agent_string_sub(smp$addresses, list(new_misc_add))))
} else {
  cat('[SKIP] real-value comparison (set LM_BLOCKLIST_SAMPLE to an .rds)\n')
}

cat('\n== GATE 2: the 24 city_only rows are inert ==\n')
city_rows <- bl_raw[bl_raw$category == 'city_only', , drop = FALSE]
ok('24 city_only rows present in the file', nrow(city_rows) == 24L,
   sprintf('%d rows', nrow(city_rows)))
ok('every city_only row has status review',
   all(city_rows$status == 'review'),
   paste(unique(city_rows$status), collapse = ','))
all_returned <- unlist(c(bl$addresses, bl$names), use.names = FALSE)
ok('no city_only category is returned by blocklist_strings',
   !('city_only' %in% c(names(bl$addresses), names(bl$names))),
   paste(c(names(bl$addresses), names(bl$names)), collapse = ','))
ok('no city_only pattern appears anywhere in the returned strings',
   !any(vapply(city_rows$pattern,
               function(p) any(grepl(p, all_returned, fixed = TRUE)),
               logical(1))))
# And prove they would have matched something had they been active, so the
# inertness is a filter and not an accident of the patterns being dead.
ok('city_only patterns are live regexes (would match if activated)',
   all(vapply(city_rows$pattern,
              function(p){
                probe <- gsub('\\[\\[:space:\\]\\]\\*', '',
                              gsub('\\[\\[:space:\\]\\]\\+', ' ',
                                   gsub('\\^|\\$', '', p)))
                grepl(p, probe)
              },
              logical(1))))
ok('active row count is 158',
   sum(bl_raw$status == 'active') == 158L,
   sprintf('%d', sum(bl_raw$status == 'active')))
ok('review row count is 24',
   sum(bl_raw$status == 'review') == 24L,
   sprintf('%d', sum(bl_raw$status == 'review')))

cat('\n== GATE 3: malformed / absent CSV raises ==\n')
raises <- function(expr) inherits(tryCatch(expr, error = function(e) e), 'error')
msg_of <- function(expr) {
  e <- tryCatch(expr, error = function(e) e)
  if (inherits(e, 'error')) conditionMessage(e) else '<no error>'
}

tmp <- tempfile(fileext = '.csv')

ok('absent file raises',
   raises(blocklist_read(file.path(tempdir(), 'definitely-not-here.csv'))),
   msg_of(blocklist_read(file.path(tempdir(), 'definitely-not-here.csv'))))

d <- bl_raw; d$status <- NULL
utils::write.csv(d, tmp, row.names = FALSE)
ok('missing required column raises', raises(blocklist_read(tmp)),
   msg_of(blocklist_read(tmp)))

d <- bl_raw[0, , drop = FALSE]
utils::write.csv(d, tmp, row.names = FALSE)
ok('header-only file raises', raises(blocklist_read(tmp)),
   msg_of(blocklist_read(tmp)))

d <- bl_raw
d$pattern[which(d$action == 'blank_value')[1]] <-
  sub('\\$$', '', bl_raw$pattern[which(bl_raw$action == 'blank_value')[1]])
utils::write.csv(d, tmp, row.names = FALSE)
ok('unanchored blank_value row raises', raises(blocklist_read(tmp)),
   msg_of(blocklist_read(tmp)))

d <- bl_raw; d$status[[1]] <- 'enabled'
utils::write.csv(d, tmp, row.names = FALSE)
ok('unknown status raises', raises(blocklist_read(tmp)),
   msg_of(blocklist_read(tmp)))

d <- bl_raw; d$target[[1]] <- 'owner'
utils::write.csv(d, tmp, row.names = FALSE)
ok('unknown target raises', raises(blocklist_read(tmp)),
   msg_of(blocklist_read(tmp)))

d <- bl_raw; d$action[[1]] <- 'delete'
utils::write.csv(d, tmp, row.names = FALSE)
ok('unknown action raises', raises(blocklist_read(tmp)),
   msg_of(blocklist_read(tmp)))

d <- bl_raw; d$pattern[[1]] <- '[unclosed'
utils::write.csv(d, tmp, row.names = FALSE)
ok('uncompilable regex raises', raises(blocklist_read(tmp)),
   msg_of(blocklist_read(tmp)))

d <- bl_raw; d$pattern[[1]] <- ''
utils::write.csv(d, tmp, row.names = FALSE)
ok('empty pattern raises', raises(blocklist_read(tmp)),
   msg_of(blocklist_read(tmp)))

d <- bl_raw; d$status <- 'review'
utils::write.csv(d, tmp, row.names = FALSE)
ok('zero active rows raises (no silent empty blocklist)',
   raises(blocklist_strings(tmp, consumer = 'cell')),
   msg_of(blocklist_strings(tmp, consumer = 'cell')))

d <- bl_raw[bl_raw$target == 'address', , drop = FALSE]
utils::write.csv(d, tmp, row.names = FALSE)
ok('a target losing all its active rows raises',
   raises(blocklist_strings(tmp, consumer = 'cell')),
   msg_of(blocklist_strings(tmp, consumer = 'cell')))

d <- bl_raw
d$pattern[which(d$action == 'blank_value')[1]] <-
  paste0('^A|B$')
utils::write.csv(d, tmp, row.names = FALSE)
ok('piped blank_value row raises', raises(blocklist_read(tmp)),
   msg_of(blocklist_read(tmp)))

ok('LM_BLOCKLIST_PATH pointing at nothing raises rather than falling back',
   {
     Sys.setenv(LM_BLOCKLIST_PATH = file.path(tempdir(), 'nope.csv'))
     r <- raises(blocklist_read())
     Sys.unsetenv('LM_BLOCKLIST_PATH')
     r
   })
unlink(tmp)

cat('\n== GATE 4: what reg_agent_string_gen now returns ==\n')
cat('address categories:', paste(names(bl$addresses), collapse = ', '), '\n')
cat('name categories   :', paste(names(bl$names), collapse = ', '), '\n')
cat('address element nchar:',
    paste(names(bl$addresses), vapply(bl$addresses, nchar, integer(1)),
          sep = '=', collapse = ' '), '\n')
cat('name element nchar   :',
    paste(names(bl$names), vapply(bl$names, nchar, integer(1)),
          sep = '=', collapse = ' '), '\n')
cat('misc_address is the FIRST address element (position preserved):',
    identical(names(bl$addresses)[[1]], 'misc_address'), '\n')

cat('\n== GATE 5: the cell/blob consumer split ==\n')
# The defect this gate exists for: a greedy `.*ADDR.*` hub_address pattern is
# correct on one address cell (it blanks the cell) and catastrophic on the
# concatenated per-situs blob (it blanks the parcel's entire name evidence).

ok('blocklist_strings with no consumer raises',
   raises(blocklist_strings(blocklist = bl_raw)),
   msg_of(blocklist_strings(blocklist = bl_raw)))
ok('blocklist_strings with an unknown consumer raises',
   raises(blocklist_strings(blocklist = bl_raw, consumer = 'string')),
   msg_of(blocklist_strings(blocklist = bl_raw, consumer = 'string')))
ok('reg_agent_string_gen with no consumer raises',
   raises(reg_agent_string_gen(data.frame(), 1)))

hub_rows  <- bl_raw[bl_raw$category == 'hub_address', , drop = FALSE]
hub_cell  <- bl$addresses[['hub_address']]
hub_blob  <- bl_blob$addresses[['hub_address']]

ok('108 hub_address rows present', nrow(hub_rows) == 108L,
   sprintf('%d rows', nrow(hub_rows)))
ok('every hub_address row is written with the greedy wrapper on both ends',
   all(startsWith(hub_rows$pattern, '.*') & endsWith(hub_rows$pattern, '.*')))
ok('cell mode keeps the greedy wrappers',
   identical(hub_cell, paste(hub_rows$pattern, collapse = '|')))
ok('blob mode strips them, per row, not just at the ends',
   identical(hub_blob,
             paste(substr(hub_rows$pattern, 3, nchar(hub_rows$pattern) - 2),
                   collapse = '|')))
ok('blob mode leaves no bare .* alternation branch',
   !any(grepl('(^|\\|)\\.\\*', hub_blob)))

# Categories that were never wrapped must be byte identical in both modes --
# blob mode is not allowed to disturb the legacy literals.
for (cat_used in c('misc_address', 'null_value')) {
  ok(sprintf('%s identical in cell and blob mode', cat_used),
     identical(bl$addresses[[cat_used]], bl_blob$addresses[[cat_used]]))
}
ok('misc_name identical in cell and blob mode',
   identical(bl$names[['misc_name']], bl_blob$names[['misc_name']]))

# The behavioural statement, on a blob shaped like the real ones: owner name,
# then a hub address, then a corp name.
probe_blob <- paste('SMITH FAMILY TRUST',
                    '1600 BARTON SPRINGS RD STE 300 AUSTIN TX 78704',
                    'ACME HOLDINGS LLC')
blob_cellmode <- agent_string_sub(probe_blob, bl$addresses)
blob_blobmode <- agent_string_sub(probe_blob, bl_blob$addresses)

ok('cell-mode patterns DO empty a blob (this is the defect, reproduced)',
   !nzchar(trimws(blob_cellmode)),
   sprintf('-> "%s"', blob_cellmode))
ok('blob-mode patterns do NOT empty it',
   nzchar(trimws(blob_blobmode)),
   sprintf('-> "%s"', trimws(blob_blobmode)))
ok('blob mode still removes the hub address itself',
   !grepl('BARTON SPRINGS', blob_blobmode))
ok('blob mode preserves both owner names either side of it',
   grepl('SMITH FAMILY TRUST', blob_blobmode) &&
     grepl('ACME HOLDINGS LLC', blob_blobmode))

# And the same pattern set still blanks a single CELL, which is what the
# hub_address rows are for in the first place.
probe_cell <- '1600 BARTON SPRINGS RD STE 300 AUSTIN TX 78704'
ok('cell mode still blanks a hub address cell outright',
   !nzchar(trimws(agent_string_sub(probe_cell, bl$addresses))))

ok('blocklist_unwrap_greedy raises on a wrappers-only pattern',
   raises(blocklist_unwrap_greedy('.*.*')),
   msg_of(blocklist_unwrap_greedy('.*.*')))
ok('blocklist_unwrap_greedy leaves an unwrapped pattern alone',
   identical(blocklist_unwrap_greedy('PO BOX 4090 SCOTTSDALE AZ 85261'),
             'PO BOX 4090 SCOTTSDALE AZ 85261'))

cat(sprintf('\n==== %s: %d failing gate(s) ====\n',
            if (fails == 0L) 'ALL GATES PASS' else 'GATE FAILURE',
            fails))
quit(status = if (fails == 0L) 0L else 1L)
