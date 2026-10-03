# Stage 4 probes (refunds and batch corrections)

One command runs stage 4 and, by default, the stage-3, stage-2 (API + headless browser) and stage-1 suites against one running service:

    python3 factory/probes/stage-4/run_all.py http://127.0.0.1:8080

Container level (build the Dockerfile of `stage-4/`, `-e PORT`, 2 vCPU / 2 GiB, second container for the cross-instance import probe,
optional stage-1/2/3 services built from git refs for the upgrade probe, optional no-outbound network):

    factory/probes/stage-4/run_docker.sh [--offline] [--skip-build] [--no-ui] [--stage1-ref REF] [--stage2-ref REF] [--stage3-ref REF]

Flags of `run_all.py`: `--no-ui`, `--no-stage1`, `--no-stage2`, `--no-stage3` (only stage 4: `--no-stage1 --no-stage2 --no-stage3`), `-k <substring>`,
`--ids S4-001,...`, `--list`, `--check-ledger`, `--strict` (SKIP = failure), `--screenshots DIR`.

Environment: `PROBE_BASE_URL_2` (second instance), `PROBE_STAGE1_BASE_URL` / `PROBE_STAGE2_BASE_URL` / `PROBE_STAGE3_BASE_URL` (running services of the earlier
stages; enable the export/import upgrade probes, SKIP otherwise).

Files: `s_refunds.py`, `s_batches.py`, `s_concurrent4.py`, `s_upgrade4.py` (API probes, standard library only), `lib4.py` (helpers on top of `../stage-3/lib3.py`, which
contains the independent bitemporal `Model`), `ledger_rows.txt` + `build_ledger.py` (regenerates `factory/ledger/stage-4.md`). Run one process at a time per
service: every probe resets it.
