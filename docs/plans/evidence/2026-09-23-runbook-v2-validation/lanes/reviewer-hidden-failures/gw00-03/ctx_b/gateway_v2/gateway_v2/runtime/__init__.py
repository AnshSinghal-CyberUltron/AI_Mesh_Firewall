"""ResourceContract, cgroup detection, dummy pools, SIGHUP reload."""

from gateway_v2.runtime.cgroup import DetectHooks, detect, detect_with_hooks
from gateway_v2.runtime.errors import CapacityUnavailable, CapacityUnset
from gateway_v2.runtime.kinds import CapacityHint, HardwareSignals, PoolKind
from gateway_v2.runtime.lifecycle import install_sighup
from gateway_v2.runtime.pools import DummyPool, GatewayRuntime
from gateway_v2.runtime.resources import ResourceContract, from_signals, load_contract

__all__ = (
    "CapacityHint",
    "CapacityUnavailable",
    "CapacityUnset",
    "DetectHooks",
    "DummyPool",
    "GatewayRuntime",
    "HardwareSignals",
    "PoolKind",
    "ResourceContract",
    "detect",
    "detect_with_hooks",
    "from_signals",
    "install_sighup",
    "load_contract",
)
