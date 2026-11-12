"""Audit every change to the staff flags: is_superuser, is_staff, is_platform_operator.

Owner decision (12 Nov 2026, #122): "superuser = ZeroShield staff"; customers must never be
superusers or staff. Paths that can set the flags (inventory in the #122 PR body):

- Django admin (/admin/): auth.User change form (is_superuser, is_staff) and the UserProfile
  form (is_platform_operator). Reachable only by active, non-terminated is_staff accounts
  (main_app/admin_site.py), so only existing staff can grant. The admin site runs every
  view inside ``privilege_actor(request.user, "django_admin")``.
- ``manage.py ensure_zeroshield_admin`` (shell on the control host), which runs inside
  ``privilege_actor(None, "manage.py ensure_zeroshield_admin")``.
- ``manage.py createsuperuser`` and ``manage.py shell`` (shell on the control host).
- NOT the REST API: no serializer declares the flags (UserCreateSerializer,
  UserUpdateSerializer, UserSelfProfileUpdateSerializer; UserMeSerializer is read-only),
  and auth/tests/test_privilege_escalation_paths.py proves an org admin cannot set them.

Whatever the path, a model-level hook writes one AuditLog row (action
``privilege_change``, organization NULL) per saved change, naming the actor when the path
set one and ``"unattributed"`` otherwise, so a new path that sets a flag shows up in the
audit trail instead of going unnoticed. Bulk ``QuerySet.update()`` bypasses model signals;
no product code updates these flags in bulk (grep in the PR body).
"""

from __future__ import annotations

import contextlib
import contextvars
import json
import logging

logger = logging.getLogger("auth.privilege_audit")

ACTION_PRIVILEGE_CHANGE = "privilege_change"
USER_FLAGS = ("is_superuser", "is_staff")
PROFILE_FLAGS = ("is_platform_operator",)

_actor: contextvars.ContextVar = contextvars.ContextVar("zs_privilege_actor", default=None)


@contextlib.contextmanager
def privilege_actor(user, via: str):
    """Attribute privilege changes saved inside this block to ``user`` via ``via``."""
    token = _actor.set((user, via))
    try:
        yield
    finally:
        _actor.reset(token)


def _changed_flags(sender, instance, flags, update_fields):
    if update_fields is not None and not set(flags) & set(update_fields):
        return {}
    new = {f: bool(getattr(instance, f, False)) for f in flags}
    if instance._state.adding or instance.pk is None:
        old = {f: False for f in flags}
    else:
        row = sender._default_manager.filter(pk=instance.pk).values(*flags).first()
        old = {f: bool(row[f]) for f in flags} if row else {f: False for f in flags}
    return {f: [old[f], new[f]] for f in flags if old[f] != new[f]}


def _write(target_user_id, changes):
    from core.models import AuditLog

    actor, via = _actor.get() or (None, "unattributed")
    actor_ok = actor is not None and getattr(actor, "pk", None) and getattr(actor, "is_authenticated", False)
    details = {
        "target_user_id": target_user_id,
        "changes": changes,
        "actor_id": actor.pk if actor_ok else None,
        "via": via,
    }
    AuditLog.objects.create(
        user=actor if actor_ok else None,
        organization=None,
        action=ACTION_PRIVILEGE_CHANGE,
        resource=f"user:{target_user_id}",
        details=json.dumps(details, sort_keys=True),
    )
    logger.info(
        "privilege_change target_user_id=%s changes=%s actor_id=%s via=%s",
        target_user_id, changes, details["actor_id"], via,
    )


def user_pre_save(sender, instance, update_fields=None, **kwargs):
    instance._zs_privilege_changes = _changed_flags(sender, instance, USER_FLAGS, update_fields)


def user_post_save(sender, instance, **kwargs):
    changes = getattr(instance, "_zs_privilege_changes", None)
    if changes:
        instance._zs_privilege_changes = {}
        _write(instance.pk, changes)


def profile_pre_save(sender, instance, update_fields=None, **kwargs):
    instance._zs_privilege_changes = _changed_flags(sender, instance, PROFILE_FLAGS, update_fields)


def profile_post_save(sender, instance, **kwargs):
    changes = getattr(instance, "_zs_privilege_changes", None)
    if changes:
        instance._zs_privilege_changes = {}
        _write(instance.user_id, changes)


def connect():
    from django.contrib.auth import get_user_model
    from django.db.models.signals import post_save, pre_save

    from auth.models import UserProfile

    User = get_user_model()
    pre_save.connect(user_pre_save, sender=User, dispatch_uid="zs_privilege_user_pre")
    post_save.connect(user_post_save, sender=User, dispatch_uid="zs_privilege_user_post")
    pre_save.connect(profile_pre_save, sender=UserProfile, dispatch_uid="zs_privilege_profile_pre")
    post_save.connect(profile_post_save, sender=UserProfile, dispatch_uid="zs_privilege_profile_post")
