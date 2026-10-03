# Stage 1 probes (black-box, HTTP only)

Run everything against a running service:

    python3 factory/probes/stage-1/run_all.py http://127.0.0.1:8080

Container level (build the Dockerfile, `-e PORT`, 2 vCPU / 2 GiB, 60 s start limit, two containers for the
cross-instance import probe, optional no-outbound network):

    factory/probes/stage-1/run_docker.sh [--offline] [--skip-build]

Useful flags: `--list`, `-k <substring>`, `--ids S1-105,S1-133`, `--check-ledger`, `--strict`.
`PROBE_BASE_URL_2=<url of a second instance>` enables the cross-instance import probe (SKIP otherwise).
`python3 factory/probes/stage-1/build_ledger.py` regenerates `factory/ledger/stage-1.md` from `ledger_rows.txt`.

Probes run ★-rows first. Standard library only, Python 3.8+. Each probe resets the service to its own fixture.
