# isolated reviewer stack (ports 36379 redis / 38080 synthprov / 38400 rvproto) — BENCHMARKED rvproto source
export SP=/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad
export EV=/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/evidence/reviewer-hidden-failures
export RVSRC=${RVSRC:-$EV/bench-src/rvproto}
export PY=$SP/rvproto/.venv/bin/python
export RV_PORT=${RV_PORT:-47400}
export RV_PROVIDER_URL=${RV_PROVIDER_URL:-http://127.0.0.1:47180}
export RV_REDIS_URL=${RV_REDIS_URL:-redis://127.0.0.1:36379/0}
export RV_GUARD_BACKEND=${RV_GUARD_BACKEND:-local_cpu}
export RV_GUARD_MODEL=${RV_GUARD_MODEL:-$SP/rvproto/models/pg2-22M.onnx}
export RV_GUARD_TOKENIZER=${RV_GUARD_TOKENIZER:-$SP/models/Llama-Prompt-Guard-2-22M/tokenizer.json}
export RV_GUARD_CPU_THREADS=${RV_GUARD_CPU_THREADS:-2}
export RV_GUARD_DEADLINE_MS=${RV_GUARD_DEADLINE_MS:-5000}
export AMF_TARGET_P99_MS=${AMF_TARGET_P99_MS:-2000}
export WEB_CONCURRENCY=${WEB_CONCURRENCY:-2}
export RV_ADMIN_HOOKS=${RV_ADMIN_HOOKS:-1}
export RV_GUARD_SOCKET_DIR=${RV_GUARD_SOCKET_DIR:-/tmp/rvrev-guard}
