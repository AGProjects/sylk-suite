#!/bin/bash
. "$(dirname "$0")/compose-lib.sh"
$COMPOSE --env-file /opt/sylk-suite/logs/docker.env logs -f
