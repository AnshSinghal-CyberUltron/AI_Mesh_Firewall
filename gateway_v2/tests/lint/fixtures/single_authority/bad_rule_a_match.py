"""Planted violation: a second Action-to-Disposition mapping as a match statement."""
from gateway_v2.domain import Action, Disposition


def second_authority(action: Action) -> Disposition:
    match action:
        case Action.REDACT:
            return Disposition.REDACT
        case _:
            return Disposition.ALLOW
