import sys; sys.path.insert(0,"docs/plans/evidence/2026-11-04-integration")
from ev import *
SLUG=sys.argv[1]
r=Rec("gw32-mcp"); U=G+"/v1/mcp/"+SLUG
M=lambda l,body,**k: r.call(l,"POST",U,body,**k)
init={"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"t","version":"1"}}}
M("unauthenticated initialize (must be refused)",init,key=None)
M("unauthenticated ping (must be refused)",{"jsonrpc":"2.0","id":2,"method":"ping"},key=None)
M("wrong key initialize (must be refused)",init,key="not-a-key")
M("authenticated initialize",init)
M("authenticated ping",{"jsonrpc":"2.0","id":2,"method":"ping"})
M("tools/list (authenticated upstream)",{"jsonrpc":"2.0","id":3,"method":"tools/list"})
M("tools/call echo",{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"echo","arguments":{"text":"hello"}}})
M("tools/call get_contact (L26-2 MCP: email+AKIA in result)",{"jsonrpc":"2.0","id":5,"method":"tools/call","params":{"name":"get_contact","arguments":{}}})
M("tools/call echo with competitor name (L26-1 MCP half: must be ALLOWED)",{"jsonrpc":"2.0","id":6,"method":"tools/call","params":{"name":"echo","arguments":{"text":"AcmeRivalGW26 pricing"}}})
r.call("unknown server","POST",G+"/v1/mcp/nosuch",{"jsonrpc":"2.0","id":7,"method":"tools/list"})
import json
for l in open(r.path):
    d=json.loads(l); print(d['status'],d['label'],'|',d['body_excerpt'][:330].replace('\n',' '))
