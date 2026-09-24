#!/usr/bin/env bash
# edge_config.sh LABEL "GW [GW...]"   nginx on rv-split-edge-1:8080 in front of the gateways (port 8400).
#   Controller-specified: shared upstream state (zone 256k) so round robin is shared across nginx workers,
#   upstream keepalive 256, HTTP/1.1 to upstreams, proxy_buffering off, no retries (proxy_next_upstream off).
set -euo pipefail
SP=/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad
export LANE=split EVID=$SP/evidence/proto-bench-split
source "$SP/harness/deploy/lib.sh"
label=$1 gws=$2
out=$EVID/starts/$label; mkdir -p "$out"
servers=""
for g in $gws; do servers+="    server $(vm_ip "$g"):8400 max_fails=0;"$'\n'; done
cat > "$out/nginx.conf" <<CONF
user www-data;
worker_processes auto;
worker_rlimit_nofile 1048576;
pid /run/nginx.pid;
events { worker_connections 65535; multi_accept on; use epoll; }
http {
  access_log off;
  error_log /var/log/nginx/error.log warn;
  server_tokens off;
  tcp_nodelay on;
  keepalive_timeout 75s;
  keepalive_requests 1000000;
  client_max_body_size 2m;
  client_body_buffer_size 1m;
  upstream rv_gateways {
    zone rv_gateways 256k;
$servers    keepalive 256;
    keepalive_requests 1000000;
    keepalive_timeout 75s;
  }
  server {
    listen 8080 reuseport backlog=65535;
    location /nginx_status { stub_status; }
    location / {
      proxy_pass http://rv_gateways;
      proxy_http_version 1.1;
      proxy_set_header Connection "";
      proxy_set_header Host \$host;
      proxy_buffering off;
      proxy_request_buffering off;
      proxy_read_timeout 360s;
      proxy_send_timeout 360s;
      proxy_connect_timeout 5s;
      proxy_next_upstream off;
    }
  }
}
CONF
rscp_to rv-split-edge-1 /tmp/nginx.conf "$out/nginx.conf"
rssh rv-split-edge-1 'sudo cp /tmp/nginx.conf /etc/nginx/nginx.conf && sudo nginx -t 2>&1 | tail -1 && (sudo systemctl restart nginx) && sleep 1 && systemctl is-active nginx && curl -s -m 3 localhost:8080/readyz | cut -c1-200'
