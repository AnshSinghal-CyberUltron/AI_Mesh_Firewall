#!/usr/bin/env bash
# setup_main.sh -- after recreate_main.sh: host prep + harness binaries (sha-checked), Redis (bind all, RDB off,
# maxclients 60000), nginx install, loadgen corpus/auth, iptables ACCT counters, unit helper files + hostinfo.
source "$(dirname "$0")/env.sh"
export ZONE=asia-northeast1-a; source $H/deploy/lib.sh; source $SP/rvproto/deploy/ssh.sh
wait_ssh() { for i in $(seq 1 60); do rssh "$1" true 2>/dev/null && return 0; sleep 5; done; echo "no ssh $1"; return 1; }
for n in rv-pbf-edge-1 rv-pbf-redis-1 rv-pbf-lg-1 rv-pbf-lg-2 rv-pbf-lg-3 rv-pbf-prov-1 rv-pbf-prov-2; do
  (wait_ssh $n && rscp_to $n /tmp/prep_host.sh $H/deploy/prep_host.sh && rssh $n bash /tmp/prep_host.sh >/dev/null 2>&1 \
   && rscp_to $n rv/bin/ $H/bin/synthprov $H/bin/olg $H/bin/rvproxy $H/bin/SHA256SUMS \
   && rssh $n 'cd ~/rv/bin && sha256sum -c --quiet SHA256SUMS && echo "binaries ok on $(hostname -s)"') &
done; wait
for n in rv-pbf-unit-2 rv-pbf-unit-3 rv-pbf-unit-4 rv-pbf-unit-5; do (wait_ssh $n && rssh $n 'echo "$(hostname -s) $(nproc) $(nvidia-smi -L | wc -l)gpu"') & done; wait
rvssh 10.146.0.13 'set -e; sudo DEBIAN_FRONTEND=noninteractive apt-get -qq update >/dev/null; sudo DEBIAN_FRONTEND=noninteractive apt-get -qq install -y redis-server >/dev/null 2>&1; sudo sed -i -e "s/^bind .*/bind 0.0.0.0/" -e "s/^protected-mode .*/protected-mode no/" -e "s/^# maxclients .*/maxclients 60000/" -e "s/^tcp-backlog .*/tcp-backlog 65535/" -e "s/^appendonly .*/appendonly no/" /etc/redis/redis.conf; echo "save \"\"" | sudo tee -a /etc/redis/redis.conf >/dev/null; sudo mkdir -p /etc/systemd/system/redis-server.service.d; printf "[Service]\nLimitNOFILE=1048576\n" | sudo tee /etc/systemd/system/redis-server.service.d/limits.conf >/dev/null; sudo systemctl daemon-reload; sudo systemctl restart redis-server; sleep 1; redis-cli CONFIG SET save "" >/dev/null; echo "redis $(redis-cli INFO server | grep redis_version | tr -d "\r") save=[$(redis-cli CONFIG GET save | tail -1)] bind=$(redis-cli CONFIG GET bind | tail -1)"' &
rvssh 10.146.0.10 'set -e; sudo DEBIAN_FRONTEND=noninteractive apt-get -qq update >/dev/null; sudo DEBIAN_FRONTEND=noninteractive apt-get -qq install -y nginx >/dev/null 2>&1; nginx -v 2>&1; sudo mkdir -p /etc/systemd/system/nginx.service.d; printf "[Service]\nLimitNOFILE=1048576\n" | sudo tee /etc/systemd/system/nginx.service.d/limits.conf >/dev/null; sudo systemctl daemon-reload' &
wait
bash $H/deploy/push.sh $H/corpora/headline-22M.jsonl rv-pbf-lg-1 rv-pbf-lg-2 rv-pbf-lg-3 2>&1 | tail -1
for n in 10.146.0.11 10.146.0.12 10.146.0.9; do rvssh $n 'printf "Bearer sk-rv-org-a-0001" > ~/rv/auth; chmod 600 ~/rv/auth' & done; wait
EDGE=10.146.0.10; P1=10.146.0.8; P2=10.146.0.6; R=10.146.0.13; LGS="10.146.0.11 10.146.0.12 10.146.0.9"; UNITS="10.146.0.2 10.146.0.5 10.146.0.3 10.146.0.4"
acct_cmd() { local c="sudo iptables -N ACCT 2>/dev/null; sudo iptables -F ACCT; sudo iptables -C INPUT -j ACCT 2>/dev/null || sudo iptables -I INPUT 1 -j ACCT; sudo iptables -C OUTPUT -j ACCT 2>/dev/null || sudo iptables -I OUTPUT 1 -j ACCT;"; for ip in $*; do c+=" sudo iptables -A ACCT -d $ip -m comment --comment out_$ip; sudo iptables -A ACCT -s $ip -m comment --comment in_$ip;"; done; echo "$c"; }
for u in $UNITS; do (rvssh $u "$(acct_cmd $EDGE $P1 $P2 $R $LGS) true" >/dev/null 2>&1; rvscp $EVID/scripts/usnap.py $EVID/scripts/gauge_poll.py rv@$u:rv/; rvssh $u 'bash ~/rv/rvproto/deploy/hostinfo.sh ~/rv/hostinfo >/dev/null 2>&1; echo "$(hostname -s) acct $(sudo iptables -L ACCT -n | wc -l)"') & done
(rvssh $EDGE "$(acct_cmd $LGS $UNITS) true" >/dev/null 2>&1; echo edge acct $(rvssh $EDGE 'sudo iptables -L ACCT -n | wc -l')) &
(rvssh $R "$(acct_cmd $UNITS) true" >/dev/null 2>&1; echo redis acct $(rvssh $R 'sudo iptables -L ACCT -n | wc -l')) &
wait; echo SETUP-DONE
