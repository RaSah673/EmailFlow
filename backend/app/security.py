"""Shared-workspace access control. Credentials never leave the API/CLI."""
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
from urllib.parse import urlsplit
from uuid import uuid4
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError, InvalidHashError
from cryptography.fernet import Fernet, InvalidToken
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
LOOPBACK_HOSTS = ('localhost', '127.0.0.1')
origin_url = urlsplit(ORIGIN)
if not origin_url.hostname or origin_url.username or origin_url.path or origin_url.query or origin_url.fragment:
    raise RuntimeError('APP_ORIGIN must be an origin without a path')
if not SECURE and (origin_url.scheme != 'http' or origin_url.hostname not in LOOPBACK_HOSTS):
    raise RuntimeError('HTTPS required outside loopback')
LOCAL_ACCOUNTS = os.getenv('EMAILFLOW_LOCAL_ACCOUNTS') == '1' and not SECURE
DEMO_USERNAME = 'user@example.com'
EMAIL_PATTERN = r'[a-z0-9_+-]+(?:\.[a-z0-9_+-]+)*@(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,63}'


def allowed_origins(configured):
    parsed = urlsplit(configured)
    origins = {configured}
    if parsed.scheme == 'http' and parsed.hostname in LOOPBACK_HOSTS:
        port = '' if parsed.port in (None, 80) else f':{parsed.port}'
        origins.update(f'http://{host}{port}' for host in LOOPBACK_HOSTS)
    return frozenset(origins)


REQUEST_ORIGINS = allowed_origins(ORIGIN)


def cipher(): return Fernet(os.environ['AUTH_ENCRYPTION_KEY'].encode())
def new_totp_secret(): return base64.b32encode(secrets.token_bytes(20)).decode()
def digest(value): return hashlib.sha256(value.encode()).hexdigest()
def normalize(s): return s.strip().lower()
def csrf_for(token):
    # Derive a separate token so reloads can recover it without browser storage.
    key = base64.urlsafe_b64decode(os.environ['AUTH_ENCRYPTION_KEY'])
    return hmac.new(key, b'emailflow-csrf\0' + token.encode(), hashlib.sha256).hexdigest()

def audit(conn,actor,action,outcome,target=None):
    conn.execute('INSERT INTO security_audit(actor,action,outcome,target) VALUES (%s,%s,%s,%s)',(actor,action,outcome,target))
def origin(request):
    if request.headers.get('origin') not in REQUEST_ORIGINS: raise HTTPException(403,'Invalid request origin')

def limited(conn,scope,limit,seconds):
    row=conn.execute('''INSERT INTO auth_limits(scope,attempts) VALUES (%s,1)
    ON CONFLICT(scope) DO UPDATE SET attempts=CASE WHEN auth_limits.window_start<now()-(%s*interval '1 second') THEN 1 ELSE auth_limits.attempts+1 END,
    window_start=CASE WHEN auth_limits.window_start<now()-(%s*interval '1 second') THEN now() ELSE auth_limits.window_start END RETURNING attempts''',(scope,seconds,seconds)).fetchone()
    return row['attempts']>limit

class Login(BaseModel):
    username:str=Field(min_length=1,max_length=128)
    password:str=Field(min_length=1,max_length=256)
    code:str=Field(default='',pattern=r'^(?:[0-9]{6})?$')


class Registration(BaseModel):
    email: str = Field(min_length=3, max_length=128)
    password: str = Field(min_length=15, max_length=256)


class Enrollment(BaseModel):
    enrollment_token: str = Field(min_length=1, max_length=1024)
    code: str = Field(pattern=r'^[0-9]{6}$')


def account_rate_limit(action, username, global_limit):
    # Keep limits committed even when the authentication operation is rejected.
    with pool.connection() as conn:
        blocked = limited(conn, action + ':global', global_limit, 60)
        if not blocked:
            blocked = limited(conn, action + ':user:' + digest(username), 5, 300)
    if blocked:
        raise HTTPException(429, 'Too many attempts; try again later')


def is_demo(username, account):
    return (LOCAL_ACCOUNTS and username == DEMO_USERNAME and account is not None
            and account['enabled'] and account['role'] == 'viewer' and account['totp_secret'] == '')


def create_session(conn, request, user_id):
    token = secrets.token_urlsafe(32)
    csrf = csrf_for(token)
    old = request.cookies.get(COOKIE, '')
    if 0 < len(old) <= 128:
        conn.execute('DELETE FROM auth_sessions WHERE token_hash=%s', (digest(old),))
    conn.execute(
        "INSERT INTO auth_sessions(token_hash,user_id,csrf_hash,expires_at) VALUES (%s,%s,%s,now()+interval '8 hours')",
        (digest(token), user_id, digest(csrf)),
    )
    return token, csrf


