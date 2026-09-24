#!/usr/bin/env bash
# edge_conf.sh "IP:PORT IP:PORT ..."  -> writes the production-shaped nginx edge config to stdout
# (upstream keepalive + HTTP/1.1 + Connection "", no proxy buffering for SSE, no retries of POSTs
#  to another unit, access log off). Same config at every scale; only the server list changes.
servers=""
# NGINX_ZONE=1 adds `zone rv_units 256k;`: upstream state (incl. round-robin position) shared by all workers
[[ ${NGINX_ZONE:-0} == 1 ]] && servers+="    zone rv_units 256k;"$'\n'
for s in $1; do servers+="    server $s max_fails=0;"$'\n'; done
cat <<CONF
user www-data;
worker_processes ${NGINX_WORKERS:-auto};
worker_rlimit_nofile 1048576;
pid /run/nginx.pid;
events { worker_connections 65535; multi_accept on; use epoll; }
http {
  access_log off;
  error_log /var/log/nginx/error.log warn;
  server_tokens off;
  sendfile on;
  tcp_nodelay on;
  keepalive_timeout 75s;
  keepalive_requests 1000000;
  client_max_body_size 2m;
  client_body_buffer_size 1m;
  upstream rv_units {
$servers    keepalive 4096;
    keepalive_requests 1000000;
    keepalive_timeout 75s;
  }
  server {
    listen 8080 reuseport backlog=65535;
    location /nginx_status { stub_status; }
    location / {
      proxy_pass http://rv_units;
      proxy_http_version 1.1;
      proxy_set_header Connection "";
      proxy_set_header Host \$host;
      proxy_buffering off;
      proxy_read_timeout 360s;
      proxy_send_timeout 360s;
      proxy_connect_timeout 5s;
      proxy_next_upstream off;
    }
  }
}
CONF
