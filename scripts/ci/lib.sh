#!/bin/sh
# Shared helpers for the CI scripts. Source this; do not execute it.
#
# POSIX sh only. These run in the buildkit image too, which is Alpine/busybox —
# no bash, no arrays, no [[ ]].

log() { printf '▸ %s\n' "$*" >&2; }
die() { printf '✗ %s\n' "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

# Point TLS at a bundle of the system store plus the Smile internal CA.
#
# No-op when SMILE_CA_PEM is unset or empty. That is a normal state, not an
# error: the variable is defined per project and is not exposed on unprotected
# refs, so plenty of pipelines legitimately run without it. Falling back to the
# system trust store means an untrusted registry fails later with a TLS error
# that names the problem — instead of `cat ''` killing the job before any work.
#
# Idempotent: rebuilds the bundle from scratch, sets CI_CA_BUNDLE when active.
setup_ca() {
    CI_CA_BUNDLE=""
    if [ -z "${SMILE_CA_PEM:-}" ]; then
        log "SMILE_CA_PEM unset — using the system trust store"
        return 0
    fi
    if [ ! -s "${SMILE_CA_PEM}" ]; then
        log "SMILE_CA_PEM is set but empty or missing — using the system store"
        return 0
    fi

    _bundle="${CI_PROJECT_DIR:-$PWD}/.certs/ca-certificates.crt"
    _system="/etc/ssl/certs/ca-certificates.crt"
    mkdir -p "$(dirname "${_bundle}")"
    # Same defensiveness as above: not every image ships a system bundle, and
    # `cat` on a missing one would kill the job over a file we only wanted to
    # append to.
    if [ -r "${_system}" ]; then
        cat "${_system}" "${SMILE_CA_PEM}" >"${_bundle}"
    else
        log "no system CA bundle at ${_system} — using SMILE_CA_PEM alone"
        cat "${SMILE_CA_PEM}" >"${_bundle}"
    fi

    CI_CA_BUNDLE="${_bundle}"
    SSL_CERT_FILE="${_bundle}"
    CURL_CA_BUNDLE="${_bundle}"
    export CI_CA_BUNDLE SSL_CERT_FILE CURL_CA_BUNDLE
    log "CA bundle ready: ${_bundle}"
}
