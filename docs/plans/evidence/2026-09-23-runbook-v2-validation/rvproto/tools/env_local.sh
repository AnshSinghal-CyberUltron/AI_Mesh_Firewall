# Local (controller) functional-test environment for rvproto. Source it.
export SP=/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad
export RV_PORT=${RV_PORT:-8400}
export RV_PROVIDER_URL=${RV_PROVIDER_URL:-http://127.0.0.1:18080}
export RV_REDIS_URL=${RV_REDIS_URL:-redis://127.0.0.1:16379/0}
export RV_GUARD_BACKEND=${RV_GUARD_BACKEND:-local_cpu}
export RV_GUARD_MODEL=${RV_GUARD_MODEL:-$SP/rvproto/models/pg2-22M.onnx}
export RV_GUARD_TOKENIZER=${RV_GUARD_TOKENIZER:-$SP/models/Llama-Prompt-Guard-2-22M/tokenizer.json}
export RV_GUARD_CPU_THREADS=${RV_GUARD_CPU_THREADS:-2}
export RV_GUARD_DEADLINE_MS=${RV_GUARD_DEADLINE_MS:-5000}
export AMF_TARGET_P99_MS=${AMF_TARGET_P99_MS:-2000}   # declared DEV SLO (CPU guard); GPU runs declare 20
export WEB_CONCURRENCY=${WEB_CONCURRENCY:-2}          # logged by the contract as a deviation
export RV_ADMIN_HOOKS=${RV_ADMIN_HOOKS:-1}
export RV_METRICS_DIR=${RV_METRICS_DIR:-$SP/evidence/proto-builder/metrics-local}
mkdir -p "$RV_METRICS_DIR"
