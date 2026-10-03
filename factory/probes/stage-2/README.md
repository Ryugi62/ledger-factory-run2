# Stage 2 probes (API + headless browser)

One command runs stage 2 and, by default, the whole stage-1 suite against one running service:

    python3 factory/probes/stage-2/run_all.py http://127.0.0.1:8080

Container level (build the Dockerfile, `-e PORT`, 2 vCPU / 2 GiB, second container for cross-instance import,
optional stage-1 service from a git ref for the upgrade probe, optional no-outbound network):

    factory/probes/stage-2/run_docker.sh [--offline] [--skip-build] [--stage1-ref <git-ref>]

Flags: `--no-ui` (skip the browser probes), `--no-stage1`, `-k <substring>`, `--ids S2-001,...`, `--list`,
`--check-ledger`, `--strict` (SKIP = failure), `--screenshots DIR` (saves 375/768/1280 px screenshots for a human or
model reviewer of the visual requirements).

Requirements on the machine that runs the probes: Python 3.8+, and for the browser probes `pip install playwright`
plus `playwright install chromium` (the probes SKIP, loudly, when that is missing). The service under test needs nothing.

Environment: `PROBE_BASE_URL_2` (second instance), `PROBE_STAGE1_BASE_URL` (running stage-1 service; enables the
stage-1 -> stage-2 export/import upgrade probe), `PROBE_SCREENSHOTS`.

Files: `q_*.py` API probes, `u_*.py` browser probes, `lib2.py` / `ui_lib.py` helpers, `ledger_rows.txt` +
`build_ledger.py` (regenerates `factory/ledger/stage-2.md`). Run one process at a time per service: every probe resets it.
