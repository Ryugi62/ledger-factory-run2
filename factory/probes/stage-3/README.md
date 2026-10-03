# Stage 3 probes (statements, historical balances, payment corrections)

One command runs stage 3 and, by default, the stage-2 suite (API + headless browser) and the whole stage-1 suite against one running service:

    python3 factory/probes/stage-3/run_all.py http://127.0.0.1:8080

Container level (build the Dockerfile of `stage-3/`, `-e PORT`, 2 vCPU / 2 GiB, second container for the cross-instance import probe,
optional stage-1 / stage-2 services built from git refs for the upgrade probe, optional no-outbound network):

    factory/probes/stage-3/run_docker.sh [--offline] [--skip-build] [--no-ui] [--stage1-ref <git-ref>] [--stage2-ref <git-ref>]

Flags of `run_all.py`: `--no-ui` (skip the stage-2 browser probes), `--no-stage1`, `--no-stage2` (only the stage-3 probes: `--no-stage1 --no-stage2`),
`-k <substring>`, `--ids S3-001,...`, `--list`, `--check-ledger`, `--strict` (SKIP = failure), `--screenshots DIR`.

Environment: `PROBE_BASE_URL_2` (second instance), `PROBE_STAGE1_BASE_URL` / `PROBE_STAGE2_BASE_URL` (running stage-1 / stage-2 services; enable the
export/import upgrade probe `r_upgrade3.test_exports_of_the_earlier_services_are_accepted`, SKIP otherwise).

Stage-3 files: `r_*.py` API probes (standard library only), `lib3.py` (helpers and the independent bitemporal `Model`), `ledger_rows.txt` +
`build_ledger.py` (regenerates `factory/ledger/stage-3.md`). Run one process at a time per service: every probe resets it.

Time: the bitemporal grid probe issues ~2000 small requests (about 10-40 s); hold/expiry probes wait a few seconds for 3 s authorizations.
