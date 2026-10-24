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
sed -i "s|^[[:space:]]*;*\s*outbound_proxy.*|outbound_proxy=${TARGET};transport=tcp|" "$FILE1"
sed -i "s|^[[:space:]]*;*\s*hostname.*|hostname=${FULL_DOMAIN}|" "$FILE1"
# MSRP URIs in the SDP must name the public host, not the container's address
# (a config.ini made before the [MSRP] msrp_hostname line existed gets it added)
if grep -qE '^[[:space:]]*;*[[:space:]]*msrp_hostname' "$FILE1"; then
    sed -i "s|^[[:space:]]*;*\s*msrp_hostname.*|msrp_hostname=${FULL_DOMAIN}|" "$FILE1"
elif grep -qE '^\[MSRP\]' "$FILE1"; then
    sed -i "/^\[MSRP\]/a msrp_hostname=${FULL_DOMAIN}" "$FILE1"
else
    printf '\n[MSRP]\nmsrp_hostname=%s\n' "${FULL_DOMAIN}" >> "$FILE1"
fi
sed -i "s|^[[:space:]]*;*\s*public_port.*|public_port=${WEB_PORT}|" "$FILE1"

# TLS (SIP, MSRP, the web server): the domain's certificate from certbot instead of the
# package's self-signed one, which clients refuse ("issuer is unknown"). SylkServer takes
# certificate and key in one file; it is made again at every start, so a renewal is
# picked up by restarting the container.
LIVE="/etc/letsencrypt/live/${FULL_DOMAIN}"
DOMAIN_CERT="/var/lib/sylkserver/tls/domain.crt"
if [ -r "$LIVE/fullchain.pem" ] && [ -r "$LIVE/privkey.pem" ]; then
    mkdir -p "$(dirname "$DOMAIN_CERT")"
    (umask 077; cat "$LIVE/fullchain.pem" "$LIVE/privkey.pem" > "$DOMAIN_CERT")
    sed -i "s|^[[:space:]]*certificate[[:space:]]*=.*|certificate = ${DOMAIN_CERT}|" "$FILE1"
    echo "TLS certificate: $LIVE (as $DOMAIN_CERT)"
else
    echo "TLS certificate: none found in $LIVE, the self-signed one stays"
fi
exec sylk-server --no-fork
