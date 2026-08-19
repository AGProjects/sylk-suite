#!/bin/bash
# Run sylkserver in the FOREGROUND using the source tree in
# /home/agp/work/sylkserver, mounted over the installed package via
# /home/agp/work/sylk-suite/docker-compose.debug.yml.
#
# Output shows in the terminal and is appended to ~/sylkserver.log.
# Ctrl+C stops the server. Janus and the rest of the stack are left
# untouched (--no-deps). To go back to the normal setup afterwards:
#   /opt/sylk-suite/scripts/docker-up.sh

. "$(dirname "$0")/compose-lib.sh"

ENV_FILE=/opt/sylk-suite/logs/docker.env
DEBUG_COMPOSE=/home/agp/work/sylk-suite/docker-compose.debug.yml
LOG_FILE="$HOME/sylkserver.log"

cd /opt/sylk-suite || exit 1

$COMPOSE --env-file $ENV_FILE stop sylkserver

$COMPOSE --ansi never --env-file $ENV_FILE \
    -f /opt/sylk-suite/docker-compose.yml \
    -f $DEBUG_COMPOSE \
    up --no-deps --no-log-prefix sylkserver 2>&1 | tee -a "$LOG_FILE"
