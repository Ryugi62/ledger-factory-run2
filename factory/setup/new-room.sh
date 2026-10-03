#!/bin/bash
# Create a fresh room owned by the coordinator with every seat and the human; print its id.
# usage: OWNER=<your band handle> ./new-room.sh      then: band room send <id> "<task>" --mention <coordinator participant id>
set -uo pipefail
: "${OWNER:?your Band handle, e.g. alice}"
R=$(band chat new --as "$OWNER/coordinator" 2>&1 | grep -oE '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}' | head -1)
for h in "$OWNER/implementer" "$OWNER/analyst" "$OWNER/reviewer" "$OWNER"; do
  band chat add --as "$OWNER/coordinator" "$R" "$h" >/dev/null 2>&1   # Band 0.4.12 prints a spurious decode error; the add succeeds
done
echo "$R"; band room participants "$R"
