"""Shared-workspace access control. Credentials never leave the API/CLI."""
import base64
import hashlib
import hmac
import os
import re
import secrets
from urllib.parse import urlsplit
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError, InvalidHashError
from cryptography.fernet import Fernet
from fastapi import APIRouter, Request, Response, HTTPException, Depends
from pydantic import BaseModel, Field
from .common import pool
from .totp import matching_step

router = APIRouter(prefix='/api/auth')
ph = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2)
DUMMY = ph.hash(secrets.token_hex(32))
COOKIE = 'emailflow_session'
ORIGIN = os.environ.get('APP_ORIGIN', 'http://localhost:8080')
SECURE = ORIGIN.startswith('https://')
origin_url = urlsplit(ORIGIN)
if not origin_url.hostname or origin_url.username or origin_url.path or origin_url.query or origin_url.fragment:
    raise RuntimeError('APP_ORIGIN must be an origin without a path')
if not SECURE and (origin_url.scheme != 'http' or origin_url.hostname not in ('localhost','127.0.0.1')):
    raise RuntimeError('HTTPS required outside loopback')

def cipher(): return Fernet(os.environ['AUTH_ENCRYPTION_KEY'].encode())
def digest(value): return hashlib.sha256(value.encode()).hexdigest()
def normalize(s): return s.strip().lower()
def csrf_for(token):
    # Derive a separate token so reloads can recover it without browser storage.
    key = base64.urlsafe_b64decode(os.environ['AUTH_ENCRYPTION_KEY'])
    return hmac.new(key, b'emailflow-csrf\0' + token.encode(), hashlib.sha256).hexdigest()

def audit(conn,actor,action,outcome,target=None):
    conn.execute('INSERT INTO security_audit(actor,action,outcome,target) VALUES (%s,%s,%s,%s)',(actor,action,outcome,target))
def origin(request):
    if request.headers.get('origin') != ORIGIN: raise HTTPException(403,'Invalid request origin')

def limited(conn,scope,limit,seconds):
    row=conn.execute('''INSERT INTO auth_limits(scope,attempts) VALUES (%s,1)
    ON CONFLICT(scope) DO UPDATE SET attempts=CASE WHEN auth_limits.window_start<now()-(%s*interval '1 second') THEN 1 ELSE auth_limits.attempts+1 END,
    window_start=CASE WHEN auth_limits.window_start<now()-(%s*interval '1 second') THEN now() ELSE auth_limits.window_start END RETURNING attempts''',(scope,seconds,seconds)).fetchone()
    return row['attempts']>limit

class Login(BaseModel):
    username:str=Field(min_length=1,max_length=128)
    password:str=Field(min_length=1,max_length=256)
    code:str=Field(pattern=r'^[0-9]{6}$')

@router.post('/login')
def login(data:Login, request:Request, response:Response):
    origin(request)
    username=normalize(data.username)
    if not re.fullmatch(r'[a-z0-9_.@+-]{3,128}',username):
        raise HTTPException(401,'Invalid credentials or verification code')
    token=secrets.token_urlsafe(32); csrf=csrf_for(token)
    # Commit rate counters and failed audits, even if login fails.
    with pool.connection() as conn:
        blocked=limited(conn,'login:global',60,60)
        if not blocked: blocked=limited(conn,'login:user:'+digest(username),5,300)
    if blocked: raise HTTPException(429,'Too many attempts; try again later')
    with pool.connection() as conn:
        u=conn.execute('SELECT id,enabled,role,password_hash,totp_secret,last_totp_step FROM auth_users WHERE username=%s FOR UPDATE',(username,)).fetchone()
        try: ok=ph.verify(u['password_hash'] if u else DUMMY,data.password)
        except (VerifyMismatchError,VerificationError,InvalidHashError): ok=False
        step=None
        if ok and u and u['enabled']:
            step=matching_step(cipher().decrypt(u['totp_secret'].encode()).decode(),data.code,u['last_totp_step'])
        if step is None: audit(conn,username,'login','denied')
        else:
            conn.execute('UPDATE auth_users SET last_totp_step=%s,password_hash=%s WHERE id=%s',(step,ph.hash(data.password) if ph.check_needs_rehash(u['password_hash']) else u['password_hash'],u['id']))
            old=request.cookies.get(COOKIE,'')
            if 0<len(old)<=128: conn.execute('DELETE FROM auth_sessions WHERE token_hash=%s',(digest(old),))
            conn.execute('INSERT INTO auth_sessions(token_hash,user_id,csrf_hash,expires_at) VALUES (%s,%s,%s,now()+interval \'8 hours\')',(digest(token),u['id'],digest(csrf)))
            audit(conn,username,'login','success')
    if step is None: raise HTTPException(401,'Invalid credentials or verification code')
    response.set_cookie(COOKIE,token,httponly=True,secure=SECURE,samesite='strict',max_age=28800,path='/api')
    return {'username':username,'role':u['role'],'csrf':csrf}

def current(request:Request):
    token=request.cookies.get(COOKIE,'')
    if not token or len(token)>128: raise HTTPException(401,'Authentication required')
    with pool.connection() as conn:
        u=conn.execute('''SELECT u.id,u.username,u.role,s.csrf_hash,s.token_hash FROM auth_sessions s JOIN auth_users u ON u.id=s.user_id WHERE s.token_hash=%s AND u.enabled AND s.expires_at>now() AND s.last_seen>now()-interval '30 minutes' ''',(digest(token),)).fetchone()
        if not u: raise HTTPException(401,'Session expired')
        if request.method not in ('GET','HEAD','OPTIONS'):
            origin(request)
            if not hmac.compare_digest(u['csrf_hash'],digest(request.headers.get('x-csrf-token',''))): raise HTTPException(403,'Invalid CSRF token')
        conn.execute("UPDATE auth_sessions SET last_seen=now() WHERE token_hash=%s AND last_seen<now()-interval '1 minute'",(u['token_hash'],))
    return u

def operator(u=Depends(current)):
    if u['role'] not in ('admin','operator'): raise HTTPException(403,'Operator role required')
    return u

def admin(u=Depends(current)):
    if u['role']!='admin': raise HTTPException(403,'Administrator role required')
    return u

@router.get('/me')
def me(request:Request,u=Depends(current)):
    return {'username':u['username'],'role':u['role'],'csrf':csrf_for(request.cookies[COOKIE])}

@router.post('/logout')
def logout(response:Response,u=Depends(current)):
    with pool.connection() as conn:
        conn.execute('DELETE FROM auth_sessions WHERE token_hash=%s',(u['token_hash'],)); audit(conn,u['username'],'logout','success')
    response.delete_cookie(COOKIE,path='/api',secure=SECURE,httponly=True,samesite='strict'); return {'ok':True}

@router.get('/audit')
def audit_events(u=Depends(admin)):
    with pool.connection() as conn: return conn.execute('SELECT * FROM security_audit ORDER BY id DESC LIMIT 100').fetchall()
