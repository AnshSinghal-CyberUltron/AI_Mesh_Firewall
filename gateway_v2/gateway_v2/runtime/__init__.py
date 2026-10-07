"""ResourceContract, cgroup detection, dummy pools, SIGHUP reload."""

from gateway_v2.runtime.cgroup import DetectHooks, detect, detect_with_hooks
from gateway_v2.runtime.errors import CapacityUnavailable, CapacityUnset
from gateway_v2.runtime.kinds import CapacityHint, HardwareSignals, PoolKind
from gateway_v2.runtime.lifecycle import install_sighup
from gateway_v2.runtime.pools import DummyPool, GatewayRuntime
from gateway_v2.runtime.resources import ResourceContract, from_signals, load_contract
from gateway_v2.runtime.state_feed import (
    START,
    Cursor,
    FeedReader,
    FeedRound,
    Head,
    IndexPage,
    StateStore,
)
from gateway_v2.runtime.state_sig import (
    canonical_body,
    decode_manifest,
    decode_record,
    encode_manifest,
    encode_record,
    make_manifest,
    make_record,
    record_matches_index,
)
from gateway_v2.runtime.store_keys import (
    DEFAULT_NAMESPACE,
    ENGAGED_KINDS,
    HASH_KINDS,
    KEYS,
    StoreKeys,
)

__all__ = (
    "DEFAULT_NAMESPACE",
    "ENGAGED_KINDS",
    "HASH_KINDS",
    "KEYS",
    "START",
    "CapacityHint",
    "CapacityUnavailable",
    "CapacityUnset",
    "Cursor",
    "DetectHooks",
    "DummyPool",
    "FeedReader",
    "FeedRound",
    "GatewayRuntime",
    "HardwareSignals",
    "Head",
    "IndexPage",
    "PoolKind",
    "ResourceContract",
    "StateStore",
    "StoreKeys",
    "canonical_body",
    "decode_manifest",
    "decode_record",
    "detect",
    "detect_with_hooks",
    "encode_manifest",
    "encode_record",
    "from_signals",
    "install_sighup",
    "load_contract",
    "make_manifest",
    "make_record",
    "record_matches_index",
)
