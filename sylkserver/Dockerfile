FROM --platform=linux/amd64 debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update -qq && apt-get install -y -qq\
    ca-certificates curl gnupg \
    && rm -rf /var/lib/apt/lists/*

# Add AG Projects key
RUN curl -fsSL https://download.ag-projects.com/agp-debian-key.gpg \
    -o /usr/share/keyrings/agp-debian-key.gpg

# Add repo
RUN echo "deb [signed-by=/usr/share/keyrings/agp-debian-key.gpg] \
    https://packages.ag-projects.com/debian bookworm main contrib" \
    > /etc/apt/sources.list.d/ag-projects.list

# Install sylkserver
RUN apt-get update -qq && apt-get install -y -qq sylkserver \
    && rm -rf /var/lib/apt/lists/*

#COPY webrtcgateway.ini /etc/sylkserver/webrtcgateway.ini
#arg full_domain=sylk.link

#run sed -i "s/; outbound_sip_proxy/outbound_sip_proxy=${full_domain}/g" /etc/sylkserver/webrtcgateway.ini

COPY entrypoint.sh /usr/local/bin/entrypoint.sh

RUN chmod +x /usr/local/bin/entrypoint.sh

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]

#expose 5060 5061 10888 10000-20000/udp
expose 5060 5061

cmd ["sylk-server", "--no-fork"]

