#!/usr/bin/env bash
# Controller: when both off-box runs are done, turn G2-5 into an in-process host and start chain_g25.sh on it.
EV=$(cd "$(dirname "$0")/.." && pwd); source $EV/scripts/hosts.env
until grep -q "DRIVER_C_DONE Llama-Prompt-Guard-2-86M" $EV/logs/driver_c.log; do sleep 20; done
M=Llama-Prompt-Guard-2-22M
timeout 600 $EV/scripts/r $G25 "sudo docker rm -f triton >/dev/null 2>&1; ~/.local/bin/uv pip install -p ~/venv/bin/python tensorrt-cu13==10.16.1.11 uvloop >/dev/null 2>&1; mkdir -p ~/gb/onnx/$M" </dev/null
timeout 900 $EV/scripts/rcp /var/tmp/guard-bench/onnx/$M/model.fp32.onnx /var/tmp/guard-bench/onnx/$M/ref_scores.json rv@$G25:gb/onnx/$M/ </dev/null
timeout 120 $EV/scripts/rcp $EV/scripts/*.py $EV/scripts/driver_knee.sh $EV/scripts/chain_g25.sh rv@$G25:gb/scripts/ </dev/null
timeout 60 $EV/scripts/rcp $EV/scripts/gpuenv.sh rv@$G25:gb/ </dev/null
timeout 60 $EV/scripts/r $G25 "bash ~/gb/bg.sh ~/gb/chain_g25.log 'bash ~/gb/scripts/chain_g25.sh'" </dev/null
echo CTL_AFTER_C_LAUNCHED
