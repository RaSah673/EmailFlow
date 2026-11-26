from datetime import datetime

def chunk_ranges(cursor: int, quantity: int, size: int, limit: int=32):
    if not (0 <= cursor <= quantity and size > 0 and limit > 0): raise ValueError('invalid range')
    for start in range(cursor+1,min(quantity,cursor+size*limit)+1,size):
        yield start,min(start+size-1,quantity)

def valid_chunk(start: int,end: int,quantity: int,size: int):
    return 1 <= start <= end <= quantity and (start-1)%size==0 and end==min(start+size-1,quantity)

def delivery_key(campaign: str,seq: int):
    return f'{campaign}.{seq}@emailflow.test'

def require_aware(value: datetime):
    if value.utcoffset() is None: raise ValueError('Timezone offset is required')
    return value
