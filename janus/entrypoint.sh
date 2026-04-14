#!/bin/bash
set -e
set -x

echo "Using IP: ${IP}"

if [ "${NAT,,}" = "true" ]; then
    sed -i "s|^\([[:space:]]*\)#\?\s*nat_1_1_mapping *=.*|\1nat_1_1_mapping = \"${IP}\"|" /etc/janus/janus.jcfg
    sed -i "s|^\([[:space:]]*\)#\?\s*local_ip *=.*|\1local_ip = \"${LOCAL_IP}\"|" /etc/janus/janus.plugin.sip.jcfg
    sed -i "s|^\([[:space:]]*\)#\?\s*sdp_ip *=.*|\1sdp_ip = \"${IP}\"|" /etc/janus/janus.plugin.sip.jcfg
    
    # Calculate RTP port range
    if [ -n "${RTP_PORT}" ]; then
        START=$((RTP_PORT + 500))
        END=$((RTP_PORT + 1000))

        echo "Setting RTP port range: ${START}-${END}"
        sed -i "s|^\([[:space:]]*\)#\?\s*rtp_port_range *=.*|\1rtp_port_range = \"${START}-${END}\"|" /etc/janus/janus.plugin.sip.jcfg
    fi

else
    sed -i "s|^\([[:space:]]*\)#\?\s*sdp_ip *=.*|\1sdp_ip = \"${IP}\"|" /etc/janus/janus.plugin.sip.jcfg
fi
cat /etc/janus/janus.jcfg | grep nat

exec janus
