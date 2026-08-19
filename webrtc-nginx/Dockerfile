########################
# Build sylk-webrtc
########################
FROM node:20-bookworm AS build

WORKDIR /build

RUN apt-get update -qq && apt-get install -y -qq git yarn

RUN git clone https://github.com/AGProjects/sylk-webrtc.git .

COPY config.js src/app/config.js

ARG FULL_DOMAIN=sylk.link
ARG WEB_PORT=443

# The defaults above are NOT a safety net: compose passes these through as
# build args, and an unset variable substitutes to an empty string, which
# overrides the default. That is how sylk-suite issue #2 happened — every
# URL in config.js lost its hostname ('https://:/enrollment/user') because
# the stack was built without --env-file. Refuse to build instead.
RUN if [ -z "$FULL_DOMAIN" ]; then \
        echo "ERROR: FULL_DOMAIN build arg is empty." >&2; \
        echo "Build via install.py, or pass --env-file /opt/sylk-suite/logs/docker.env to compose." >&2; \
        exit 1; \
    fi

RUN sed -i "s/sylk.link/${FULL_DOMAIN}/g" src/app/config.js && \
    sed -i "s/videoconference.sip2sip.info/videoconference.${FULL_DOMAIN}/g" src/app/config.js && \
    sed -i "s/webrtc.sipthor.net/${FULL_DOMAIN}/g" src/app/config.js && \
    sed -i "s/blink.sipthor.net\/enrollment-sylk-mobile.phtml/${FULL_DOMAIN}\/enrollment\/user/g" src/app/config.js

# An empty WEB_PORT means "the default", i.e. 443 — without the -n test an
# empty value took the != 443 branch and rewrote every https:// into
# 'https://:' (no host, no port).
RUN if [ -n "$WEB_PORT" ] && [ "$WEB_PORT" != "443" ]; then \
    sed -i "s|wss://${FULL_DOMAIN}|wss://${FULL_DOMAIN}:${WEB_PORT}|g" src/app/config.js && \
    sed -i "s|https://${FULL_DOMAIN}|https://${FULL_DOMAIN}:${WEB_PORT}|g" src/app/config.js ; \
    fi

# Last line of defence: catch any URL left without a hostname before the
# frontend is compiled around it. Cheaper to fail here than to debug
# "Invalid enrollment url link" in a browser three deploys later.
RUN if grep -Eq "(https?|wss)://[:/]" src/app/config.js; then \
        echo "ERROR: config.js still contains URLs without a hostname:" >&2; \
        grep -En "(https?|wss)://[:/]" src/app/config.js >&2; \
        exit 1; \
    fi

RUN ./configure
RUN make
COPY to-json.js to-json.js
RUN node to-json.js

########################
# Runtime (nginx only)
########################
FROM nginx:alpine

#RUN rm /etc/nginx/conf.d/default.conf

#COPY nginx.conf /etc/nginx/conf.d/default.conf

#VOLUME /etc/ssl/sylkserver

COPY --from=build /build/dist /usr/share/nginx/html
COPY --from=build /build/sylk-config.json /usr/share/nginx/html
# Preserve the build-time config under a second name so it survives the
# installer's post-build overwrite of sylk-config.json (which replaces it
# with a version tailored for the Sylk Mobile client). sylk-config-website.json
# lets you inspect exactly what was injected into the web frontend at
# `make` time, to debug domain/port-related issues.
COPY --from=build /build/sylk-config.json /usr/share/nginx/html/sylk-config-website.json

EXPOSE 80 443

CMD ["nginx", "-g", "daemon off;"]

