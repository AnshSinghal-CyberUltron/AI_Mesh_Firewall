"""The prover's four forgery forms, written against the sealed type."""
from __future__ import annotations
import dataclasses
from pkg.dispatch.provider import call_provider
from pkg.resolve.decision import Decision, Disposition, DispatchAuthorization, mint


def f1_direct(blocked: Decision) -> None:          # direct construction outside resolve/
    call_provider(DispatchAuthorization(blocked), b"raw")


def f2_replace(ok: DispatchAuthorization, blocked: Decision) -> None:   # dataclasses.replace laundering
    call_provider(dataclasses.replace(ok, decision=blocked), b"raw")


def f3_new(blocked: Decision) -> None:              # object.__new__ + object.__setattr__
    a = object.__new__(DispatchAuthorization)
    object.__setattr__(a, "decision", blocked)
    call_provider(a, b"raw")


def f4_launder(blocked: Decision) -> None:          # relabel the Decision itself, then mint legitimately
    a = mint(dataclasses.replace(blocked, disposition=Disposition.ALLOW))
    assert a is not None
    call_provider(a, b"raw")
