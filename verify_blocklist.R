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

bl_raw <- blocklist_read()
bl     <- blocklist_strings(blocklist = bl_raw)

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
   raises(blocklist_strings(tmp)), msg_of(blocklist_strings(tmp)))

d <- bl_raw[bl_raw$target == 'address', , drop = FALSE]
utils::write.csv(d, tmp, row.names = FALSE)
ok('a target losing all its active rows raises',
   raises(blocklist_strings(tmp)), msg_of(blocklist_strings(tmp)))

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

cat(sprintf('\n==== %s: %d failing gate(s) ====\n',
            if (fails == 0L) 'ALL GATES PASS' else 'GATE FAILURE',
            fails))
quit(status = if (fails == 0L) 0L else 1L)
