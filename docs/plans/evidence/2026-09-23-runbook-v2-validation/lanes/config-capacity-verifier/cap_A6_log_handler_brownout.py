"""Redis brownout: server accepts TCP but never answers. Each gateway log line then
blocks the calling (event-loop) thread for socket_timeout=1.0 s, because emit()
resets the client on TimeoutError and the NEXT line reconnects and waits again."""
import logging, socket, threading, time
from ai_mesh_shared.redis_log_handler import RedisLogPublisher
ls = socket.socket(); ls.bind(("127.0.0.1", 0)); ls.listen(64); port = ls.getsockname()[1]
held = []
def acceptor():
    while True:
        c, _ = ls.accept(); held.append(c)   # accept, never reply
threading.Thread(target=acceptor, daemon=True).start()
pub = RedisLogPublisher(redis_url=f"redis://127.0.0.1:{port}/0", service_name="Gateway")
g = logging.getLogger("gateway"); g.addHandler(pub); g.setLevel(logging.DEBUG); g.propagate = False
child = logging.getLogger("gateway.middleware")
for i in range(3):
    t0 = time.perf_counter(); child.info("request %d authenticated", i)
    print(f"log line {i}: emit blocked caller for {(time.perf_counter()-t0)*1000:.0f} ms; TCP connections opened so far = {len(held)}")
