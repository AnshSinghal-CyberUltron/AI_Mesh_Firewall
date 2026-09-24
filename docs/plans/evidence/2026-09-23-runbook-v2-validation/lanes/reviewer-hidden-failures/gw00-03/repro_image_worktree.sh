#!/usr/bin/env bash
# Finding: image digest/content depends on untracked working-tree files (+ runtime PYTHONHASHSEED=0).
# Uses the engine's default builder, distinct tags, removes images at the end.
set -u
EV=$(cd "$(dirname "$0")" && pwd); REPO=/home/contact_cyberultron_com/AI_Mesh_Firewall; W=$(mktemp -d -p $EV)
mkdir $W/a && git -C $REPO archive HEAD gateway_v2/pyproject.toml gateway_v2/Dockerfile gateway_v2/gateway_v2 shared/ai_mesh_shared .dockerignore | tar -x -C $W/a
cp -r $W/a $W/b && echo scratch > $W/b/shared/ai_mesh_shared/local_notes.txt && echo 'X = 1' > $W/b/gateway_v2/gateway_v2/detect/wip_experiment.py
B() { docker buildx build --builder default --no-cache --provenance=false --sbom=false --load --build-arg SOURCE_DATE_EPOCH=1704067200 -f $1/gateway_v2/Dockerfile -t $2 $1 2>&1 | grep -o 'exporting config sha256:[0-9a-f]*'; }
echo "a : $(B $W/a hfreview-gw00:a)"; echo "a2: $(B $W/a hfreview-gw00:a2)"; echo "b : $(B $W/b hfreview-gw00:b)"
docker run --rm --entrypoint sh hfreview-gw00:b -c 'ls /opt/shared/ai_mesh_shared/local_notes.txt /opt/gateway_v2/gateway_v2/detect/wip_experiment.py; python -c "import sys;print(\"hash_randomization\",sys.flags.hash_randomization)"'
docker image rm hfreview-gw00:a hfreview-gw00:a2 hfreview-gw00:b >/dev/null; rm -rf $W
