#!/bin/bash
# enter-env.sh — sshd ForceCommand that routes an interactive SSH session into
# the env container's namespaces with the env container's REAL environment.
#
# This access container shares the env container's PID namespace
# (--pid container:<env>), so /proc/1/environ inside this container IS the env
# container's own environment (the `docker run` of `sleep infinity`). We read
# it, drop the vars sshd/nsenter would clobber (PWD, SHLVL, _, OLDPWD, SSH_*),
# sanitize/backfill HOME and PATH, then nsenter (setuid-root) into the env
# container's namespaces and exec a login shell in the project dir (/workspace,
# falling back to /).
#
# The result behaves like `docker exec -it <env> /bin/bash`: root shell, real
# PATH (conda/python), real HOME, and the image's ENV — with zero per-image
# hardcoding (works for pytorch/pytorch, python:*-slim, derived training
# images, and future base images).
#
# NOTE: the environment is passed EXPLICITLY through `sh -c 'export ...'`,
# NOT via nsenter's --preserve-environment (which would keep the *access*
# container's env — the wrong one).
set -eu

# The env container's PID 1 environ is NUL-delimited and root-owned; the
# sandbox SSH user cannot read it directly. nsenter is setuid root (see
# access-entrypoint.sh), so read through it to escalate.
TMP_ENVIRON="$(mktemp)"
trap 'rm -f "$TMP_ENVIRON"' EXIT

if [ -r /proc/1/environ ]; then
    cat /proc/1/environ > "$TMP_ENVIRON" 2>/dev/null || true
else
    nsenter -t 1 -p -- cat /proc/1/environ > "$TMP_ENVIRON" 2>/dev/null || true
fi

# Collect name=value pairs, dropping hostile/transient vars.
env1_lines=()
while IFS= read -r -d '' line; do
    [ -n "$line" ] || continue
    name="${line%%=*}"
    case "$name" in
        'PWD' | 'OLDPWD' | 'SHLVL' | '_' | '') continue ;;
        SSH_*) continue ;;
    esac
    env1_lines+=("$line")
done < "$TMP_ENVIRON"

# Extract HOME/PATH/TERM so they can be sanitized/backfilled.
HOME_ENV=''
PATH_ENV=''
for line in ${env1_lines[@]+"${env1_lines[@]}"}; do
    case "$line" in
        HOME=*) HOME_ENV="${line#HOME=}" ;;
        PATH=*) PATH_ENV="${line#PATH=}" ;;
    esac
done
[ -n "$HOME_ENV" ] || HOME_ENV=/root
[ -n "$PATH_ENV" ] || PATH_ENV=/opt/conda/bin:/usr/local/bin:/usr/local/sbin:/usr/sbin:/usr/bin:/sbin:/bin

# TERM: prefer the env container's; else carry the sshd session's; else xterm.
TERM_ENV="${TERM:-}"
for line in ${env1_lines[@]+"${env1_lines[@]}"}; do
    case "$line" in
        TERM=*) TERM_ENV="${line#TERM=}" ;;
    esac
done
[ -n "$TERM_ENV" ] || TERM_ENV=xterm

# Single-quote a value so it survives the inner shell's parse verbatim. Built
# char-by-char (rather than a pattern substitution) so the escaping is
# identical on every bash version.
shell_quote() {
    local s="$1" out="'"
    local i len
    len=${#s}
    i=0
    while [ "$i" -lt "$len" ]; do
        case "${s:$i:1}" in
            "'") out="${out}'\\''" ;;
            *) out="${out}${s:$i:1}" ;;
        esac
        i=$((i + 1))
    done
    out="${out}'"
    printf '%s' "$out"
}

# Build the export list. PATH/HOME/TERM go first (they always exist); the
# remaining env container vars follow, capped at MAX_BYTES so the execve()
# argument list never approaches ARG_MAX / E2BIG (each kept var appears once
# as `export name=value;` in the inner shell string).
MAX_BYTES=64000
exports=(
    "export PATH=$(shell_quote "$PATH_ENV")"
    "export HOME=$(shell_quote "$HOME_ENV")"
    "export TERM=$(shell_quote "$TERM_ENV")"
)
total=0
for line in ${env1_lines[@]+"${env1_lines[@]}"}; do
    name="${line%%=*}"
    case "$name" in
        'HOME' | 'PATH' | 'TERM') continue ;;
    esac
    export_cmd="export $(shell_quote "$name")=$(shell_quote "${line#*=}")"
    total=$((total + ${#export_cmd}))
    [ "$total" -le "$MAX_BYTES" ] || break
    exports+=("$export_cmd")
done

# Assemble the inner shell command: export the reconstructed env, land in the
# project dir, and exec a login shell (bash -> zsh -> sh fallback for
# Alpine/other bases). cd runs *after* nsenter (inside the env container's
# mount namespace), where /workspace actually exists.
script=''
for e in "${exports[@]}"; do
    script="${script}${e}; "
done
script="${script}cd /workspace 2>/dev/null || cd /; "
script="${script}if command -v bash >/dev/null 2>&1; then exec bash -l; "
script="${script}elif command -v zsh >/dev/null 2>&1; then exec zsh -l; "
script="${script}else exec sh; fi"

# Enter all the env container's namespaces (mount, UTS, IPC, net, PID) as root
# via the setuid nsenter and run the reconstructed login shell.
exec nsenter -t 1 -m -u -i -n -p -- /bin/sh -c "$script"