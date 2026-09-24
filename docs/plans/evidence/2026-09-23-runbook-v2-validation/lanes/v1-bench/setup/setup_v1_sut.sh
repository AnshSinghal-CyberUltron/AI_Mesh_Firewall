#!/usr/bin/env bash
# setup_v1_sut.sh — reproduce the v1 baseline SUT exactly as measured (run from the controller).
#   1. VMs (GCP.md conventions; ledger in ../vm-ledger.jsonl):
#        vm.sh create rv-v1-sut-1  asia-south1-c g2-standard-24 gpu   # SUT (the 2x L4 are unused by v1)
#        vm.sh create rv-v1-prov-1 asia-south1-c c4-standard-8  cpu   # synthprov
#        vm.sh create rv-v1-lg-1   asia-south1-c c4-standard-8  cpu   # olg
#      prov/lg: harness deploy/prep_host.sh + push_bin.sh (binaries verified against bin/SHA256SUMS)
#   2. v1 source = `git archive` of HEAD 52a584e9 (branch revamp), repo untouched:
#        gateway shared control docker-compose.yml docker-compose.prod.yml scripts/perf/e2e
#        scripts/ensure_*_network.sh .dockerignore services/mcp-stub
#   3. Docker CE 29.8.1 + compose v5.5.1 (install_docker.sh); images built ON the SUT from the export.
#   4. Stack = repo docker-compose.yml + compose.v1bench.yml (prod values from docker-compose.prod.yml:
#      gateway no CPU quota / 12 GiB, WEB_CONCURRENCY unset -> GW03 detector; Tier-2 OFF; output guard ON;
#      in-gateway stub OFF; BYOK custom provider -> synthprov). Services: postgres pgbouncer redis
#      rabbitmq control gateway (project aimeshperf).
#   5. migrate; provision_v1bench.py (org v1bench, block posture, PII + content filtering + output guard,
#      rate-limit ceilings raised with the stage still active, API key, BYOK model synth-1 ->
#      http://<prov>:8080/v1, policy package + PII package seeded and compiled per org; built-in
#      detection packs left at their shipped default OFF — see verify/fp-check-*.txt for why).
set -euo pipefail
SP=/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad
S=$SP/evidence/v1-bench/setup
SUT=${SUT_IP:-10.160.0.37} PROV=${PROV_IP:-10.160.0.38}
KEYOPT=(-i $SP/gcp/rv_ed25519 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR)
REPO=/home/contact_cyberultron_com/AI_Mesh_Firewall
git -C $REPO archive --format=tar.gz -o $SP/v1-src-52a584e9.tgz 52a584e944c08e63fc7a20b521a958e425ffd0e5 \
  gateway shared control docker-compose.yml docker-compose.prod.yml scripts/perf/e2e \
  scripts/ensure_mcp_sandbox_network.sh scripts/ensure_org_sandbox_network.sh .dockerignore services/mcp-stub
scp "${KEYOPT[@]}" $S/install_docker.sh $SP/v1-src-52a584e9.tgz $S/compose.v1bench.yml $S/bench.env \
    $S/provision_v1bench.py $S/sut_sampler.py $S/sut_step.sh $S/v1_events_export.sh $S/hostinfo.sh rv@$SUT:~/
ssh "${KEYOPT[@]}" rv@$SUT "bash ~/install_docker.sh && mkdir -p ~/v1 && cd ~/v1 && tar xzf ~/v1-src-52a584e9.tgz \
  && cp ~/compose.v1bench.yml ~/bench.env . \
  && C='sudo docker compose -f docker-compose.yml -f compose.v1bench.yml' \
  && \$C build control gateway \
  && \$C up -d postgres pgbouncer redis rabbitmq control \
  && until [ \"\$(sudo docker inspect -f '{{.State.Health.Status}}' aimeshperf-control-1)\" = healthy ]; do sleep 3; done \
  && \$C exec -T control python manage.py migrate --noinput \
  && \$C up -d gateway \
  && until [ \"\$(sudo docker inspect -f '{{.State.Health.Status}}' aimeshperf-gateway-1)\" = healthy ]; do sleep 3; done \
  && sudo docker cp ~/provision_v1bench.py aimeshperf-control-1:/tmp/provision_v1bench.py \
  && \$C exec -T -e V1B_PROV_API_BASE=http://$PROV:8080/v1 -e V1B_MODEL_NAME=synth-1 control python /tmp/provision_v1bench.py \
  && sudo docker cp aimeshperf-control-1:/tmp/v1bench.key ~/v1bench.key && sudo chown rv ~/v1bench.key && chmod 600 ~/v1bench.key \
  && sudo docker logs aimeshperf-gateway-1 2>&1 | grep gateway-entrypoint"
# loadgen auth file (never printed): "Bearer <key>" -> ~/rv/auth.txt on rv-v1-lg-1
