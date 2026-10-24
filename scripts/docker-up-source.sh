#!/bin/bash
# Run sylkserver from the source tree /home/agp/work/sylkserver, mounted over
# the installed package via /home/agp/work/sylk-suite/docker-compose.source.yml,
# detached and restarted like the production service (docker-up-debug.sh is the
# foreground variant). The rest of the stack is left untouched (--no-deps).
#
# After pushing new code: sudo docker restart sylkserver
# Back to the deb-installed package: /opt/sylk-suite/scripts/docker-up.sh

. "$(dirname "$0")/compose-lib.sh"

ENV_FILE=/opt/sylk-suite/logs/docker.env
SOURCE_COMPOSE=/home/agp/work/sylk-suite/docker-compose.source.yml

for path in /home/agp/work/sylkserver/sylk /home/agp/work/sylkserver/sylk-server /home/agp/work/sylk-suite/sylkserver/entrypoint.sh; do
    if [ ! -e "$path" ]; then
        echo "Missing $path: push the work trees first" >&2
        exit 1
    fi
done

cd /opt/sylk-suite || exit 1

$COMPOSE --env-file $ENV_FILE \
    -f /opt/sylk-suite/docker-compose.yml \
    -f $SOURCE_COMPOSE \
    up -d --no-deps --force-recreate sylkserver

$COMPOSE --env-file $ENV_FILE logs --tail 20 sylkserver
