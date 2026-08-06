# landlord-mapper

Repo for organizing the landlord mapper project.

The project answers a question a tenant or an organizer actually walks in with:
who owns my building, and what else do they own? It joins Texas county
appraisal rolls to the Texas Comptroller's franchise-tax registry, folds an
entity's name variants into one owner, and reports the result as portfolios
rather than as parcels.

WIP front end visualizing collated data is here: https://ontheseams.shinyapps.io/landlord_mapper_app/

Request access to the following GDrive for further project materials: https://drive.google.com/drive/folders/1e2Ahq9sNNQ2K_Q-RuTrdkWzDL6FH_gAa?usp=sharing

## How the data flows

The R pipeline is a `targets` chain. In outline:

1. **Parse each county's appraisal roll.** Travis is parsed locally
   (`TCAD_parse.py`); the others come from their own parsers and are `rbind`ed
   into one frame, `austin_parcel_data_merged`. That frame currently carries 13
   counties across the Austin-San Antonio corridor (Travis, Bexar, Williamson,
   Hays, Comal, Guadalupe, Bastrop, Caldwell, Atascosa, Bandera, Kendall,
   Medina, Wilson), so any file or variable named `5county` is a legacy name and
   not a count.
2. **Ask the registry who each owner is.** `owner_scrape_actual()` in
   `scrape_helper_functions.R` looks up one row per distinct
   `(owner_name, owner_address)` and records `matched`, `no_record`, or
   `not_resolved`. Results accumulate in `owner_data_total.csv`, and the scrape
   **resumes** rather than restarting: it drops every parcel already recorded in
   that file, so a rerun only asks about what is new.
3. **Fold name variants into one owner.** The `situs_group_assignments*` targets
   build the owner grouping, without which the top of the units ranking is
   fragments of the same landlord competing with itself.
4. **Publish.** `final_output_helper_functions.R` counts the figures, and
   `web/build-db.py` builds the SQLite database the web front end serves.

## The scope rule, and why it is worth reading first

Every published figure is counted against a filtered population, not against the
whole roll. That population is defined once, in
`final_output_helper_functions.R`:

```r
dplyr::filter(owner_data,
              ((is_financialized == TRUE) & (is_owner_occupied == FALSE)) |
                (property_units > 4),
              property_units != 0)
```

`property_units > 4` means **5 units and over**, which is the size the project
means by a rental building. `property_units != 0` drops a parcel with no floor
area on the roll, because units are themselves an estimate from floor area.
`dplyr::filter` keeps a row only when the whole condition is `TRUE`, so an
unreadable value anywhere in it drops the row rather than defaulting it in.

**The rule is encoded in four places, in two languages, and they have to agree:**

| Where | What it gates |
| --- | --- |
| `scrape_helper_functions.R:2185` | Which parcels' owners get asked about at the registry. |
| `final_output_helper_functions.R` (7 filters) | The published R figures. |
| `web/build-db.py` (`units > 4`) | The `in_scope` flag on every row of the SQLite build, and so every figure the web front end shows. |
| `web/lm/scope.py` (`parcel_in_scope`) | The per-parcel explanation the site shows for why a parcel is in or out. `web/lm/filters.py` and `web/lm/pages_health.py` carry the same predicate in comment and SQL form. |

When these disagree the failure is quiet and specific: the scrape asks about a
different population than the figures are counted over, so some owners sit in
scope with no registry answer at all and the "no Texas registration" share is
computed over a denominator the scrape never covered. Change one, change all
four, and rerun the pipeline rather than only rebuilding the database.

Two shares are worth keeping distinct when quoting a registration figure:
`no_record / owners_in_scope` (the headline, which counts unanswered owners
against the denominator) and `no_record / (matched + no_record)` (answered owners
only). They converge only as the unanswered residue shrinks.

## Layout

| Path | What it is |
| --- | --- |
| `_targets.R` | The pipeline definition. Every target and its dependencies. |
| `scrape_helper_functions.R` | The owner-registry scrape: worker pool, resume logic, retry passes, and the scope filter at line 2185. |
| `final_output_helper_functions.R` | The published figures, and the canonical scope filter. |
| `target_helper_functions.R`, `supplementary_scrape_helper_functions.R` | Roll parsing and supplementary lookups. |
| `TCAD_parse.py` | The Travis appraisal-roll parser. Not standalone despite being Python: `_targets.R` sources it with `reticulate::source_python()` and eight parse targets dispatch into its `TCAD_parseYear_*` functions. |
| `shinyApp/app.R` | The Shiny front end, deployed at the shinyapps.io link above. |
| `web/` | A second front end: a stdlib-only Python server over a read-only SQLite build of the pipeline's output, deployed on Railway. Added because it holds the whole dataset on a small box and answers a page in well under a second; see `web/README.md`. It does not replace `shinyApp/`, and neither one reads the other's code. |
| `pipeline-monitor/` | A read-only local page, API, and MCP server showing what the pipeline is doing right now: current target, elapsed and estimated time, whether the scrape is mid-flight. Reads the `targets` metadata and the container log; never writes. See `pipeline-monitor/README.md`. |
| `renv.lock`, `Dockerfile` | Reproducibility for the R pipeline. |
| `Appraisal Export Layout - 8.0.30.xlsx`, `pac_cols.txt` | Column layouts for the appraisal-roll exports. |
| `HHI Data 2024 United States.xlsx` | Household-income reference data. |

## Running the R pipeline

The pipeline runs under `targets`, so the entry point is a single call and
everything already up to date is skipped:

```r
targets::tar_make()
```

`Dockerfile` builds an image whose `CMD` is exactly that, meaning a plain
`docker run` of the image **is a full pipeline run**. Override the command with
something inert if you only want to look inside the image.

Two things to know before building:

- **`docker build .` does not work from a fresh clone.** The `Dockerfile` copies
  `renv/activate.R`, `renv/settings.json`, `requirements.txt`, `AUSTIN*.zip`, and
  `*.json`. None of those are in the repo, and the first three are not
  gitignored either, so they are simply absent and the build fails at the first
  `COPY`. You need at least the renv bootstrap files and a Python requirements
  file alongside the source before the image will build. The roll archive is the
  softer case: the `tcad_data_get` target downloads the Travis roll itself, so
  the `COPY AUSTIN*.zip` line is there to bake in a copy already on hand rather
  than because the pipeline cannot fetch one.
- **The scrape is the long pole and it resumes.** A rerun after a scope or code
  change re-asks only about parcels missing from `owner_data_total.csv`. Confirm
  that from the log line the scrape prints before it starts working:

  ```
  [owner_scrape] <N> parcels -> <M> distinct owners, <W> workers
  ```

  If `M` is in the thousands the resume worked. If it is near the full owner
  count, the resume did not, and the run is a full re-scrape rather than a
  top-up.

## Data sources

- County appraisal rolls for the 13 counties listed above, which supply parcels,
  owner names and addresses, floor area, and the unit estimate derived from it.
- Deed records, folded into the parcel frame by `deed_summ_data` and so part of
  the core join, not an overlay. They are where an owner's first and last
  purchase dates come from.
- Texas Comptroller franchise-tax registry, which supplies corporate
  registration, registered agent, and officers for an owner that is an entity.
- CDC Social Vulnerability Index (`svi_data`) and household-income data
  (`hhi_data`), consumed only by the supplementary output stage as a demographic
  overlay. Neither one affects who owns what.
