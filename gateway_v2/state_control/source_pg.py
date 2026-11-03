"""Control's tables, read with plain SQL in ONE read-only REPEATABLE READ snapshot.

The projector must not import Django, so it reads the tables Django owns by name. Every column
is named explicitly: some deployed databases still carry columns the ORM no longer knows
(`core/migrations/0038` drops `ueba_*` with raw SQL; a stray `key_purpose` survives on some), and
`SELECT *` would make the projection depend on them.

Expiry is compared with `transaction_timestamp()`, the snapshot's own instant, not the Python
clock: the safety heartbeat attests "every revocation visible at `as_of` is durable", and that is
only true if liveness was judged at `as_of` too.

The tables (Django app labels in brackets):

    auth_api_organization   [auth_api]  id, slug, is_active
    core_gatewayapikey      [core]      id, key_hash, organization_id, is_active, expires_at
    core_v2_plan_document   [core]      organization_id, body, revision        (GW36 S2)
    core_killswitch         [core]      organization_id, model_name, api_key_prefix, is_active,
                                        action
"""

from __future__ import annotations

from typing import Any

from state_control.pg import PostgresControlDB
from state_control.source import SourceKey, SourceOrg, SourcePlan, SourceSwitch, SourceView

_AS_OF = "SELECT EXTRACT(EPOCH FROM transaction_timestamp())"

_ORGS = "SELECT slug, is_active FROM auth_api_organization ORDER BY slug"

_KEYS = (
    "SELECT k.key_hash, k.id::text, o.slug, "
    "(k.is_active AND (k.expires_at IS NULL OR k.expires_at > transaction_timestamp())) "
    "FROM core_gatewayapikey k "
    "LEFT JOIN auth_api_organization o ON o.id = k.organization_id "
    "ORDER BY k.key_hash"
)

_PLANS = (
    "SELECT o.slug, d.body, d.revision FROM core_v2_plan_document d "
    "JOIN auth_api_organization o ON o.id = d.organization_id ORDER BY o.slug"
)

_SWITCHES = (
    "SELECT o.slug, s.model_name, s.api_key_prefix, s.action FROM core_killswitch s "
    "LEFT JOIN auth_api_organization o ON o.id = s.organization_id "
    "WHERE s.is_active ORDER BY o.slug, s.model_name, s.api_key_prefix"
)


class PostgresControlSource:
    """Satisfies `state_control.source.ControlSource` over control's shared database."""

    def __init__(self, db: PostgresControlDB) -> None:
        self._db = db

    def read(self) -> SourceView:
        with self._db.cursor_tx(read_only=True) as cursor:
            as_of = float(_one(cursor, _AS_OF)[0])
            cursor.execute(_ORGS)
            orgs = tuple(SourceOrg(str(slug), bool(active)) for slug, active in cursor.fetchall())
            cursor.execute(_KEYS)
            keys = tuple(
                SourceKey(
                    key_hash=str(key_hash),
                    key_id=str(key_id),
                    org_slug=None if slug is None else str(slug),
                    active=bool(active),
                )
                for key_hash, key_id, slug, active in cursor.fetchall()
            )
            cursor.execute(_PLANS)
            plans = tuple(
                SourcePlan(str(slug), str(body), int(revision))
                for slug, body, revision in cursor.fetchall()
            )
            cursor.execute(_SWITCHES)
            switches = tuple(
                SourceSwitch(
                    org_slug=None if slug is None else str(slug),
                    model_name=str(model),
                    api_key_prefix=str(prefix or ""),
                    action=str(action),
                )
                for slug, model, prefix, action in cursor.fetchall()
            )
        return SourceView(as_of=as_of, orgs=orgs, keys=keys, plans=plans, switches=switches)


def _one(cursor: Any, sql: str) -> tuple[Any, ...]:
    cursor.execute(sql)
    row = cursor.fetchone()
    if row is None:
        raise RuntimeError(f"no row from {sql!r}")
    return tuple(row)
