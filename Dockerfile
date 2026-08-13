FROM rocker/r-ver:4.5.2
# Set the working directory for the project
RUN apt-get update && apt-get install -y libcurl4-openssl-dev libssl-dev libxml2-dev libproj-dev cmake gdal-bin libabsl-dev libgdal-dev libglpk-dev libpng-dev libx11-dev cloud-utils cloud-guest-utils pandoc && apt-get install -y python3.6 python3-pip python3-setuptools python3-dev python3.12-venv curl libudunits2-dev && rm -rf /var/lib/apt/lists/*

# Register TBB library
RUN ldconfig

ENV http_version=2
ENV CURL_HTTP_VERSION=CURL_HTTP_VERSION_1_1
ENV VIRTUAL_ENV=/opt/venv
RUN python3 -m venv $VIRTUAL_ENV
ENV PATH="$VIRTUAL_ENV/bin:$PATH"
ENV RENV_VERSION=f2c9163
ENV RENV_WATCHDOG_ENABLED=FALSE

# Install renv
RUN Rscript -e "install.packages('remotes', repos = c(CRAN = 'https://cloud.r-project.org'))"
RUN Rscript -e "remotes::install_github('rstudio/renv@${RENV_VERSION}')"

WORKDIR /landlord_mapper_etl

#copy renv.lock over to dir
COPY renv.lock renv.lock
COPY renv/activate.R renv/activate.R
COPY renv/settings.json renv/settings.json

#COPY _targets/ _targets/
# Restore R project library - force source package builds to ensure TBB compatibility
RUN R -s -e "renv::restore()"

# Rfast is a hard dependency -- _targets.R does library(Rfast) and lists it in
# tar_option_set(packages) -- but it is absent from renv.lock, so renv::restore()
# above does not install it. The images in use carry it because it was added to
# a running container out of band and never written back to the lockfile. That
# means an image built from this repo ALONE has always been broken, and fails at
# the first tar_make() with "there is no package called 'Rfast'". Found
# 2026-08-13 by rebuilding from a clean checkout; the rebuilt image differed from
# the working one by exactly this one package out of 179.
#
# Installed here rather than added to renv.lock so the fix is visible next to the
# reason. It resolves from the same pinned p3m snapshot as everything else (see
# the CRAN env var in the base image), so the version is reproducible.
RUN R -s -e "install.packages('Rfast'); if (!requireNamespace('Rfast', quietly = TRUE)) stop('Rfast failed to install')"

COPY requirements.txt .
RUN pip install -r requirements.txt

# Add local files and folders needed to generate the report
COPY *.xlsx          .
COPY *.R            .
COPY *.py            .
COPY AUSTIN*.zip            .
COPY *.txt            .
COPY link_used.csv            .
COPY blocklist.csv            .

# ---------------------------------------------------------------------------
# CODE SYNC STAGING. Read entrypoint.sh for the full reason. In short: a run
# binds a volume over /landlord_mapper_etl so the appraisal exports and the
# _targets store survive between runs, and that volume's copy of the code
# SHADOWS everything the COPY lines above put there. From the second run onward
# a rebuilt image executes stale sources with no warning. The entrypoint copies
# this staging directory over the volume on every start, so the image stays the
# single source of truth for code while the data is left alone.
#
# This whole mechanism lived ONLY inside the built images until 2026-08-13. The
# script and these lines were added to a container out of band and never
# committed, so a fresh build from this repo produced an image with no
# ENTRYPOINT at all, which silently ran whatever code the volume happened to
# hold. Discovered when tar_outdated() reported nothing stale after a change
# that certainly should have invalidated targets.
#
# ANYTHING THE PIPELINE READS AT RUNTIME BELONGS IN THIS LIST. blocklist.csv is
# here and was not in the images' copy: the loader resolves it against the
# working directory, i.e. the volume, so without this line every run would stop
# at "blocklist.csv not found" the moment the blocklist is consulted.
# ---------------------------------------------------------------------------
COPY _targets.R                              /opt/pipeline-src/
COPY final_output_helper_functions.R         /opt/pipeline-src/
COPY scrape_helper_functions.R               /opt/pipeline-src/
COPY supplementary_scrape_helper_functions.R /opt/pipeline-src/
COPY target_helper_functions.R               /opt/pipeline-src/
COPY TCAD_parse.py                           /opt/pipeline-src/
COPY blocklist.csv                           /opt/pipeline-src/
COPY ["HHI Data 2024 United States.xlsx", "/opt/pipeline-src/"]

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]

#EXPOSE 8080
CMD ["R", "-e", "targets::tar_make(callr_function = NULL, use_crew = FALSE, as_job = FALSE)"]

#
