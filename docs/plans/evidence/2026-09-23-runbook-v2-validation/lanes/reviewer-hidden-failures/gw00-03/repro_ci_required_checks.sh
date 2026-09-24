#!/usr/bin/env bash
# Finding: no gateway-v2 job is a required check; all GW00-GW03 commits were direct pushes.
R=AnshSinghal-CyberUltron/AI_Mesh_Firewall
for b in revamp main; do gh api repos/$R/branches/$b --jq '{name, protected, protection}'; gh api repos/$R/rules/branches/$b --jq '[.[].type]'; done
gh run list -R $R --workflow gateway-v2.yml --limit 40 --json headSha,event,conclusion,displayTitle --jq '.[] | [.event,(.headSha[0:8]),.conclusion,.displayTitle] | @tsv'
grep -nE 'needs:|compare|diff ' /home/contact_cyberultron_com/AI_Mesh_Firewall/.github/workflows/gateway-v2.yml || echo "no needs:/digest comparison in gateway-v2.yml"
