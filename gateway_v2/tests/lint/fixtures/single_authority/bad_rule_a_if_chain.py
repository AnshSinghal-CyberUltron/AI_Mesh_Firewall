"""Planted violation: a second Action-to-Disposition mapping as an if-chain."""
from gateway_v2.domain import Action, Disposition


def second_authority(action: Action) -> Disposition:
    if action is Action.BLOCK:
        return Disposition.BLOCK
    return Disposition.ALLOW