def signed_in(response, username, role, token, csrf):
    response.set_cookie(COOKIE, token, httponly=True, secure=SECURE, samesite='strict', max_age=28800, path='/api')
    return {'username': username, 'role': role, 'csrf': csrf}


@router.get('/options')
def account_options():
    demo = None
    if LOCAL_ACCOUNTS:
        with pool.connection() as conn:
            account = conn.execute(
                'SELECT enabled,role,totp_secret FROM auth_users WHERE username=%s', (DEMO_USERNAME,),
            ).fetchone()
        if is_demo(DEMO_USERNAME, account):
            demo = DEMO_USERNAME
    return {'registration': LOCAL_ACCOUNTS, 'demo_username': demo}


def registration_origin(request):
    if not LOCAL_ACCOUNTS:
        raise HTTPException(404, 'Registration is unavailable')
    origin(request)


@router.post('/register')
def begin_registration(data: Registration, request: Request):
    registration_origin(request)
    email = normalize(data.email)
    if not re.fullmatch(EMAIL_PATTERN, email):
        raise HTTPException(422, 'Enter a valid email address')
    if email == DEMO_USERNAME:
        raise HTTPException(409, 'This email is reserved for the local demo account')
    account_rate_limit('register', email, 12)
    secret = new_totp_secret()
    payload = json.dumps([email, ph.hash(data.password), secret]).encode()
    token = cipher().encrypt(payload).decode()
    return {'enrollment_token': token, 'secret': secret}


@router.post('/register/verify', status_code=201)
def verify_registration(data: Enrollment, request: Request, response: Response):
    registration_origin(request)
    try:
        email, password_hash, secret = json.loads(cipher().decrypt(data.enrollment_token.encode(), ttl=600))
    except (InvalidToken, ValueError):
        raise HTTPException(400, 'Setup expired. Start registration again.') from None
    account_rate_limit('verify_registration', email, 24)
    step = matching_step(secret, data.code, -1)
    if step is None:
        raise HTTPException(401, 'Invalid authenticator code')
    with pool.connection() as conn:
        user_id = conn.execute(
            'SELECT public.register_local_operator(%s,%s,%s,%s,%s) AS id',
            (uuid4(), email, password_hash, cipher().encrypt(secret.encode()).decode(), step),
        ).fetchone()['id']
        if user_id is None:
            raise HTTPException(409, 'An account with this email already exists')
        token, csrf = create_session(conn, request, user_id)
        audit(conn, email, 'register', 'success')
    return signed_in(response, email, 'operator', token, csrf)

@router.post('/login')
def login(data:Login, request:Request, response:Response):
    origin(request)
    username=normalize(data.username)
    if not re.fullmatch(r'[a-z0-9_.@+-]{3,128}',username):
        raise HTTPException(401,'Invalid credentials or verification code')
    account_rate_limit('login', username, 60)
    with pool.connection() as conn:
        u=conn.execute('SELECT id,enabled,role,password_hash,totp_secret,last_totp_step FROM auth_users WHERE username=%s FOR UPDATE',(username,)).fetchone()
        try: ok=ph.verify(u['password_hash'] if u else DUMMY,data.password)
        except (VerifyMismatchError,VerificationError,InvalidHashError): ok=False
        step=None
        if ok and u and u['enabled'] and u['totp_secret'] and data.code:
            step=matching_step(cipher().decrypt(u['totp_secret'].encode()).decode(),data.code,u['last_totp_step'])
        authenticated = ok and (step is not None or is_demo(username, u))
        if not authenticated: audit(conn,username,'login','denied')
        else:
            conn.execute('UPDATE auth_users SET last_totp_step=%s,password_hash=%s WHERE id=%s',
                         (u['last_totp_step'] if step is None else step,
                          ph.hash(data.password) if ph.check_needs_rehash(u['password_hash']) else u['password_hash'],u['id']))
            token, csrf = create_session(conn, request, u['id'])
            audit(conn,username,'login','success')
    if not authenticated: raise HTTPException(401,'Invalid credentials or verification code')
    return signed_in(response, username, u['role'], token, csrf)

def current(request:Request):
    token=request.cookies.get(COOKIE,'')
    if not token or len(token)>128: raise HTTPException(401,'Authentication required')
    with pool.connection() as conn:
        u=conn.execute('''SELECT u.id,u.username,u.role,s.csrf_hash,s.token_hash FROM auth_sessions s JOIN auth_users u ON u.id=s.user_id WHERE s.token_hash=%s AND u.enabled AND s.expires_at>now() AND s.last_seen>now()-interval '30 minutes'
            AND (u.totp_secret<>'' OR (%s AND u.username=%s AND u.role='viewer'))''',(digest(token),LOCAL_ACCOUNTS,DEMO_USERNAME)).fetchone()
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
