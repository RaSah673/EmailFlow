import time,base64,struct,hmac,hashlib
def totp(secret,step):
    key=base64.b32decode(secret+'='*((-len(secret))%8)); mac=hmac.new(key,struct.pack('>Q',step),hashlib.sha1).digest(); offset=mac[-1]&15
    return f'{(struct.unpack(">I",mac[offset:offset+4])[0]&0x7fffffff)%1000000:06d}'
def matching_step(secret,code,last,now=None):
    step=int((time.time() if now is None else now)//30)
    for i in (step,step-1,step+1):
        if i>=0 and i>last and hmac.compare_digest(totp(secret,i),code): return i
    return None
