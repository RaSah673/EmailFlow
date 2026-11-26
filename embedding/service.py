import os,hmac
from fastapi import FastAPI,Header,HTTPException,Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel,Field
from fastembed import TextEmbedding
from threading import Lock
from typing import Annotated, Literal
MODEL='BAAI/bge-small-en-v1.5'
model=TextEmbedding(model_name=MODEL,cache_dir=os.getenv('MODEL_CACHE_DIR','/models'),threads=2,local_files_only=True)
lock=Lock(); app=FastAPI(docs_url=None,redoc_url=None,openapi_url=None)

@app.middleware('http')
async def limit_body(request: Request, call_next):
    if request.url.path == '/embed' and request.method == 'POST':
        length = request.headers.get('content-length', '')
        if not length.isascii() or not length.isdecimal() or len(length) > 6 or int(length) > 262144:
            return JSONResponse({'detail':'Invalid request size'},status_code=413)
    return await call_next(request)

@app.exception_handler(RequestValidationError)
async def invalid_embedding(request, exc):
    return JSONResponse({'detail':'Invalid embedding fields'},status_code=422)

class Input(BaseModel):
    texts:list[Annotated[str,Field(min_length=1,max_length=2500)]]=Field(min_length=1,max_length=32)
    mode:Literal['query','document']='document'
@app.get('/health')
def health(): return {'ok':True,'model':MODEL}
@app.post('/embed')
def embed(data:Input,authorization:str=Header(default='')):
    if not hmac.compare_digest(authorization.encode(),('Bearer '+os.environ['EMBED_TOKEN']).encode()): raise HTTPException(401,'Unauthorized')
    if any(not t.strip() for t in data.texts): raise HTTPException(422,'Invalid embedding text')
    if not lock.acquire(blocking=False):
        raise HTTPException(503,'Embedding service busy',headers={'Retry-After':'1'})
    try:
        fn=model.query_embed if data.mode=='query' else model.passage_embed
        return {'model':MODEL,'vectors':[v.tolist() for v in fn(data.texts)]}
    finally:
        lock.release()
