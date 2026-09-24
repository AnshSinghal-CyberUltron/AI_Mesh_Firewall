#!/usr/bin/env bash
# Finding: CI secret-headers + repo pre-commit miss PKCS#8 "BEGIN PRIVATE KEY" and dotfiles.
# Fixture bodies are placeholder text, never key material.
set -u
EV=$(cd "$(dirname "$0")" && pwd); REPO=/home/contact_cyberultron_com/AI_Mesh_Firewall
R=$(mktemp -d); cd $R && git init -q -b main && git config user.email t@t && git config user.name t
mkdir -p gateway_v2/lint scripts/ralph
git -C $REPO show HEAD:gateway_v2/lint/check_secret_headers.py > gateway_v2/lint/check_secret_headers.py
git -C $REPO show HEAD:scripts/ralph/precommit-secret-scan.sh > scripts/ralph/precommit-secret-scan.sh
fx() { printf -- '-----BEGIN %s-----\nFIXTURE-NOT-A-REAL-KEY\n-----END %s-----\n' "$1" "$1" > "$2"; }
fx "RSA PRIVATE KEY" ctl_rsa_extless                       # control
fx "PRIVATE KEY" deploy_key; fx "PRIVATE KEY" tls.key; fx "PRIVATE KEY" server.pem; fx "OPENSSH PRIVATE KEY" .deploy_key
git add -f . && git commit -qm f
python3 gateway_v2/lint/check_secret_headers.py --repo . --fail-allowlist; echo "CI exit (with control)=$?"
git rm -q --cached ctl_rsa_extless && git commit -qm c
python3 gateway_v2/lint/check_secret_headers.py --repo . --fail-allowlist; echo "CI exit (4 keys tracked, no control)=$?"
fx "PRIVATE KEY" staged_key && fx "OPENSSH PRIVATE KEY" .id_x && git add -f staged_key .id_x
bash scripts/ralph/precommit-secret-scan.sh; echo "pre-commit exit=$?"
