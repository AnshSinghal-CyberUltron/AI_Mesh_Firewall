#!/usr/bin/env bash
# edge_set.sh TAG "IP:PORT ..."  -- install the edge nginx config for this upstream list (graceful reload)
#   env EDGE_IP (default 10.146.0.10 = rv-pbf-edge-1; rv-pbf-edge-2 = 10.146.0.20)
source "$(dirname "$0")/env.sh"
source $SP/rvproto/deploy/ssh.sh
tag=$1; servers=$2; E=${EDGE_IP:-10.146.0.10}
bash "$(dirname "$0")/edge_conf.sh" "$servers" > $EVID/notes/nginx-$tag.conf
rvscp $EVID/notes/nginx-$tag.conf rv@$E:/tmp/nginx.conf
rvssh $E 'sudo cp /tmp/nginx.conf /etc/nginx/nginx.conf && sudo nginx -t 2>&1 | tail -1 && (sudo systemctl reload nginx || sudo systemctl restart nginx) && sleep 2 && grep -c "server 10" /etc/nginx/nginx.conf && systemctl is-active nginx'
