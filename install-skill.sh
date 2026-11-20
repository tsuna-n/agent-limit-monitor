#!/usr/bin/env bash
set -euo pipefail

alm_source=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)

install_skill() (
    alm_parent=$1
    alm_destination="$alm_parent/agent-limit-monitor"
    mkdir -p -- "$alm_parent"
    alm_stage=$(mktemp -d -- "$alm_parent/.agent-limit-monitor.XXXXXXXX")
    trap 'rm -rf -- "$alm_stage"' EXIT
    tar -C "$alm_source" --exclude=__pycache__ --exclude='*.pyc' -cf - \
        SKILL.md README.md config.yaml agent_limit_monitor.py install-skill.sh \
        agent_adapters handoff quota_monitor terminal_ui tests |
        tar -C "$alm_stage" -xf -

    if [[ -e "$alm_destination" || -L "$alm_destination" ]]; then
        if [[ -d "$alm_destination" ]] && diff -qr -x __pycache__ -x '*.pyc' -- "$alm_stage" "$alm_destination" >/dev/null; then
            printf 'Already installed at %s\n' "$alm_destination"
            exit 0
        fi
        printf 'Existing skill was left in place at %s\n' "$alm_destination" >&2
        exit 1
    fi

    mv -T --no-clobber -- "$alm_stage" "$alm_destination"
    if [[ -d "$alm_stage" ]]; then
        printf 'Existing skill was left in place at %s\n' "$alm_destination" >&2
        exit 1
    fi
    printf 'Installed at %s\n' "$alm_destination"
)

case "${1-}" in
    "")
        install_skill "$HOME/.agents/skills"
        install_skill "$HOME/.gemini/antigravity-cli/skills"
        ;;
    --project-opencode)
        install_skill "$alm_source/.opencode/skills"
        ;;
    *)
        printf 'Usage: %s [--project-opencode]\n' "$0" >&2
        exit 2
        ;;
esac
