import asyncio, sys
variant = sys.argv[1]
calls = []
class Recorder:
    async def open_stream(self, auth, payload):
        calls.append((auth.decision.disposition.value, payload)); return "stream"
class SyncRecorder:
    def open_stream(self, auth, payload):
        calls.append((auth.decision.disposition.value, payload)); return "stream"
if variant == "v1":
    from gateway_v2.resolve.decision import Decision, Disposition, mint_dispatch_authorization, DispatchAuthorization
    from gateway_v2.edge import forge
    blocked = Decision(Disposition.BLOCK, (), (), "v1", ("r-inj",), ())
    ok_auth = mint_dispatch_authorization(Decision(Disposition.ALLOW, (), (), "v1", (), ()))
    print("resolve/ refuses to mint for BLOCK:", mint_dispatch_authorization(blocked))
    asyncio.run(forge.forge_direct(Recorder(), blocked))
    asyncio.run(forge.forge_replace(Recorder(), ok_auth, blocked))
    a = forge.forge_new(blocked); print("object.__new__ forgery isinstance:", isinstance(a, DispatchAuthorization), a.decision.disposition)
    l = forge.forge_launder(blocked); print("laundered forgery carries rules", l.decision.deciding_rules, "disposition", l.decision.disposition)
else:
    from gateway_v2.domain.decision import Decision
    from gateway_v2.domain.taxonomy import Disposition
    from gateway_v2.dispatch import forge
    blocked = Decision(Disposition.BLOCK, (), (), "v1", ("r-inj",), ())
    forge.forge_through_checked_entry(SyncRecorder(), blocked)
print("provider calls recorded (disposition, payload):", calls)
