#!/bin/bash
# Render a QR code for the configured Sylk domain.
# The installer persists settings to /opt/sylk-suite/logs/setup.json
# (previously this script sourced a .env file).

set -euo pipefail

SETUP_FILE="${SETUP_FILE:-/opt/sylk-suite/logs/setup.json}"

if [ ! -f "$SETUP_FILE" ]; then
    echo "Settings file not found: $SETUP_FILE" >&2
    echo "Run install.py first." >&2
    exit 1
fi

FULL_DOMAIN=$(python3 -c '
import json, sys
try:
    with open(sys.argv[1]) as f:
        print(json.load(f)["full_domain"])
except (KeyError, OSError, ValueError) as e:
    sys.exit(f"Could not read full_domain from {sys.argv[1]}: {e}")
' "$SETUP_FILE")

qrencode -t ansiutf8 "$FULL_DOMAIN"
