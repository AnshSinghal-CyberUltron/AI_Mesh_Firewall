# GCP experiment conventions (every experiment lane MUST follow)

SP = /tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad

## Project / region
- Project `ai-mesh-firewall` ONLY. Never touch project `aisecshield-prod` (production) in any way.
- Region asia-south1, zone asia-south1-c first; on stock-out try asia-south1-a, then -b; if Mumbai has no capacity
  at all, another region is allowed — label every result with the region/zone it came from.
- Pricing basis for any $ figure is ON-DEMAND list price (user decision). Pricing lane publishes SKU prices in
  SP/evidence/pricing-verifier/prices.json.
- This controller VM: `ai-mesh-firewall` (c4-standard-16, 10.160.0.2). Do NOT run load/benchmarks on it and do NOT
  touch its running docker stack. It may be used to orchestrate (gcloud/ssh/scp) and for functional tests only.

## Creating VMs
- Name: `rv-<lane>-<role>-<n>` (e.g. rv-guard-g2-1, rv-proto-unit-1, rv-v1-sut-1, rv-micro-lg-1). Lowercase, <=63 chars.
- Labels (mandatory): `--labels=purpose=runbook-validation,lane=<lane>,owner=claude`
- SSH: create with
  `--metadata-from-file=ssh-keys=$SP/gcp/ssh-keys.txt --metadata=block-project-ssh-keys=TRUE`
  and connect with `ssh -i $SP/gcp/rv_ed25519 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null rv@<INTERNAL_IP>`
  (internal IPs are reachable from the controller; default-allow-internal covers 10.128.0.0/9). Do NOT use
  `gcloud compute ssh` (it edits project metadata and races with other lanes). Do NOT use the repo's ai-mesh-firewall key.
- GPU (G2/L4): `--image-project=deeplearning-platform-release --image-family=common-cu129-ubuntu-2204-nvidia-580`
  (NVIDIA 580 driver + CUDA 12.9 preinstalled), `--maintenance-policy=TERMINATE`, `--boot-disk-size=200GB`,
  `--boot-disk-type=pd-balanced`. G2 shapes: g2-standard-4/8/12/16/32 = 1x L4, g2-standard-24 = 2x L4, g2-standard-48 = 4x L4.
- CPU: `--image-project=ubuntu-os-cloud --image-family=ubuntu-2404-lts-amd64`.
  C4 / C4D / C3 REQUIRE `--boot-disk-type=hyperdisk-balanced`; N2 can use pd-balanced.
- Default network, ephemeral external IP is fine (needed for apt/pip/docker pulls).
- Record for every VM in SP/evidence/<lane>/vm-ledger.jsonl: name, zone, machine type, create UTC, delete UTC.
  Pricing lane turns these into actual spend.

## Measurement hygiene (non-negotiable)
- Load generator never runs on the system under test (SUT). Provider/stub never shares cores with the SUT.
- Record on every VM: `lscpu`, `nproc`, `free -g`, `uname -a`, `nvidia-smi -q` (GPU), package versions (`pip freeze`), image.
- Open-loop arrival-rate load only (never closed-loop concurrency) for any latency-under-load or capacity claim.
- Warm-up before measuring; >=5 min steady state per rate step; 3 repeats at the knee (highest passing rate).
  Report highest repeatable passing rate AND the first failing rate.
- Prove the load generator is not the bottleneck (schedule lateness/drops recorded; direct-to-provider run at the same rate).
- Raw data (JSONL per request) is kept; summaries are recomputed from raw data by a script that ships with the evidence.
- Percentiles are computed over merged raw samples, never by averaging per-worker percentiles.

## Cleanup
- Delete every VM you created as soon as your lane no longer needs it:
  `gcloud compute instances delete <name> --zone <zone> --quiet`. Never delete anything not named rv-*.
- Before finishing, run `gcloud compute instances list --filter='labels.lane=<lane>'` and confirm it is empty
  (unless the controller told you to leave a VM for a downstream lane — then say so explicitly in your final answer).

## Secrets
- Security is out of scope. Never print tokens/keys. The HF token lives only on the controller
  (~/.cache/huggingface/token); models are pre-downloaded at SP/models/Llama-Prompt-Guard-2-{22M,86M} — copy
  model files to VMs with scp; never copy the token to a VM.
