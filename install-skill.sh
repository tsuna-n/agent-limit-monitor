#!/usr/bin/env bash
set -euo pipefail

alm_source=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
alm_update=false
if [[ ${1-} == --update ]]; then
    alm_update=true
    shift
fi

install_skill() (
    alm_parent=$1
    alm_name=$2
    alm_package=$3
    shift 3
    alm_destination="$alm_parent/$alm_name"
    mkdir -p -- "$alm_parent"
    exec {alm_install_fd}<"$alm_parent"
    flock -x "$alm_install_fd"
    alm_stage=$(mktemp -d -- "$alm_parent/.$alm_name.XXXXXXXX")
    alm_backup=
    cleanup_skill() {
        if [[ -n "$alm_backup" && -d "$alm_backup" && -d "$alm_stage" ]]; then
            if ! mv -T --no-clobber -- "$alm_backup" "$alm_destination" || [[ -d "$alm_backup" ]]; then
                printf 'Previous skill remains in %s\n' "$alm_backup" >&2
            fi
        fi
        rm -rf -- "$alm_stage"
    }
    trap cleanup_skill EXIT
    tar -C "$alm_package" --exclude=__pycache__ --exclude='*.pyc' -cf - "$@" |
        tar -C "$alm_stage" -xf -

    if [[ -e "$alm_destination" || -L "$alm_destination" ]]; then
        alm_config="$alm_destination/config.yaml"
        if $alm_update && [[ "$alm_name" == agent-limit-monitor && ! -L "$alm_destination" ]] &&
            [[ -e "$alm_config" || -L "$alm_config" ]]; then
            if [[ ! -f "$alm_config" ]]; then
                printf 'Cannot preserve config at %s\n' "$alm_config" >&2
                exit 1
            fi
            cp -pL -- "$alm_config" "$alm_stage/config.yaml"
        fi
        if [[ -d "$alm_destination" ]] && diff -qr -x __pycache__ -x '*.pyc' -- "$alm_stage" "$alm_destination" >/dev/null; then
            printf 'Already installed at %s\n' "$alm_destination"
            exit 0
        fi
        if ! $alm_update || [[ ! -d "$alm_destination" || -L "$alm_destination" ]]; then
            printf 'Existing skill was left in place at %s\n' "$alm_destination" >&2
            exit 1
        fi
        alm_backup=$(mktemp -d -- "$(dirname -- "$alm_parent")/.agent-limit-monitor-backup.XXXXXXXX")/$alm_name
        mv -T -- "$alm_destination" "$alm_backup"
    fi

    mv -T --no-clobber -- "$alm_stage" "$alm_destination"
    if [[ -d "$alm_stage" ]]; then
        printf 'Existing skill was left in place at %s\n' "$alm_destination" >&2
        exit 1
    fi
    if [[ -n "$alm_backup" ]]; then
        printf 'Backup: %s\n' "$alm_backup"
    fi
    printf 'Installed at %s\n' "$alm_destination"
)

install_monitor() {
    install_skill "$1" agent-limit-monitor "$alm_source" \
        SKILL.md README.md config.yaml agent_limit_monitor.py install-skill.sh \
        agent_adapters agy handoff quota_monitor terminal_ui tests
    install_skill "$1" agent-limit-moritor "$alm_source/agy/agent-limit-moritor" SKILL.md
}

install_auto() (
    alm_monitor="$alm_source/agent_limit_monitor.py"
    alm_auto_dir="$HOME/.local/share/agent-limit-monitor"
    alm_bin="$alm_auto_dir/bin"
    alm_bashrc="$HOME/.bashrc"
    alm_path_line='case ${PATH-} in "$HOME/.local/share/agent-limit-monitor/bin"|"$HOME/.local/share/agent-limit-monitor/bin:"*) ;; *) export PATH="$HOME/.local/share/agent-limit-monitor/bin${PATH:+:$PATH}" ;; esac # agent-limit-monitor'

    if [[ ! -f "$alm_monitor" || ! -x "$alm_monitor" ]]; then
        printf 'Monitor must be executable at %s\n' "$alm_monitor" >&2
        exit 1
    fi
    if [[ "$HOME" != /* || "$HOME" == *:* ]]; then
        printf 'Automatic monitoring requires an absolute home directory without a colon\n' >&2
        exit 1
    fi
    if [[ -L "$alm_auto_dir" || -L "$alm_bin" ]]; then
        printf 'Automatic monitoring requires a dedicated directory at %s\n' "$alm_bin" >&2
        exit 1
    fi
    if [[ -e "$alm_bashrc" || -L "$alm_bashrc" ]] && [[ ! -f "$alm_bashrc" ]]; then
        printf 'Bash startup file must be a regular file at %s\n' "$alm_bashrc" >&2
        exit 1
    fi

    exec {alm_bashrc_fd}>>"$alm_bashrc"
    flock -x "$alm_bashrc_fd"
    bash -n "$alm_bashrc"
    if grep -Fxq -- "$alm_path_line" "$alm_bashrc"; then
        alm_path_line=
    else
        bash -n <(cat -- "$alm_bashrc"; printf '\n\n%s\n' "$alm_path_line")
    fi

    for alm_command in codex opencode agy agent-limit-monitor; do
        alm_link="$alm_bin/$alm_command"
        if [[ -e "$alm_link" || -L "$alm_link" ]] &&
            [[ ! -L "$alm_link" || "$(readlink -- "$alm_link")" != "$alm_monitor" ]]; then
            printf 'Existing command was left in place at %s\n' "$alm_link" >&2
            exit 1
        fi
    done

    mkdir -p -- "$alm_bin"
    for alm_command in codex opencode agy agent-limit-monitor; do
        alm_link="$alm_bin/$alm_command"
        if [[ ! -L "$alm_link" ]]; then
            ln -sT -- "$alm_monitor" "$alm_link"
        fi
    done

    if [[ -n "$alm_path_line" ]]; then
        printf '\n\n%s\n' "$alm_path_line" >&"$alm_bashrc_fd"
    fi
    printf 'Installed agent-limit-monitor and the Codex, OpenCode, and AGY launchers. Start a new Bash shell.\n'
)

usage() {
    printf 'Usage: %s [--update] [--project-opencode | --project-claude]\n       %s --auto\n' "$0" "$0" >&2
    exit 2
}

if (( $# > 1 )); then
    usage
fi
case "${1-}" in
    "")
        install_monitor "$HOME/.agents/skills"
        install_monitor "$HOME/.gemini/antigravity-cli/skills"
        install_monitor "$HOME/.claude/skills"
        ;;
    --project-opencode)
        install_monitor "$alm_source/.opencode/skills"
        ;;
    --project-claude)
        install_monitor "$alm_source/.claude/skills"
        ;;
    --auto)
        if $alm_update; then
            usage
        fi
        install_auto
        ;;
    *)
        usage
        ;;
esac
