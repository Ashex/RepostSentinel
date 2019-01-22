# Basic docker image for RepostSentinal
# Usage:
#   docker build -t repostsentinal .
#   docker run -d -P --name repostsentinal --link repostsentinaldb:postgres repostsentinal -dbhost postgres -dbname repost_sentinel -dbuser repost_sentinel -dbpass repost_sentinel -clientid clientid -clientsecret clientsecret -user-name username -user-password password -user-agent "RepostSentinal for /u/username"
FROM python:3.5-alpine

MAINTAINER Ahmed Osman (/u/Ashex)

STOPSIGNAL SIGINT
# ca-certificates is needed because without it, pip fails to install packages due to a certificate failure
# plus it's needed to communicate with reddit because SSL
RUN apk --no-cache add ca-certificates

WORKDIR /usr/src/app

COPY requirements.txt /usr/src/app/

RUN apk update && \
 apk add postgresql-libs jpeg zlib && \
 apk add --virtual .build-deps gcc musl-dev postgresql-dev jpeg-dev zlib-dev && \
 pip install -r requirements.txt --no-cache-dir && \
 apk --purge del .build-deps

RUN rm -rf /tmp/*

COPY . /usr/src/app/


ENTRYPOINT ["/bin/sh", "entrypoint.sh"]