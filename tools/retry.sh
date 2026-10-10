#!/usr/bin/env bash
# Retry wrapper for transient CI infrastructure failures.
#
# Mirrors are allowed to blip: an apt mirror 503, a PyPI timeout, a docker pull
# reset, a stale Arch keyring.  None of those say anything about the change
# under test, so retry them instead of failing the run.
#
#   . tools/retry.sh
#   retry -a 3 -d 5 -l "apt-get update" -- sudo apt-get update
#
#   bash tools/retry.sh -a 3 -d 1 -- false
#
# A command that only passes on a later attempt emits a ::notice:: and a
# step-summary line, so an absorbed flake stays visible rather than silently
# turning green.
#
# `RETRY_DISABLED=1` at the workflow level disables every retry at once -- it
# overrides even an explicit -a, since every call site passes one.  That is the
# escape hatch for debugging a suspected retry-masked failure.

# NOTE: deliberately does not set -e/-u/-o pipefail at the top level.  This file
# is sourced by scripts and by workflow `run:` blocks that own their own shell
# options, and a library must not reconfigure its caller.  Every expansion below
# is ${VAR:-default}-guarded so the function is safe under `set -u`.

_retry_log() { printf '[retry] %s\n' "$*" >&2; }

# Annotations go to stdout: the runner parses workflow commands from both
# streams, but stdout is the reliable one.  Titles must not contain commas --
# GitHub splits the property list on commas, so "a, b" would be parsed as two
# properties and the title truncated.
_retry_succeeded_late() {
    local label="$1" attempt="$2" attempts="$3"
    printf '::notice title=Transient failure absorbed::%s passed on attempt %d/%d\n' \
        "${label}" "${attempt}" "${attempts}"
    if [ -n "${GITHUB_STEP_SUMMARY:-}" ] && [ -w "${GITHUB_STEP_SUMMARY}" ]; then
        printf -- '- :warning: **Transient failure absorbed** - `%s` needed %d/%d attempts\n' \
            "${label}" "${attempt}" "${attempts}" >>"${GITHUB_STEP_SUMMARY}"
    fi
}

retry() {
    local attempts="${RETRY_ATTEMPTS:-3}" delay="${RETRY_DELAY:-5}"
    local max_delay="${RETRY_MAX_DELAY:-120}" jitter="" label="${RETRY_LABEL:-}"

    while [ "$#" -gt 0 ]; do
        case "$1" in
            -a|--attempts)  attempts="${2:?--attempts needs a value}"; shift 2 ;;
            -d|--delay)     delay="${2:?--delay needs a value}"; shift 2 ;;
            -m|--max-delay) max_delay="${2:?--max-delay needs a value}"; shift 2 ;;
            -j|--jitter)    jitter="${2:?--jitter needs a value}"; shift 2 ;;
            -l|--label)     label="${2:?--label needs a value}"; shift 2 ;;
            -h|--help)
                _retry_log "usage: retry [-a N] [-d S] [-m S] [-j S] [-l LABEL] -- CMD [ARGS...]"
                return 0
                ;;
            --) shift; break ;;
            *)
                # Refuse to guess where options end: `retry -a 3 ls -l` would
                # otherwise swallow the -l.
                _retry_log "expected -- before the command (got: $1)"
                return 2
                ;;
        esac
    done

    if [ "$#" -eq 0 ]; then
        _retry_log "no command given"
        return 2
    fi

    # Hard kill switch.  Deliberately overrides an explicit -a/--attempts,
    # because every call site in this repo passes -a explicitly -- so
    # RETRY_ATTEMPTS alone would not disable anything.  Setting
    # RETRY_DISABLED=1 at the workflow level reproduces pre-retry behaviour
    # everywhere at once, which is what you want when chasing a failure that
    # retries may be masking.
    if [ -n "${RETRY_DISABLED:-}" ]; then
        _retry_log "RETRY_DISABLED is set; running '$1' once, without retries"
        attempts=1
    fi

    if [ -z "${label}" ]; then label="$1"; fi
    if [ -z "${jitter}" ]; then jitter="${delay}"; fi

    local attempt=1 rc=0 backoff=0 pause=0
    while :; do
        rc=0
        # `cmd || rc=$?` rather than if/else: the failing command sits on the
        # left of ||, so errexit cannot abort the loop mid-retry, and we keep
        # the command's exact exit status to hand back to the caller.
        "$@" || rc=$?

        if [ "${rc}" -eq 0 ]; then
            if [ "${attempt}" -gt 1 ]; then
                _retry_succeeded_late "${label}" "${attempt}" "${attempts}"
            fi
            return 0
        fi

        # 126/127 mean "not executable" / "not found" -- deterministic, so
        # retrying just burns the whole budget three times over.
        if [ "${rc}" -eq 126 ] || [ "${rc}" -eq 127 ]; then
            _retry_log "${label}: exit ${rc} is not retryable"
            return "${rc}"
        fi

        if [ "${attempt}" -ge "${attempts}" ]; then
            _retry_log "${label}: failed after ${attempt}/${attempts} attempts (exit ${rc})"
            return "${rc}"
        fi

        backoff=$(( delay * (1 << (attempt - 1)) ))
        if [ "${backoff}" -gt "${max_delay}" ]; then backoff="${max_delay}"; fi
        pause="${backoff}"
        # Jitter is not cosmetic here: test-deb-install and test-rpm-install fan
        # out to ten legs that hit the same mirrors in lockstep, and without it
        # they all retry on the same second and collide again.
        if [ "${jitter}" -gt 0 ]; then pause=$(( backoff + RANDOM % (jitter + 1) )); fi

        printf '::warning title=Transient failure retrying::%s exited %d on attempt %d/%d, retrying in %ds\n' \
            "${label}" "${rc}" "${attempt}" "${attempts}" "${pause}"
        _retry_log "${label}: attempt ${attempt}/${attempts} failed (exit ${rc}); sleeping ${pause}s"
        sleep "${pause}"
        attempt=$(( attempt + 1 ))
    done
}

# Runnable directly as well as sourceable, so the helper is testable without
# writing a wrapper around it.
if [ "${BASH_SOURCE[0]}" = "${0}" ]; then
    set -euo pipefail
    retry "$@"
fi
