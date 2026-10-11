#!/usr/bin/env bash
# Pulls new commits for the panel and the bot and restarts whichever needs it.
# Run every 10 minutes by oyb-update.timer; does nothing when there's nothing new.
# Only ever fast-forwards, and puts the old version back if a service won't start.
set -uo pipefail

SYSTEMCTL="${SYSTEMCTL:-systemctl}"
LOG="${OYB_UPDATE_LOG:-/var/log/oyb-update.log}"
WAIT="${OYB_UPDATE_WAIT:-20}"
SERVICES="${OYB_UPDATE_SERVICES:-oyb-panel.service reforger-timer.service}"

say() { printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >> "$LOG"; }

exec 9> "${LOG}.lock"
flock -n 9 || exit 0

# Which checkout each service runs from, and as whom.
declare -A owner units
for unit in $SERVICES; do
    [ "$("$SYSTEMCTL" show "$unit" -p LoadState --value)" = loaded ] || continue
    dir="$("$SYSTEMCTL" show "$unit" -p WorkingDirectory --value)"
    user="$("$SYSTEMCTL" show "$unit" -p User --value)"
    [ -n "$dir" ] && [ -d "$dir/.git" ] || { say "$unit: no git checkout at '$dir', skipped"; continue; }
    owner[$dir]="${user:-root}"
    units[$dir]="${units[$dir]:-} $unit"
done

as() { if [ "$1" = "$(id -un)" ]; then shift; "$@"; else local u="$1"; shift; runuser -u "$u" -- "$@"; fi; }

# A change only restarts the service whose code it touches.
needs_restart() {
    local unit="$1" files="$2" path
    while IFS= read -r path; do
        [ -n "$path" ] || continue
        case "$unit:$path" in
            oyb-panel.service:panel/*|oyb-panel.service:bot/*|oyb-panel.service:requirements.txt) return 0 ;;
            reforger-timer.service:panel/*|reforger-timer.service:docs/*|reforger-timer.service:tests/*) ;;
            reforger-timer.service:dev/*|reforger-timer.service:*.md) ;;
            reforger-timer.service:*) return 0 ;;
        esac
    done <<< "$files"
    return 1
}

for dir in "${!owner[@]}"; do
    user="${owner[$dir]}"
    git=(as "$user" git -C "$dir")
    if ! "${git[@]}" fetch -q origin 2> /tmp/oyb-update-fetch.err; then
        say "$dir: couldn't reach GitHub: $(tr '\n' ' ' < /tmp/oyb-update-fetch.err)"
        continue
    fi
    old="$("${git[@]}" rev-parse HEAD)"
    new="$("${git[@]}" rev-parse '@{u}' 2> /dev/null)" || { say "$dir: no upstream branch, skipped"; continue; }
    [ "$old" = "$new" ] && continue
    if ! "${git[@]}" merge-base --is-ancestor "$old" "$new"; then
        say "$dir: has its own commits that GitHub doesn't, so it wasn't touched. Fix it by hand."
        continue
    fi
    files="$("${git[@]}" diff --name-only "$old" "$new")"
    if ! "${git[@]}" merge -q --ff-only "$new" 2> /tmp/oyb-update-merge.err; then
        say "$dir: couldn't update (files changed by hand?): $(tr '\n' ' ' < /tmp/oyb-update-merge.err)"
        continue
    fi
    say "$dir: updated ${old:0:7} -> ${new:0:7} ($("${git[@]}" log -1 --format=%s "$new"))"
    if grep -qx 'requirements.txt' <<< "$files" && [ -x "$dir/.venv/bin/python" ]; then
        as "$user" "$dir/.venv/bin/python" -m pip install -q -r "$dir/requirements.txt" >> "$LOG" 2>&1 \
            || say "$dir: installing the new requirements failed"
    fi
    for unit in ${units[$dir]}; do
        needs_restart "$unit" "$files" || { say "$unit: nothing it uses changed, not restarted"; continue; }
        "$SYSTEMCTL" restart "$unit"
        sleep "$WAIT"
        if "$SYSTEMCTL" is-active --quiet "$unit"; then
            say "$unit: restarted"
            continue
        fi
        say "$unit: didn't start on ${new:0:7}, putting ${old:0:7} back"
        "${git[@]}" reset -q --hard "$old"
        "$SYSTEMCTL" restart "$unit"
        sleep "$WAIT"
        "$SYSTEMCTL" is-active --quiet "$unit" && say "$unit: running on ${old:0:7} again" \
            || say "$unit: still not running; check journalctl -u $unit"
        break
    done
done
