#!/bin/bash
# Shared helper: resolve which Docker Compose CLI this host has.
#
# Compose V2 is a plugin of the docker CLI and is invoked as
# `docker compose` (no hyphen). The standalone V1 `docker-compose` binary
# is deprecated upstream and is no longer packaged on current Debian /
# Ubuntu releases, so scripts must not hard-code it.
#
# Source this file, then use "$COMPOSE" as the command prefix:
#
#   . "$(dirname "$0")/compose-lib.sh"
#   $COMPOSE --env-file "$ENV_FILE" up -d
#
# The resolved command already includes sudo.

if sudo docker compose version >/dev/null 2>&1; then
    COMPOSE="sudo docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
    COMPOSE="sudo docker-compose"
else
    echo "No Docker Compose CLI found (tried 'docker compose' and 'docker-compose')." >&2
    echo "Install it with: apt-get install -y docker-compose-v2" >&2
    exit 1
fi
