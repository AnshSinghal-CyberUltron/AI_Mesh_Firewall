from pkg.dispatch import provider
from pkg.edge import forge
from pkg.resolve.decision import Decision, Disposition, mint
blocked = Decision(Disposition.BLOCK); ok = mint(Decision(Disposition.ALLOW)); assert ok is not None
for name, fn, args in (("f1_direct", forge.f1_direct, (blocked,)), ("f2_replace", forge.f2_replace, (ok, blocked)),
                       ("f3_new", forge.f3_new, (blocked,)), ("f4_launder", forge.f4_launder, (blocked,))):
    before = len(provider.CALLS)
    try:
        fn(*args); print(f"{name}: provider CALLED with disposition={provider.CALLS[-1]!r}")
    except TypeError as e:
        print(f"{name}: rejected at runtime: {e}")
