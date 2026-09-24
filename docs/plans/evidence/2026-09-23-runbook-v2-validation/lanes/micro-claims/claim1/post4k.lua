-- wrk2 script: POST the same ~4 KB chat body olg sends (saturation probe only; not used for latency claims)
wrk.method = "POST"
wrk.headers["Content-Type"] = "application/json"
local f = io.open(os.getenv("HOME") .. "/body4k.json", "rb"); wrk.body = f:read("*a"); f:close()
