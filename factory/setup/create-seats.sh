#!/bin/bash
# Create the four factory seats as Band-owned headless Claude Code runtimes.
# usage: WORKSPACE=/abs/workspace REPO=/abs/result-repo ./create-seats.sh [seat]
set -euo pipefail
: "${WORKSPACE:?absolute path the seats work in}" "${REPO:?absolute path of the result repository}"
M="$REPO/mandates"
FLAGS=(--claude-context-mode local_config --claude-permission-mode bypassPermissions
  --claude-strict-mcp-config)          # only Band's own MCP server reaches the seat
mk() { # session/name, description, model
  band agent create --session "$1" --name "$1" --description "$2" \
    --transport claude-code-cli --runtime-model "$3" "${FLAGS[@]}" \
    --cwd "$WORKSPACE" --instructions-file "$M/$1.md" --json
}
want=${1:-all}
[[ $want == all || $want == coordinator ]] && mk coordinator "Factory coordinator: plans, routes and records the run; never writes product code." claude-sonnet-5-5
[[ $want == all || $want == analyst ]]     && mk analyst "Factory analyst: turns a specification into a requirement ledger and black-box probes." claude-sonnet-5-5
[[ $want == all || $want == implementer ]] && mk implementer "Factory implementer: builds the product to the specification, one commit per work item." claude-opus-5-5
[[ $want == all || $want == reviewer ]]    && mk reviewer "Factory reviewer: verifies each revision independently and accepts or rejects it." claude-opus-5-5
