#!/bin/sh
# Quality gate for CI: install what the hardened image lacks, then run `task ci`.
#
# The gate itself stays defined in Taskfile.yaml — this script only prepares the
# environment, so there is one definition of "the checks", not two.
#
# Every step is guarded, so re-running is free.
set -eu
. "$(dirname "$0")/lib.sh"

UV_VERSION="${UV_VERSION:-0.9.6}"
TASK_VERSION="${TASK_VERSION:-3.52.0}"

# pyright downloads its own prebuilt node, which links against libatomic.so.1.
# Hardened images strip it, and the failure surfaces as a loader error during
# the typecheck step — nothing to do with types.
if ls /usr/lib/*/libatomic.so.1 >/dev/null 2>&1; then
    log "libatomic present"
else
    log "installing libatomic1 (needed by pyright's bundled node)"
    apt-get update -qq
    apt-get install -y -qq --no-install-recommends libatomic1
fi

# No curl or wget in the hardened Python image, so the upstream task installer
# is not an option; PyPI ships the same binary.
have uv || pip install --no-cache-dir --root-user-action=ignore "uv==${UV_VERSION}"
have task || pip install --no-cache-dir --root-user-action=ignore "go-task-bin==${TASK_VERSION}"

log "uv $(uv --version) | task $(task --version)"

# `ci`, not `test`: the gate must fail on unformatted code, not rewrite it.
exec task ci -- "${GATE_PATHS:-src/}"
