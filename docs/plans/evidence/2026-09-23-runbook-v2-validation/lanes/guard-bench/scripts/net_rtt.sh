#!/usr/bin/env bash
# Exp C network floor: ICMP ping, netperf TCP_RR (payload sizes of W=1/2/7 int64 requests), sockperf TCP ping-pong.
# usage (on the client): net_rtt.sh <server-ip> <out-dir>     (server must run: netserver; sockperf server --tcp -p 11111)
set -uo pipefail
S=$1; O=$2; mkdir -p $O
sudo ping -c 3000 -i 0.002 $S > $O/ping.txt 2>&1
for RR in 1,1 8192,64 16384,64 57344,64; do
  netperf -H $S -t TCP_RR -l 30 -- -r $RR -o min_latency,mean_latency,p50_latency,p90_latency,p99_latency,max_latency,stddev_latency,transaction_rate,request_size,response_size > $O/netperf_tcprr_${RR/,/_}.txt 2>&1
done
sockperf ping-pong -i $S -p 11111 --tcp -t 30 -m 64 --full-rtt > $O/sockperf_tcp_64B.txt 2>&1
sockperf ping-pong -i $S -p 11111 --tcp -t 30 -m 16384 --full-rtt > $O/sockperf_tcp_16KB.txt 2>&1
echo NET_RTT_DONE
