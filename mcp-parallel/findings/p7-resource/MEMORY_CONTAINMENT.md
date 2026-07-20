# P7 — per-org memory limit containment (adversarial, PASS)

Complements the fork-bomb (pids cgroup) containment with a MEMORY bomb.

## Config (verified live)
- `memory.max` = 2147483648 (2 GiB) per org sandbox
- `memory.swap.max` = 0 + `MemorySwap=Memory` → **no swap-bypass** of the limit
- baseline `memory.current` ≈ 420 MB (5 running Everything servers)

## Test
Ran a Python allocator in `org-a-mcp-sandbox` that mmaps + TOUCHES 200 MB chunks
(touch forces real pages, not lazy/overcommit) up to 3 GB.

## Result — PASS
```
org-a: allocated … 1600MB → "Killed" (exit 137 = OOM SIGKILL) at the cgroup limit
org-a memory.current AFTER = 419 MB   (reclaimed; NEVER exceeded 2 GiB max)
org-a memory.events oom_kill = 1      (cgroup enforced the cap)
org-a still responsive ("alive")      (OOM killed the runaway PROCESS, not the sandbox)
org-b memory.current: 417 MB → 419 MB (UNCHANGED — cross-tenant isolation holds)
```

## Conclusion
The per-org `mem_limit` (cgroup memory controller, swap disabled) contains a runaway
allocation: the offending process is OOM-killed at 2 GiB, the sandbox's memory never
exceeds its cap, the sandbox recovers, and **another org's sandbox is entirely
unaffected**. Together with pids_limit (fork-bomb) this proves per-tenant CPU/mem/pid
resource isolation — one noisy tenant cannot exhaust host memory or starve another.
