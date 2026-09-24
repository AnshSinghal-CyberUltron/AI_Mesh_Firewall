#!/bin/bash
# On rv-micro-edge-1. edge.sh start VARIANT | stop | stub
case $1 in
start)
  sudo docker rm -f edge >/dev/null 2>&1
  sudo docker run -d --name edge --network host --add-host gateway:10.160.0.57 \
    -v ~/nginx/$2/conf.d:/etc/nginx/conf.d:ro \
    -v ~/nginx/base/edge-error.inc:/etc/nginx/edge-error.inc:ro \
    -v ~/nginx/base/security-headers.inc:/etc/nginx/security-headers.inc:ro \
    -v ~/nginx/base/require-edge-https.inc:/etc/nginx/require-edge-https.inc:ro \
    -v ~/nginx/base/deny-public-openapi.inc:/etc/nginx/deny-public-openapi.inc:ro \
    -v ~/nginx/edge_errors:/etc/nginx/edge_errors:ro -v ~/nginx/ssl:/etc/nginx/ssl:ro \
    nginx:1.30-alpine >/dev/null
  sleep 2; sudo docker exec edge nginx -t 2>&1 | tail -1; echo "edge started variant=$2 workers=$(pgrep -c -f 'nginx: worker')";;
stop) sudo docker rm -f edge >/dev/null 2>&1; echo stopped;;
stub) curl -s http://127.0.0.1:8081/stub_status;;
esac
