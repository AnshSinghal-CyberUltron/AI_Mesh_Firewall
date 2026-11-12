#!/usr/bin/env bash
# Reproducible, hash-pinned build of regex 2023.8.8 with the AMF thread-CPU timeout patch.
#
#   build_patched_regex.sh OUT_DIR
#
# 1. Fetches the sdist (or reuses ./sdist/) and verifies its sha256; verifies the patch's sha256.
# 2. Extracts it into OUT_DIR/src, applies the patch with no fuzz.
# 3. Creates an isolated venv on the same interpreter as rv2023.8.8 (/usr/bin/python3.12, 3.12.3)
#    and installs the build backend (setuptools) pinned by version AND hash (--require-hashes).
# 4. Calls the PEP 517 hook directly: no build frontend, no build isolation, no network.
# 5. Writes the wheel to OUT_DIR/dist and its sha256 to OUT_DIR/WHEEL_SHA256.
#
# Environment:
#   REPRO=1 (default)  pin SOURCE_DATE_EPOCH and map the build path out of the debug info,
#                      so two builds give the same wheel bytes. REPRO=0 disables both, REPRO=epoch
#                      sets only SOURCE_DATE_EPOCH (both are experiments, to attribute the difference).
#   ASAN=1             build with -fsanitize=address,undefined (crash fuzz only; never ship it).
#   NOPATCH=1          build the unpatched sdist through the same pipeline (control for benchmarks).
#   PY=...             build interpreter (default /usr/bin/python3.12; must be 3.12.x).
#   EXTRA_PATCH=FILE EXTRA_PATCH_SHA256=HEX
#                      apply a second, hash-checked patch after the clock patch (PROTOTYPES only,
#                      e.g. regex-2023.8.8-nogil-check.prototype.patch; never the shipped build).
#
# Pitfall (found here): setuptools' distutils does cflags = os.environ.get('CFLAGS', cflags), so ANY
# CFLAGS in the environment, even an empty one, REPLACES the interpreter's -DNDEBUG -g -O2 -Wall and
# builds at -O0 with assert() live. This script prepends the interpreter's CFLAGS to any extra flag
# and never exports an empty CFLAGS. A Dockerfile builder stage must do the same.
set -euo pipefail
umask 022
export LC_ALL=C TZ=UTC

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[ $# -eq 1 ] || { echo "usage: $0 OUT_DIR" >&2; exit 2; }
mkdir -p "$1"; OUT="$(cd "$1" && pwd)"
PY="${PY:-/usr/bin/python3.12}"
REPRO="${REPRO:-1}"
ASAN="${ASAN:-0}"
NOPATCH="${NOPATCH:-0}"
EXTRA_PATCH="${EXTRA_PATCH:-}"
unset CFLAGS LDFLAGS CPPFLAGS CC LDSHARED || true

SDIST_URL="https://files.pythonhosted.org/packages/4f/1d/6998ba539616a4c8f58b07fd7c9b90c6b0f0c0ecbe8db69095a6079537a7/regex-2023.8.8.tar.gz"
SDIST_SHA256="fcbdc5f2b0f1cd0f6a56cdb46fe41d2cce1e644e3b68832f3eeebc5fb0f7712e"
PATCH="$HERE/regex-2023.8.8-thread-cputime.patch"
PATCH_SHA256="cb544353400b9870ff8d3018d0c7bf783873f18447566d5bd899783e8ef3cd7d"
REQS="$HERE/build-requirements.txt"
SDIST="$HERE/sdist/regex-2023.8.8.tar.gz"
# Newest member mtime in the sdist (2023-08-08T20:38:00Z): the release, not the build time.
EPOCH=1691527080

"$PY" -I -c 'import sys; assert sys.version_info[:2] == (3, 12), sys.version; print("build interpreter:", sys.version.split()[0], sys.executable)'

# 1. sdist and patch, verified before anything is unpacked
mkdir -p "$HERE/sdist"
if [ ! -f "$SDIST" ]; then
    curl -fsS -o "$SDIST.part" "$SDIST_URL"
    mv "$SDIST.part" "$SDIST"
fi
echo "$SDIST_SHA256  $SDIST" | sha256sum -c --strict --quiet - || { echo "sdist sha256 mismatch" >&2; exit 1; }
echo "$PATCH_SHA256  $PATCH" | sha256sum -c --strict --quiet - || { echo "patch sha256 mismatch" >&2; exit 1; }

# 2. fresh, patched source tree
rm -rf "$OUT/src" "$OUT/dist"
mkdir -p "$OUT/src" "$OUT/dist"
tar -xzf "$SDIST" -C "$OUT/src" --no-same-owner
SRC="$OUT/src/regex-2023.8.8"
if [ "$NOPATCH" = 1 ]; then
    echo "NOPATCH=1: building the UNPATCHED sdist (control build)"
else
    patch -d "$SRC" -p1 --forward --fuzz=0 --no-backup-if-mismatch -s < "$PATCH"
    if [ -n "$EXTRA_PATCH" ]; then
        echo "${EXTRA_PATCH_SHA256:?EXTRA_PATCH needs EXTRA_PATCH_SHA256}  $EXTRA_PATCH" | sha256sum -c --strict --quiet - \
            || { echo "extra patch sha256 mismatch" >&2; exit 1; }
        echo "EXTRA_PATCH: applying $(basename "$EXTRA_PATCH") (prototype build, not the shipped wheel)"
        patch -d "$SRC" -p1 --forward --fuzz=0 --no-backup-if-mismatch -s < "$EXTRA_PATCH"
    fi
fi

# 3. isolated build venv, backend pinned by version and hash
rm -rf "$OUT/build-venv"
"$PY" -m venv "$OUT/build-venv"
"$OUT/build-venv/bin/python" -I -m pip install -q --disable-pip-version-check --no-cache-dir \
    --require-hashes --no-deps --only-binary :all: -r "$REQS"
"$OUT/build-venv/bin/python" -I -m pip freeze --all > "$OUT/build-venv.freeze.txt"

# 4. build
CFLAGS_EXTRA=""
LDFLAGS_EXTRA=""
if [ "$REPRO" = 1 ]; then
    export SOURCE_DATE_EPOCH="$EPOCH"
    CFLAGS_EXTRA="-ffile-prefix-map=$SRC=/usr/src/regex-2023.8.8"
elif [ "$REPRO" = epoch ]; then          # experiment: SOURCE_DATE_EPOCH without the path map
    export SOURCE_DATE_EPOCH="$EPOCH"
else
    unset SOURCE_DATE_EPOCH || true
fi
if [ "$ASAN" = 1 ]; then
    CFLAGS_EXTRA="$CFLAGS_EXTRA -fsanitize=address,undefined -fno-omit-frame-pointer -fno-sanitize-recover=undefined"
    LDFLAGS_EXTRA="-fsanitize=address,undefined"
fi
BASE_CFLAGS="$("$OUT/build-venv/bin/python" -I -c 'import sysconfig; print(sysconfig.get_config_var("CFLAGS"))')"
(
    cd "$SRC"
    if [ -n "$CFLAGS_EXTRA" ]; then export CFLAGS="$BASE_CFLAGS $CFLAGS_EXTRA"; fi
    if [ -n "$LDFLAGS_EXTRA" ]; then export LDFLAGS="$LDFLAGS_EXTRA"; fi
    "$OUT/build-venv/bin/python" -I -c \
        'import sys, setuptools.build_meta as b; print("built", b.build_wheel(sys.argv[1]))' "$OUT/dist"
) > "$OUT/build.log" 2>&1 || { tail -40 "$OUT/build.log" >&2; exit 1; }
# Fail if the compile lost the interpreter's optimisation flags (see the pitfall above).
grep -E -- "-c regex_3/_regex.c" "$OUT/build.log" | grep -q -- "-O2" || { echo "compile line lacks -O2" >&2; exit 1; }
grep -E -- "-c regex_3/_regex.c" "$OUT/build.log" | grep -q -- "-DNDEBUG" || { echo "compile line lacks -DNDEBUG" >&2; exit 1; }
grep -E "^(built|x86_64-linux-gnu-gcc|gcc)" "$OUT/build.log" | sed 's/^/  /' | cut -c1-240

WHEEL="$(ls "$OUT"/dist/*.whl)"
sha256sum "$WHEEL" | tee "$OUT/WHEEL_SHA256"
