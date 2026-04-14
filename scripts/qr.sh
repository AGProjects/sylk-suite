#!/bin/bash

# Load .env file
set -a
source .env
set +a

# Generate QR
qrencode -t ansiutf8 "$FULL_DOMAIN"
