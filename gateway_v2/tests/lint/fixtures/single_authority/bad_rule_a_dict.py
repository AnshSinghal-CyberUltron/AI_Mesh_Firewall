"""Planted violation: a second Action-to-Disposition mapping as a dict literal."""
from gateway_v2.domain import Action, Disposition


def second_authority(action: Action) -> Disposition:
    table = {Action.BLOCK: Disposition.BLOCK, Action.FLAG: Disposition.FLAG}
    return table[action]
