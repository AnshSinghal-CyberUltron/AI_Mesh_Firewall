#!/bin/bash
# F4 author round 3 (PR #115): the REAL control/server-entrypoint.sh under gunicorn 26.0.0 (the
# control/uv.lock pin). PATH stubs: `python` stands for the rollout (exit $CODE after 2 s; `-m`
# calls exit 1 so the entrypoint falls back), optional `sed` exits $SED_EXIT, and `gunicorn` execs
# gunicorn 26.0.0 serving a trivial WSGI app instead of main_app.asgi (so gunicorn is still the
# exec'd parent of the background launch). Usage: run_g26.sh <entrypoint> <code> [sed_exit]
set -u
EP=$1; CODE=$2; SED_EXIT=${3:-}
H=/home/claude/work/runs/115-author3/g26
T=$(mktemp -d); mkdir "$T/bin"
PORT=$(( 18200 + RANDOM % 500 ))
cat > "$T/bin/python" <<PY
#!/bin/sh
[ "\$1" = "-m" ] && exit 1
echo "rollout stub argv0=\$1 cwd=\$(pwd) exiting $CODE"
sleep 2
exit $CODE
PY
cat > "$T/bin/gunicorn" <<GU
#!/bin/sh
exec /home/claude/work/runs/115-author3/venv-g26/bin/gunicorn --chdir $H app:application --bind 127.0.0.1:$PORT --workers 1
GU
if [ -n "$SED_EXIT" ]; then printf '#!/bin/sh\ncat >/dev/null\nexit %s\n' "$SED_EXIT" > "$T/bin/sed"; fi
chmod +x "$T"/bin/*
PATH="$T/bin:$PATH" CONTROL_WEB_CONCURRENCY=1 sh "$EP" > "$T/out.txt" 2>&1 &
PID=$!
sleep 6
HTTP=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/" || true)
if kill -0 $PID 2>/dev/null; then ALIVE=serving; kill -TERM $PID; wait $PID 2>/dev/null; else wait $PID; ALIVE="exited rc=$?"; fi
grep -E "rollout stub|ERROR|Worker \(pid|Shutting down|Reason|Listening" "$T/out.txt" | sed -E 's/^\[[0-9-]+ [0-9:]+ [+0-9]+\] //'
echo "after 6 s: gunicorn $ALIVE, HTTP $HTTP"
rm -rf "$T"
