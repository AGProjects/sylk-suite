# janus/Dockerfile
FROM --platform=linux/amd64 debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive

# Install Janus and essentials
RUN apt-get update -qq && apt-get install -qq -y \
    janus \
    && rm -rf /var/lib/apt/lists/*

# Copy custom configuration (optional)
#COPY config/janus.cfg /usr/local/etc/janus/janus.cfg

#ARG IP=0.0.0.0

#RUN sed -i "s/local_ip = 0.0.0.0/local_ip = ${IP}/g" /usr/local/etc/janus/janus.cfg && \
#    sed -i "s/nat_1_1_mapping = 0.0.0.0/nat_1_1_mapping = ${IP}/g" /usr/local/etc/janus/janus.cfg

COPY entrypoint.sh /usr/local/bin/entrypoint.sh

RUN chmod +x /usr/local/bin/entrypoint.sh

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]

# Expose internal ports only
EXPOSE 60500-61000/udp

CMD ["janus"]
