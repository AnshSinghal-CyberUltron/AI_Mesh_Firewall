"""Planted violation: a module-level Action-to-Disposition table."""
from gateway_v2.domain import Action, Disposition

TABLE = {Action.BLOCK: Disposition.BLOCK}
