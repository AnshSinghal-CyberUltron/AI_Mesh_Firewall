#!/bin/bash
# Cross-check with the READY (validated) olg: fresh VMs, identical SUT/edge/upstream setup and step scripts as the
# main runs; ONLY the olg binary changes (+ the harness's prep_host.sh tuning on loadgens, which it is validated with).
set -euo pipefail
SP=/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad
E=$SP/evidence/micro-claims; H=$SP/harness; X=$E/xcheck; cd $E
( bin/mkvm.sh rv-micro-sut-3 c4-standard-8 > $X/.v1 2>&1 & bin/mkvm.sh rv-micro-lg-6 c4-standard-16 > $X/.v2 2>&1 &
  bin/mkvm.sh rv-micro-edge-3 c4-standard-8 > $X/.v3 2>&1 & bin/mkvm.sh rv-micro-up-3 c4-standard-8 > $X/.v4 2>&1 &
  bin/mkvm.sh rv-micro-lg-7 c4-standard-16 > $X/.v5 2>&1 & wait )
cat $X/.v1 $X/.v2 $X/.v3 $X/.v4 $X/.v5 | tee $X/vms.txt
ip() { awk -v n=$1 '$1==n{print $4}' $X/vms.txt; }
cat > $X/xcheck.env <<EOV
SUT3=$(ip rv-micro-sut-3)
LG6=$(ip rv-micro-lg-6)
EDGE1=$(ip rv-micro-edge-3)
UP1=$(ip rv-micro-up-3)
LG2=$(ip rv-micro-lg-7)
EOV
source $X/xcheck.env; cat $X/xcheck.env
run() { local ip=$1 role=$2 name=$3; for i in $(seq 1 30); do bin/rssh $ip true 2>/dev/null && break; sleep 5; done
  bin/rscp setup/base.sh setup/hostinfo.sh rv@$ip:~/ && bin/rssh $ip "bash ~/base.sh $role" > setup/$name.log 2>&1 < /dev/null
  bin/rssh $ip "bash ~/hostinfo.sh" > hostinfo/$name.txt 2>&1 < /dev/null; echo "$name: $(tail -1 setup/$name.log)"; }
run $SUT3 py sut-3 & run $LG6 lg lg-6 & run $EDGE1 edge edge-3 & run $UP1 py up-3 & run $LG2 lg lg-7 & wait
# loadgens: validated olg + harness host prep + analyzers + corpora
for ip in $LG6 $LG2; do
  bin/rscp $H/bin/olg $H/deploy/prep_host.sh bin/micro_analyze.py claim1/corpus_4k.jsonl rv@$ip:~/
  bin/rssh $ip "bash ~/prep_host.sh; VIRTUAL_ENV=~/venv ~/.local/uv pip install -q numpy orjson 2>/dev/null || VIRTUAL_ENV=~/venv ~/.local/bin/uv pip install -q numpy orjson; which zstd; sha256sum ~/olg" < /dev/null
done
bin/rssh $LG2 "echo '$EDGE1 aimeshgateway.zeroshield.ai aimeshfirewall.zeroshield.ai' | sudo tee -a /etc/hosts >/dev/null" < /dev/null
# claim-1 SUT
bin/rscp claim1/sut.sh claim1/mw_app.py claim1/cpusample.py rv@$SUT3:~/; bin/rssh $SUT3 "chmod +x sut.sh cpusample.py" < /dev/null
# claim-3 edge + upstream (identical configs; edge.sh points gateway at the new upstream)
bin/rssh $EDGE1 "sudo docker pull -q nginx:1.30-alpine >/dev/null; mkdir -p ~/nginx/edge_errors ~/nginx/ssl ~/venv/bin && ln -sf /usr/bin/python3 ~/venv/bin/python && ln -sf /usr/bin/python3 ~/venv/bin/python3; echo '$UP1 gateway' | sudo tee -a /etc/hosts >/dev/null; openssl req -x509 -nodes -days 30 -newkey rsa:2048 -keyout ~/nginx/ssl/origin.key -out ~/nginx/ssl/origin.crt -subj '/CN=zeroshield.ai/O=ZeroShield/C=IN' 2>/dev/null; chmod 644 ~/nginx/ssl/*" < /dev/null
sed "s/--add-host gateway:[0-9.]*/--add-host gateway:$UP1/" claim3/edge.sh > $X/edge.sh; chmod +x $X/edge.sh
bin/rscp -r claim3/nginx/base claim3/nginx/A_prod claim3/nginx/B_keepalive rv@$EDGE1:~/nginx/
bin/rscp claim3/nginx/base/edge-error.json rv@$EDGE1:~/nginx/edge_errors/__edge_error.json
bin/rscp $X/edge.sh claim3/netsnap.py claim1/cpusample.py rv@$EDGE1:~/
bin/rscp claim3/up.sh claim1/mw_app.py claim3/netsnap.py claim1/cpusample.py rv@$UP1:~/
echo SETUP_XCHECK_DONE
