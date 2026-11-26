import argparse, base64, secrets, getpass, re
from uuid import uuid4
from urllib.parse import quote
from .common import pool
from .security import ph,cipher,audit,normalize

def main():
    p=argparse.ArgumentParser(); p.add_argument('action',choices=['create','reset','disable','enable','revoke','role','list','cleanup']); p.add_argument('username',nargs='?'); p.add_argument('--role',choices=['admin','operator','viewer'],default='viewer'); a=p.parse_args(); pool.open(wait=True,timeout=15)
    with pool.connection() as conn:
        if a.action=='list':
            for row in conn.execute('SELECT username,role,enabled FROM auth_users ORDER BY username'): print(row)
            return
        if a.action=='cleanup':
            conn.execute("DELETE FROM auth_sessions WHERE expires_at<now() OR last_seen<now()-interval '30 minutes'"); conn.execute("DELETE FROM auth_limits WHERE window_start<now()-interval '1 day'"); return
        u=normalize(a.username or '')
        if not re.fullmatch(r'[a-z0-9_.@+-]{3,128}',u): p.error('Valid username required')
        if a.action in ('create','reset'):
            pw=getpass.getpass('Password (minimum 15 characters): '); confirm=getpass.getpass('Confirm password: ')
            if pw!=confirm or not 15<=len(pw)<=256: p.error('Passwords differ or length invalid')
            secret=base64.b32encode(secrets.token_bytes(20)).decode().rstrip('='); encrypted=cipher().encrypt(secret.encode()).decode()
            if a.action=='create': conn.execute('INSERT INTO auth_users(id,username,password_hash,totp_secret,role) VALUES (%s,%s,%s,%s,%s)',(uuid4(),u,ph.hash(pw),encrypted,a.role))
            else:
                if not conn.execute('UPDATE auth_users SET password_hash=%s,totp_secret=%s,last_totp_step=-1 WHERE username=%s RETURNING id',(ph.hash(pw),encrypted,u)).fetchone(): p.error('Unknown user')
            conn.execute('DELETE FROM auth_sessions WHERE user_id IN (SELECT id FROM auth_users WHERE username=%s)',(u,)); audit(conn,'cluster-admin',a.action,'success',u)
            conn.commit(); print('Enroll this secret in your authenticator app (time-based, 6 digits, 30 seconds):'); print(secret); print('otpauth://totp/EmailFlow:'+quote(u,safe='')+'?secret='+secret+'&issuer=EmailFlow'); print('Keep this private. It will not be displayed again.'); return
        if not conn.execute('SELECT id FROM auth_users WHERE username=%s FOR UPDATE',(u,)).fetchone(): p.error('Unknown user')
        if a.action in ('disable','enable'): conn.execute('UPDATE auth_users SET enabled=%s WHERE username=%s',(a.action=='enable',u))
        if a.action=='role': conn.execute('UPDATE auth_users SET role=%s WHERE username=%s',(a.role,u))
        conn.execute('DELETE FROM auth_sessions WHERE user_id IN (SELECT id FROM auth_users WHERE username=%s)',(u,)); audit(conn,'cluster-admin',a.action,'success',u)
if __name__=='__main__': main()
