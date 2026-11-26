"""Pure, testable ranking policy and content chunking."""
import math

def chunks(subject,body):
    # Bounded character windows, overlap for context. Tokenizer may truncate unusually dense text.
    return [subject+'\n'+body[i:i+1200] for i in range(0,len(body),1000)] or [subject]

def score(semantic,keyword,age_days,urgent,weights):
    parts={'semantic':max(0.,min(1.,semantic)), 'keyword':max(0.,min(1.,keyword)),
           'recency':math.exp(-max(0.,age_days)/30), 'priority':float(urgent)}
    total=sum(weights.values())
    if total<=0: raise ValueError('At least one weight required')
    return sum(parts[k]*v for k,v in weights.items())/total,parts
