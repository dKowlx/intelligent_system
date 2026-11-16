#!/bin/sh
# Run as root on the VPS. No credentials are passed to the backup container.
set -eu
umask 077
BASE=/opt/studynavigator
BACKUPS="$BASE/backups"
mkdir -p "$BACKUPS"
chmod 0700 "$BACKUPS"
exec 9>"$BACKUPS/.backup.lock"
flock -n 9 || { echo 'Another backup is running.' >&2; exit 1; }
cd "$BASE/app"
compose() {
    docker compose --env-file ../server.env -f compose.yaml -f compose.knowledge.yaml "$@"
}
backup_tmp=
restart_needed=0
cleanup() {
    saved_status=$?
    trap - EXIT HUP INT TERM
    if [ "$restart_needed" -eq 1 ]; then
        if ! compose start app; then
            echo 'App restart failed; run the Compose start command manually.' >&2
            saved_status=1
        fi
    fi
    if [ -n "$backup_tmp" ]; then
        rm -f "$backup_tmp"
    fi
    exit "$saved_status"
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
# Preserve an intentional shutdown; a scheduled backup must not reopen the site.
if [ -n "$(compose ps --status running -q app)" ]; then
    # Set before stop so an interrupted stop still restores the prior state.
    restart_needed=1
    compose stop app
fi
backup_tmp=$(mktemp "$BACKUPS/.navigator-XXXXXXXX.tar.gz")
docker run --rm --network none --read-only --user 10001:10001 \
    --tmpfs /tmp:rw,size=1g,mode=1777 \
    --cap-drop ALL --security-opt no-new-privileges:true \
    -v studynavigator_app_data:/app/var \
    -v /opt/project-navigator/knowledge:/app/knowledge:ro \
    --entrypoint python studynavigator:local deploy/backup.py \
    --db /app/var/study.db --uploads /app/var/uploads \
    --ledger /app/var/live-budget.json \
    --knowledge-db /app/var/knowledge.sqlite3 --knowledge-root /app/knowledge \
    > "$backup_tmp"
tar -tzf "$backup_tmp" > /dev/null
backup_final="$BACKUPS/navigator-$(date -u +%Y%m%dT%H%M%SZ)-$$.tar.gz"
chmod 0600 "$backup_tmp"
mv "$backup_tmp" "$backup_final"
backup_tmp=
printf '%s\n' "$backup_final"
