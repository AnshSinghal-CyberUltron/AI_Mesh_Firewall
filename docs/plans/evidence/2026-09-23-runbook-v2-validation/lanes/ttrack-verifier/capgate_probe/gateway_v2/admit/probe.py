import asyncio, collections, threading
import anyio, httpx
a = asyncio.Semaphore(value=64)          # semaphore limit via keyword 'value'
b = threading.BoundedSemaphore(value=8)  # keyword 'value'
c = anyio.CapacityLimiter(40)            # call not in CAPACITY_CALLS
d = httpx.Limits(max_keepalive_connections=20)  # keyword not in list
e = collections.deque(maxlen=4096)       # buffer bound via 'maxlen'
LIMIT = 100
f = asyncio.Semaphore(LIMIT)             # literal hoisted into a module constant
g = asyncio.Queue(maxsize=LIMIT)         # same
h = dict(limit_concurrency=1000)         # uvicorn-style concurrency cap
i = asyncio.Queue(maxsize=128)           # control: SHOULD be caught
