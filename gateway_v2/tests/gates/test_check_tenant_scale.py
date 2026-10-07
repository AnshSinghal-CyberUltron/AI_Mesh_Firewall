"""The tenant-scale gate, fed R2-02's actual defect shapes.

A gate that only passes on clean code is decorative. Each test below reproduces a line that was
really in RC2 and really cost C4 p99 145-153 ms at 10,000 tenants, and asserts the gate fires on
it. The last two assert the shipped tree is clean and that the one allowlisted read stays allowed.
"""

from __future__ import annotations

from pathlib import Path

from lint.check_tenant_scale import ALLOWED, WHOLE_COLLECTION_COMMANDS, scan_tree

_PKG = Path(__file__).resolve().parents[2] / "gateway_v2"


def _write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


# --- the three shapes that were really in RC2 ---------------------------------------------------


def test_the_killswitch_whole_set_refresh_fails(tmp_path: Path) -> None:
    """RC2 admit/state_v2.py:106 — HVALS of every record, every 500 ms, on the serving loop."""
    _write(
        tmp_path,
        "admit/killswitch.py",
        "async def refresh(client, keys) -> None:\n"
        "    raws = await client.hvals(keys.ks)\n"
        "    verify_set(raws)\n",
    )

    hits = scan_tree(tmp_path)

    assert [hit.symbol for hit in hits] == ["hvals"]
    assert "whole-collection" in hits[0].reason


def test_the_plan_index_reconcile_fails(tmp_path: Path) -> None:
    """RC2 plan/snapshot_v2.py:80 — HGETALL of the index of every tenant, once a second."""
    _write(
        tmp_path,
        "plan/snapshot.py",
        "async def reconcile(client, keys) -> None:\n"
        "    index = await client.hgetall(keys.plan_index)\n"
        "    for org in index:\n"
        "        pass\n",
    )

    hits = scan_tree(tmp_path)

    assert [hit.symbol for hit in hits] == ["hgetall"]


def test_iterating_the_estate_fails(tmp_path: Path) -> None:
    """The shape this card deleted: `for org_id in self._store.known()`."""
    _write(
        tmp_path,
        "plan/snapshot.py",
        "def reconcile(self) -> None:\n"
        "    for org_id in self._store.known():\n"
        "        self._absorb(org_id)\n",
    )

    hits = scan_tree(tmp_path)

    assert [hit.symbol for hit in hits] == ["known()"]
    assert "every tenant" in hits[0].reason


# --- the rest of the forbidden surface -----------------------------------------------------------


def test_every_forbidden_command_fires(tmp_path: Path) -> None:
    """No command in the list may be silently unenforced."""
    for command in sorted(WHOLE_COLLECTION_COMMANDS):
        root = tmp_path / command
        _write(root, "plan/probe.py", f"async def go(c) -> None:\n    await c.{command}('k')\n")
        hits = scan_tree(root)
        assert [hit.symbol for hit in hits] == [command], command


def test_a_scan_iter_sweep_fails(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "runtime/sweep.py",
        "def sweep(client) -> None:\n"
        "    for key in client.scan_iter(match='plan:*'):\n"
        "        pass\n",
    )

    assert [hit.symbol for hit in scan_tree(tmp_path)] == ["scan_iter"]


def test_several_violations_are_all_reported(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "admit/bad.py",
        "async def go(c, s) -> None:\n"
        "    await c.hgetall('a')\n"
        "    await c.smembers('b')\n"
        "    s.known()\n",
    )

    hits = scan_tree(tmp_path)

    assert sorted(hit.symbol for hit in hits) == ["hgetall", "known()", "smembers"]
    assert all(hit.line >= 1 for hit in hits)


# --- what must NOT fire ---------------------------------------------------------------------------


def test_the_allowlisted_engaged_read_is_allowed(tmp_path: Path) -> None:
    """The kill-switch cold start is O(engaged) and is the thing that avoids O(tenants)."""
    _write(
        tmp_path,
        "runtime/store_valkey.py",
        "async def engaged(self, kind) -> tuple[str, ...]:\n"
        "    members = await self._client.smembers(self._keys.engaged(kind))\n"
        "    return tuple(members)\n",
    )

    assert scan_tree(tmp_path) == []


def test_the_allowlist_is_scoped_to_one_file(tmp_path: Path) -> None:
    """smembers elsewhere must still fail: the exemption is a file/command pair, not a command."""
    _write(
        tmp_path,
        "plan/elsewhere.py",
        "async def go(c) -> None:\n    await c.smembers('k')\n",
    )

    assert [hit.symbol for hit in scan_tree(tmp_path)] == ["smembers"]


def test_defining_known_is_not_calling_it(tmp_path: Path) -> None:
    """PlanStore may define it; a refresh path may not call it."""
    _write(
        tmp_path,
        "plan/store.py",
        "class PlanStore:\n"
        "    def known(self) -> tuple[str, ...]:\n"
        "        return tuple(sorted(self._tenants))\n",
    )

    assert scan_tree(tmp_path) == []


def test_a_bounded_read_is_not_a_violation(tmp_path: Path) -> None:
    """The shipped shape: a bounded range above a cursor, and a keyed multi-get."""
    _write(
        tmp_path,
        "runtime/store_valkey.py",
        "async def go(c, keys) -> None:\n"
        "    await c.zcount(keys, '-inf', 10)\n"
        "    await c.zrangebyscore(keys, '(3', 10, start=0, num=256, withscores=True)\n"
        "    await c.hmget('h', ['a', 'b'])\n"
        "    await c.mget(['x', 'y'])\n"
        "    await c.zrevrange(keys, 0, 0, withscores=True)\n"
        "    await c.zcard(keys)\n",
    )

    assert scan_tree(tmp_path) == []


def test_a_local_function_named_keys_is_not_a_store_command(tmp_path: Path) -> None:
    """Only attribute calls count, so a plain local helper is not a false positive."""
    _write(
        tmp_path,
        "plan/local.py",
        "def keys(mapping) -> list[str]:\n"
        "    return list(mapping)\n"
        "def go(mapping) -> list[str]:\n"
        "    return keys(mapping)\n",
    )

    assert scan_tree(tmp_path) == []


# --- the shipped tree -------------------------------------------------------------------------


def test_the_shipped_data_plane_is_clean() -> None:
    assert scan_tree(_PKG) == []


def test_the_allowlist_holds_exactly_the_one_justified_exemption() -> None:
    """Growing this set is the thing a reviewer must notice, so pin it."""
    assert ALLOWED == frozenset({("runtime/store_valkey.py", "smembers")})
