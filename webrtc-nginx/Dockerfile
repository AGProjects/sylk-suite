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

RUN sed -i "s/sylk.link/${FULL_DOMAIN}/g" src/app/config.js && \
    sed -i "s/videoconference.sip2sip.info/videoconference.${FULL_DOMAIN}/g" src/app/config.js && \
    sed -i "s/webrtc.sipthor.net/${FULL_DOMAIN}/g" src/app/config.js && \
    sed -i "s/blink.sipthor.net\/enrollment-sylk-mobile.phtml/${FULL_DOMAIN}\/enrollment\/user/g" src/app/config.js

RUN if [ "$WEB_PORT" != "443" ]; then \
    sed -i "s|wss://${FULL_DOMAIN}|wss://${FULL_DOMAIN}:${WEB_PORT}|g" src/app/config.js && \
    sed -i "s|https://${FULL_DOMAIN}|https://${FULL_DOMAIN}:${WEB_PORT}|g" src/app/config.js ; \
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

EXPOSE 80 443

CMD ["nginx", "-g", "daemon off;"]

