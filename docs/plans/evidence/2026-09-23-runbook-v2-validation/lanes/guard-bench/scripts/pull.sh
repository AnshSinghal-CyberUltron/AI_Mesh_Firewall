#!/usr/bin/env bash
# Pull raw outputs + host info from every lane VM into evidence/raw/<exp>/... (idempotent; run on the controller)
EV=$(cd "$(dirname "$0")/.." && pwd); source $EV/scripts/hosts.env
R=$EV/raw; mkdir -p $R/A $R/B $R/C $R/D $R/hostinfo
get() { # ip remote_dir local_dir
  timeout 900 $EV/scripts/r $1 "test -d $2" </dev/null 2>/dev/null || return 0
  mkdir -p $3; timeout 900 $EV/scripts/rcp -r "rv@$1:$2/." $3/ </dev/null 2>/dev/null
}
for pair in g2-1:$G21 g2-2:$G22 g2-3:$G23 g2-4:$G24 g2-5:$G25 c4-1:$C41 c4-2:$C42 c4h-1:$C4H; do
  n=${pair%%:*}; ip=${pair#*:}; [ -z "$ip" ] && continue
  get $ip gb/hostinfo $R/hostinfo/$n
done
get $G21 gb/out/A $R/A/g2-1
get $G23 gb/out/A $R/A/g2-3
get $G21 gb/out/E $R/E/g2-1
get $G23 gb/out/E $R/E/g2-3
get $G22 gb/out/B $R/B/g2-2
get $G24 gb/out/B $R/B/g2-4
get $C41 gb/out/C $R/C/c4-1
get $C41 gb/out/A $R/A/c4-1
get $C42 gb/out/D $R/D/c4-standard-8
get $C4H gb/out/D $R/D/c4-highcpu-16
echo PULL_DONE
