import http.client, sys, json
port, n = int(sys.argv[1]), int(sys.argv[2])
c = http.client.HTTPConnection("127.0.0.1", port)   # ONE client keep-alive connection to nginx
for i in range(n):
    c.request("POST", "/v1/chat/completions", body=b"{}", headers={"Content-Type": "application/json"})
    r = c.getresponse(); r.read(); assert r.status == 200, r.status
print(f"client sent {n} requests over 1 keep-alive connection to nginx :{port}")
