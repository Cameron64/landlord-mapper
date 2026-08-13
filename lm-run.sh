#!/bin/sh
# Launch the landlord-mapper pipeline the way it has to be launched on this box.
#
# Three flags are load-bearing, and are the reason this is a file rather than a
# remembered docker command:
#
#   -m 120g            Bound the container instead of the box. When a worker pool
#                      blows up, the cgroup kills the pipeline and the kernel
#                      never has to pick a victim. The port-8099 UI survived two
#                      such kills on 2026-08-04 with this in place; it did not
#                      survive the 05:39 kill that happened without it.
#
#   --shm-size 32g     mori::share() puts the 1.4 GB owner frame in /dev/shm.
#                      Without this the run fails immediately with
#                      "mori: cannot create region (requested 1.4 GB)".
#
#   -v ...lmtmp:/tmp   Keep R tempdir OFF the root filesystem. multidplyr and
#                      future both hand their worker payload to callr, which
#                      writes the serialised owner frame to tempdir once per
#                      worker, 935 MB a time. On the container overlay that ran
#                      the 263 GB root disk to 0 bytes free and killed the run,
#                      with ENOSPC surfacing as the very misleading
#                      "Error in tar_make(): error writing to connection".
#                      Bounded by FINAL_OUTPUT_WORKERS it is about 21 GB per
#                      stage, which this disk has room for.
#
# Leftover Rtmp* directories from killed runs are root-owned; reclaim them with
#   sudo rm -rf /media/cam/ImageProcessing/lmtmp/Rtmp<name>
# after confirming no container is using them.
#
# Do NOT pass --entrypoint. entrypoint.sh syncs code from /opt/pipeline-src on
# every start, and overriding it silently runs whatever stale code the named
# lm_work volume happens to hold. Rebuild the image with /tmp/rebuild.sh after
# editing any R source, or the container will not see the change.
set -e
TMPDIR_HOST=/media/cam/ImageProcessing/lmtmp
mkdir -p "$TMPDIR_HOST"
docker rm -f lm-pipeline >/dev/null 2>&1 || true
docker run -d --name lm-pipeline \
  -m 120g --shm-size 32g \
  -v lm_work:/landlord_mapper_etl \
  -v "$TMPDIR_HOST":/tmp \
  landlord_mapper_box:latest \
  R --max-connections=512 -q -e "targets::tar_make(callr_function = NULL)"
echo "started; watch with: docker logs -f lm-pipeline"
