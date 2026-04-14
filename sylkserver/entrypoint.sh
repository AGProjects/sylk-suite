#!/bin/bash
set -e

FILE="/etc/sylkserver/webrtcgateway.ini"
FILE1="/etc/sylkserver/config.ini"

if [ "${NAT,,}" = "true" ]; then
    TARGET="${LOCAL_IP}:${SIP_PORT}"
else
    TARGET="${FULL_DOMAIN}:${SIP_PORT}"
fi

sed -i "s|^[[:space:]]*;*\s*outbound_sip_proxy.*|outbound_sip_proxy=${TARGET};transport=tcp|" "$FILE"
sed -i "s|^[[:space:]]*;*\s*hostname.*|hostname=${FULL_DOMAIN}|" "$FILE1"
sed -i "s|^[[:space:]]*;*\s*public_port.*|public_port=${WEB_PORT}|" "$FILE1"
exec sylk-server --no-fork
