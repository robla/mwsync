# Bash completion for mwsync.py.
#
# Source this file from ~/.bashrc or an interactive shell:
#   source /path/to/mwsync/extras/mwsync.bash

_mwsync_article_words()
{
    local config="mwsync.yaml"
    [[ -r "$config" ]] || return 0

    awk '
        /^[[:space:]]{4}[^[:space:]#][^:]*:[[:space:]]*$/ {
            key=$1
            sub(/:$/, "", key)
            if (key != "api_base" && key != "articles") {
                print key
            }
        }
        /^[[:space:]]{6}local:[[:space:]]*/ {
            local=$0
            sub(/^[[:space:]]*local:[[:space:]]*/, "", local)
            gsub(/^["'\'']|["'\'']$/, "", local)
            print local
        }
    ' "$config"
}

_mwsync_complete()
{
    local cur prev subcommand
    local commands global_opts article_commands
    COMPREPLY=()

    cur="${COMP_WORDS[COMP_CWORD]}"
    prev="${COMP_WORDS[COMP_CWORD-1]}"
    commands="init add checkout fetch push diff difftool merge log show fsck migrate status"
    global_opts="-h --help --config"
    article_commands="fetch push difftool merge log fsck status"

    if [[ "$COMP_CWORD" -eq 1 ]]; then
        COMPREPLY=( $(compgen -W "$commands $global_opts" -- "$cur") )
        return 0
    fi

    if [[ "$prev" == "--config" ]]; then
        COMPREPLY=( $(compgen -f -- "$cur") )
        return 0
    fi

    subcommand=""
    local word
    for word in "${COMP_WORDS[@]:1}"; do
        if [[ "$word" != -* ]]; then
            subcommand="$word"
            break
        fi
    done

    case "$subcommand" in
        checkout)
            if [[ "$cur" == -* ]]; then
                COMPREPLY=( $(compgen -W "--depth --to -h --help" -- "$cur") )
            else
                COMPREPLY=( $(compgen -W "$(_mwsync_article_words)" -- "$cur") )
            fi
            ;;
        fetch)
            if [[ "$cur" == -* ]]; then
                COMPREPLY=( $(compgen -W "--dry-run --depth --all-known --with-bodies -h --help" -- "$cur") )
            else
                COMPREPLY=( $(compgen -W "$(_mwsync_article_words)" -- "$cur") )
            fi
            ;;
        push)
            if [[ "$cur" == -* ]]; then
                COMPREPLY=( $(compgen -W "--dry-run --new -m --message -h --help" -- "$cur") )
            else
                COMPREPLY=( $(compgen -W "$(_mwsync_article_words)" -- "$cur") )
            fi
            ;;
        diff)
            if [[ "$cur" == -* ]]; then
                COMPREPLY=( $(compgen -W "--remote -h --help" -- "$cur") )
            else
                COMPREPLY=( $(compgen -W "$(_mwsync_article_words)" -- "$cur") )
            fi
            ;;
        show)
            if [[ "$cur" == -* ]]; then
                COMPREPLY=( $(compgen -W "-h --help" -- "$cur") )
            else
                COMPREPLY=( $(compgen -W "$(_mwsync_article_words)" -- "$cur") )
            fi
            ;;
        add)
            COMPREPLY=( $(compgen -W "-h --help" -- "$cur") )
            ;;
        init)
            COMPREPLY=( $(compgen -W "-h --help" -- "$cur") )
            ;;
        migrate)
            if [[ "$cur" == -* ]]; then
                COMPREPLY=( $(compgen -W "--dry-run --yes -h --help" -- "$cur") )
            else
                COMPREPLY=( $(compgen -W "$(_mwsync_article_words)" -- "$cur") )
            fi
            ;;
        difftool|merge|log|fsck|status)
            if [[ "$cur" == -* ]]; then
                COMPREPLY=( $(compgen -W "-h --help" -- "$cur") )
            else
                COMPREPLY=( $(compgen -W "$(_mwsync_article_words)" -- "$cur") )
            fi
            ;;
        *)
            COMPREPLY=( $(compgen -W "$commands $global_opts" -- "$cur") )
            ;;
    esac
}

complete -F _mwsync_complete mwsync.py
