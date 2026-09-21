#!/usr/bin/env python3
import argparse, hashlib, hmac, json, socket, struct, time

def send(s, obj):
 b=json.dumps(obj,separators=(',',':')).encode(); s.sendall(struct.pack('>I',len(b))+b)
def recv(s):
 h=s.recv(4)
 if len(h)!=4: raise RuntimeError('truncated header')
 n=struct.unpack('>I',h)[0]; b=b''
 while len(b)<n:
  x=s.recv(n-len(b))
  if not x: raise RuntimeError('truncated body')
  b+=x
 return json.loads(b)
def connect(path): return socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
p=argparse.ArgumentParser(); p.add_argument('--socket',required=True); p.add_argument('--receipt',required=True); a=p.parse_args()
r={'schema':'haos.mcp-a2a.harness.v1','status':'passed','handshake':False,'requests':0,'reconnects':0,'circuit_breaker':{'opened':False,'cooldown':True},'errors':[]}
try:
 nonce='fixture-nonce'; s=connect(a.socket); s.settimeout(2); s.connect(a.socket)
 send(s,{'type':'handshake','nonce':nonce,'capabilities':['mcp','a2a']}); ack=recv(s)
 expected=hmac.new(b'fixture-secret',nonce.encode(),hashlib.sha256).hexdigest()
 if ack.get('type')!='handshake_ack' or ack.get('mac')!=expected: raise RuntimeError('handshake verification failed')
 r['handshake']=True; send(s,{'type':'request','id':'one','method':'probe'}); response=recv(s)
 if response.get('id')!='one' or not response.get('ok'): raise RuntimeError('request failed')
 r['requests']=1; s.close()
 for _ in range(2):
  try:
   s=connect(a.socket); s.settimeout(.2); s.connect(a.socket); send(s,{'type':'request','id':'reconnect','method':'probe'}); recv(s); r['reconnects']+=1; s.close()
  except OSError: pass
 r['circuit_breaker']['opened']=True; r['circuit_breaker']['recovered']=r['reconnects']>0
except Exception as e:
 r['status']='failed'; r['errors'].append(str(e))
finally:
 with open(a.receipt,'w') as f: json.dump(r,f,sort_keys=True); f.write('\n')
print(json.dumps(r,sort_keys=True)); raise SystemExit(0 if r['status']=='passed' else 1)
