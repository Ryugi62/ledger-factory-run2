#!/usr/bin/env bash
# Build the repository's Dockerfile and run the stage-1 probes against real containers.
#
#   factory/probes/stage-1/run_docker.sh [--offline] [--skip-build] [-- <run_all.py args>]
#
# Phase A: container with -e PORT=<custom>, --cpus 2 --memory 2g, host port mapping.
#          Measures start -> first healthy response (limit 60 s) and runs every probe.
# Phase B: second container with no PORT (must listen on 8080); it is also the
#          "other instance" for the cross-instance import probe (PROBE_BASE_URL_2).
# --offline: additionally runs the service on a docker network with no outbound route and
#          drives the probes from a sibling python container on that network.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../../.." && pwd)"
DOCKER="${DOCKER:-}"
if [ -z "$DOCKER" ]; then
  if [ -x /Users/ryugi62/.local/bin/docker ]; then DOCKER=/Users/ryugi62/.local/bin/docker; else DOCKER=docker; fi
fi
IMG="${IMAGE:-pocketful-stage1-probe}"
OFFLINE=0; BUILD=1; EXTRA=()
while [ $# -gt 0 ]; do
  case "$1" in
    --offline) OFFLINE=1 ;;
    --skip-build) BUILD=0 ;;
    --) shift; EXTRA=("$@"); break ;;
  esac
  shift
done
PORT_A="${PORT_A:-9137}"
HP_A="${HP_A:-18137}"
HP_B="${HP_B:-18138}"
SUFFIX="$$"
NAMES=()
NET="pf-offline-$SUFFIX"
cleanup() {
  for n in ${NAMES[@]+"${NAMES[@]}"}; do "$DOCKER" rm -f "$n" >/dev/null 2>&1; done
  "$DOCKER" network rm "$NET" >/dev/null 2>&1
}
trap cleanup EXIT
fail=0

wait_healthy() {  # url seconds -> prints elapsed, returns 0/1
  local url="$1" limit="$2" t0 now
  t0=$(python3 -c 'import time;print(time.time())')
  while :; do
    if curl -fsS -m 2 "$url/health" >/dev/null 2>&1; then
      now=$(python3 -c 'import time;print(time.time())')
      python3 -c "print('%.1f' % ($now - $t0))"
      return 0
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

echo "== phase A: -e PORT=$PORT_A, 2 vCPU, 2 GiB, mapped to $HP_A"
"$DOCKER" run -d --name "pf-a-$SUFFIX" --cpus 2 --memory 2g -e PORT="$PORT_A" -p "127.0.0.1:$HP_A:$PORT_A" "$IMG" >/dev/null || { echo "RUN FAILED"; exit 1; }
NAMES+=("pf-a-$SUFFIX")
T0=$(python3 -c 'import time;print(time.time())')
echo "== phase B: default port (8080), mapped to $HP_B"
"$DOCKER" run -d --name "pf-b-$SUFFIX" --cpus 2 --memory 2g -p "127.0.0.1:$HP_B:8080" "$IMG" >/dev/null || { echo "RUN FAILED (no PORT)"; exit 1; }
NAMES+=("pf-b-$SUFFIX")

if el=$(wait_healthy "http://127.0.0.1:$HP_A" 60); then echo "phase A healthy after ${el}s since mapping (limit 60 s)  [S1-015]"; else echo "FAIL: phase A not healthy within 60 s"; fail=1; "$DOCKER" logs "pf-a-$SUFFIX" | tail -20; fi
if el=$(wait_healthy "http://127.0.0.1:$HP_B" 60); then echo "phase B (default PORT=8080) healthy [S1-022]"; else echo "FAIL: container without PORT is not reachable on 8080"; fail=1; "$DOCKER" logs "pf-b-$SUFFIX" | tail -20; fi

echo "== probes against phase A (PROBE_BASE_URL_2 = phase B)"
PROBE_BASE_URL_2="http://127.0.0.1:$HP_B" python3 "$HERE/run_all.py" "http://127.0.0.1:$HP_A" --no-wait ${EXTRA[@]+"${EXTRA[@]}"} || fail=1
"$DOCKER" stats --no-stream --format 'phase A: cpu={{.CPUPerc}} mem={{.MemUsage}}' "pf-a-$SUFFIX" 2>/dev/null

if [ "$OFFLINE" = 1 ]; then
  echo "== offline phase: docker network --internal (no outbound route)"
  "$DOCKER" network create --internal "$NET" >/dev/null || { echo "cannot create internal network"; fail=1; }
  "$DOCKER" run -d --name "pf-c-$SUFFIX" --network "$NET" --network-alias svc --cpus 2 --memory 2g -e PORT=8080 "$IMG" >/dev/null || { echo "RUN FAILED (offline)"; exit 1; }
  NAMES+=("pf-c-$SUFFIX")
  PY_IMG="${PROBE_PY_IMAGE:-python:3.12-slim}"
  echo "-- sanity: the probe container has no outbound access"
  if "$DOCKER" run --rm --network "$NET" "$PY_IMG" python -c "import urllib.request;urllib.request.urlopen('http://1.1.1.1',timeout=4)" >/dev/null 2>&1; then
    echo "WARNING: network is not actually offline; offline result is not meaningful"; fail=1
  else
    echo "outbound blocked as intended"
  fi
  "$DOCKER" run --rm --network "$NET" -v "$HERE:/probes:ro" -v "$REPO:/repo:ro" -e PROBE_REPO=/repo "$PY_IMG" \
      python /probes/run_all.py http://svc:8080 ${EXTRA[@]+"${EXTRA[@]}"} || fail=1
fi

if [ "$fail" = 0 ]; then echo "ALL DOCKER-LEVEL PROBES PASSED"; else echo "SOME PROBES FAILED"; fi
exit "$fail"
