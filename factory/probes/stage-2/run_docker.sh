#!/usr/bin/env bash
# Build the repository's Dockerfile and run the stage-2 probes (API + browser + the stage-1 suite) against containers.
#
#   factory/probes/stage-2/run_docker.sh [--offline] [--skip-build] [--no-ui] [--stage1-ref <git-ref>] [-- <run_all.py args>]
#
# Phase A: container with -e PORT=<custom>, --cpus 2 --memory 2g, host port mapping; all probes run against it
#          (the browser runs on the host and blocks/records any request leaving the service's origin).
# Phase B: second container, no PORT (must listen on 8080); target of the cross-instance import probe.
# --stage1-ref REF: builds the stage-1 service from `git archive REF` (read-only) and starts it; its URL is passed as
#          PROBE_STAGE1_BASE_URL so the stage-1 -> stage-2 export/import upgrade probe runs instead of skipping.
# --offline: also runs the API probes (no browser) from a python container on a docker network without outbound route.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../../.." && pwd)"
DOCKER="${DOCKER:-}"
if [ -z "$DOCKER" ]; then
  if [ -x /Users/ryugi62/.local/bin/docker ]; then DOCKER=/Users/ryugi62/.local/bin/docker; else DOCKER=docker; fi
fi
IMG="${IMAGE:-pocketful-stage2-probe}"
OFFLINE=0; BUILD=1; STAGE1_REF=""; EXTRA=(); NOUI=()
while [ $# -gt 0 ]; do
  case "$1" in
    --offline) OFFLINE=1 ;;
    --skip-build) BUILD=0 ;;
    --no-ui) NOUI=(--no-ui) ;;
    --stage1-ref) shift; STAGE1_REF="$1" ;;
    --) shift; EXTRA=("$@"); break ;;
  esac
  shift
done
PORT_A="${PORT_A:-9237}"; HP_A="${HP_A:-18237}"; HP_B="${HP_B:-18238}"; HP_S1="${HP_S1:-18239}"
SUFFIX="$$"
NAMES=(); NET="pf2-offline-$SUFFIX"; TMP=""
cleanup() {
  for n in ${NAMES[@]+"${NAMES[@]}"}; do "$DOCKER" rm -f "$n" >/dev/null 2>&1; done
  "$DOCKER" network rm "$NET" >/dev/null 2>&1
  [ -n "$TMP" ] && rm -rf "$TMP"
}
trap cleanup EXIT
fail=0

wait_healthy() {  # url seconds
  local url="$1" limit="$2" t0 now
  t0=$(python3 -c 'import time;print(time.time())')
  while :; do
    if curl -fsS -m 2 "$url/health" >/dev/null 2>&1; then
      now=$(python3 -c 'import time;print(time.time())'); python3 -c "print('%.1f' % ($now - $t0))"; return 0
    fi
    now=$(python3 -c 'import time;print(time.time())')
    if python3 -c "import sys;sys.exit(0 if $now - $t0 > $limit else 1)"; then return 1; fi
    sleep 0.3
  done
}

if [ "$BUILD" = 1 ]; then
  echo "== docker build ($REPO)"
  "$DOCKER" build -t "$IMG" "$REPO" || { echo "BUILD FAILED"; exit 1; }
fi

S1URL=""
if [ -n "$STAGE1_REF" ]; then
  TMP="$(mktemp -d)"
  echo "== stage-1 service from git ref $STAGE1_REF"
  git -C "$REPO" archive "$STAGE1_REF" | tar -x -C "$TMP" || { echo "cannot archive $STAGE1_REF"; exit 1; }
  "$DOCKER" build -t "${IMG}-stage1" "$TMP" || { echo "STAGE-1 BUILD FAILED"; exit 1; }
  "$DOCKER" run -d --name "pf2-s1-$SUFFIX" -p "127.0.0.1:$HP_S1:8080" "${IMG}-stage1" >/dev/null && NAMES+=("pf2-s1-$SUFFIX")
  if wait_healthy "http://127.0.0.1:$HP_S1" 60 >/dev/null; then S1URL="http://127.0.0.1:$HP_S1"; echo "stage-1 service up at $S1URL"; else echo "FAIL: stage-1 service not healthy"; fail=1; fi
fi

echo "== phase A: -e PORT=$PORT_A, 2 vCPU, 2 GiB, mapped to $HP_A"
"$DOCKER" run -d --name "pf2-a-$SUFFIX" --cpus 2 --memory 2g -e PORT="$PORT_A" -p "127.0.0.1:$HP_A:$PORT_A" "$IMG" >/dev/null || { echo "RUN FAILED"; exit 1; }
NAMES+=("pf2-a-$SUFFIX")
"$DOCKER" run -d --name "pf2-b-$SUFFIX" --cpus 2 --memory 2g -p "127.0.0.1:$HP_B:8080" "$IMG" >/dev/null || { echo "RUN FAILED (no PORT)"; exit 1; }
NAMES+=("pf2-b-$SUFFIX")
if el=$(wait_healthy "http://127.0.0.1:$HP_A" 60); then echo "phase A healthy after ${el}s (limit 60 s)"; else echo "FAIL: phase A not healthy within 60 s"; fail=1; "$DOCKER" logs "pf2-a-$SUFFIX" | tail -20; fi
if wait_healthy "http://127.0.0.1:$HP_B" 60 >/dev/null; then echo "phase B (default PORT 8080) healthy"; else echo "FAIL: no PORT -> not reachable on 8080"; fail=1; fi

echo "== probes against phase A"
PROBE_BASE_URL_2="http://127.0.0.1:$HP_B" PROBE_STAGE1_BASE_URL="$S1URL" \
  python3 "$HERE/run_all.py" "http://127.0.0.1:$HP_A" --no-wait ${NOUI[@]+"${NOUI[@]}"} ${EXTRA[@]+"${EXTRA[@]}"} || fail=1
"$DOCKER" stats --no-stream --format 'phase A: cpu={{.CPUPerc}} mem={{.MemUsage}}' "pf2-a-$SUFFIX" 2>/dev/null

if [ "$OFFLINE" = 1 ]; then
  echo "== offline phase: docker network --internal (no outbound route), API probes only"
  "$DOCKER" network create --internal "$NET" >/dev/null || { echo "cannot create internal network"; fail=1; }
  "$DOCKER" run -d --name "pf2-c-$SUFFIX" --network "$NET" --network-alias svc --cpus 2 --memory 2g -e PORT=8080 "$IMG" >/dev/null || { echo "RUN FAILED (offline)"; exit 1; }
  NAMES+=("pf2-c-$SUFFIX")
  PY_IMG="${PROBE_PY_IMAGE:-python:3.12-slim}"
  if "$DOCKER" run --rm --network "$NET" "$PY_IMG" python -c "import urllib.request;urllib.request.urlopen('http://1.1.1.1',timeout=4)" >/dev/null 2>&1; then
    echo "WARNING: network is not actually offline"; fail=1
  else echo "outbound blocked as intended"; fi
  "$DOCKER" run --rm --network "$NET" -v "$REPO/factory/probes:/probes:ro" -v "$REPO:/repo:ro" -e PROBE_REPO=/repo "$PY_IMG" \
      python /probes/stage-2/run_all.py http://svc:8080 --no-ui ${EXTRA[@]+"${EXTRA[@]}"} || fail=1
fi

if [ "$fail" = 0 ]; then echo "ALL DOCKER-LEVEL PROBES PASSED"; else echo "SOME PROBES FAILED"; fi
exit "$fail"
