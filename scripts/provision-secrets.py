#!/usr/bin/env python3
"""Create random, stable per-service secrets; never print values."""
import subprocess,json,secrets,base64
from cluster import kubectl
K=kubectl()
def run(*a,**kw):return subprocess.run(K+list(a),check=True,**kw)
def ensure(name,values):
 r=subprocess.check_output(K+['get','secret','emailflow-'+name,'--ignore-not-found','-o','name'],text=True)
 if r.strip():return
 run('create','-f','-',input=json.dumps({'apiVersion':'v1','kind':'Secret','metadata':{'name':'emailflow-'+name},'type':'Opaque','stringData':values}),text=True,stdout=subprocess.DEVNULL)
ensure('db-owner',{'POSTGRES_PASSWORD':secrets.token_hex(32)})
for role in ('api','scheduler','gateway','worker','mirror','archive','users'):
 pw=secrets.token_hex(32);ensure(role,{'DATABASE_URL':f'postgresql://ef_{role}:{pw}@postgres:5432/emailflow'})
ensure('gateway-token',{'GATEWAY_TOKEN':secrets.token_urlsafe(48)})
ensure('embed-token',{'EMBED_TOKEN':secrets.token_urlsafe(48)})
ensure('auth',{'AUTH_ENCRYPTION_KEY':base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()})
print('Service secrets ready (values not displayed).')
