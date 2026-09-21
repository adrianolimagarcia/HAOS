#!/usr/bin/env python3
import argparse, hashlib, hmac, json, os, socket, struct

def frame(x):
 b=json.dumps(x,separators=(',',':')).encode(); return struct.pack('>I',len(b))+b
p=argparse.ArgumentParser(); p.add_argument('--socket'); p.add_argument('--ready'); a=p.parse_args()
try: os.unlink(a.socket)
except FileNotFoundError: pass
s=socket.socket(socket.AF_UNIX); s.bind(a.socket); s.listen(1); open(a.ready,'w').close()
while True:
 c,_=s.accept(); c.settimeout(2)
 try:
  while True:
   h=c.recv(4)
   if not h: break
   n=struct.unpack('>I',h)[0]; body=c.recv(n); req=json.loads(body)
   if req.get('type')=='handshake':
    mac=hmac.new(b'fixture-secret',req.get('nonce','').encode(),hashlib.sha256).hexdigest(); c.sendall(frame({'type':'handshake_ack','capabilities':['mcp','a2a'],'mac':mac}))
   elif req.get('type')=='request': c.sendall(frame({'type':'response','id':req.get('id'),'ok':True}))
 finally: c.close()
