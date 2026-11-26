import React, { useEffect, useState } from 'react';
import { ChevronRight, Database, Search as SearchIcon, SlidersHorizontal } from 'lucide-react';
import { api } from './session';

type Hit = {
  id: string; name: string; subject: string; preview: string; truncated: boolean; priority: string;
  scheduled_at: string; accepted: number; score: number; components: Record<string, number>;
  contributions: Record<string, number>; receipts: { seq: number }[];
};
type SearchResult = { results: Hit[]; candidate_count: number; model: string; semantic_available: boolean };
type ArchiveStatus = { indexed: number; pending: number; searchable_emails: number };
type Receipt = { seq: number; accepted_at: string; delivery_key: string };
const defaults = { semantic: .6, keyword: .25, recency: .1, priority_weight: .05 };
const weightLabels = { semantic: 'Semantic similarity', keyword: 'Keyword match', recency: 'Recency', priority_weight: 'Urgency' };
const componentLabels: Record<string, string> = { ...weightLabels, priority: 'Urgency' };

export default function Archive() {
  const [query, setQuery] = useState('');
  const [priority, setPriority] = useState('all');
  const [recipient, setRecipient] = useState('');
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');
  const [weights, setWeights] = useState(defaults);
  const [result, setResult] = useState<SearchResult>();
  const [searchedRecipient, setSearchedRecipient] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [status, setStatus] = useState<ArchiveStatus>();
  const [statusError, setStatusError] = useState('');
  const [selected, setSelected] = useState<Hit>();
  const [body, setBody] = useState<string>();
  const [receipts, setReceipts] = useState<Receipt[]>();
  const [detailError, setDetailError] = useState('');
  const [after, setAfter] = useState(0);
  const selectedId = selected?.id;

  useEffect(() => {
    const request = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function load() {
      try {
        const value = await api<ArchiveStatus>('/archive/status', { signal: request.signal });
        if (!request.signal.aborted) { setStatus(value); setStatusError(''); }
      } catch (failure) {
        if (!request.signal.aborted) setStatusError(String(failure));
      }
      if (!request.signal.aborted) timer = setTimeout(load, 10000);
    }
    void load();
    return () => { request.abort(); clearTimeout(timer); };
  }, []);

  useEffect(() => {
    if (!selectedId) return;
    const request = new AbortController();
    setBody(undefined);
    api<{ body: string }>(`/archive/documents/${selectedId}`, { signal: request.signal })
      .then(value => setBody(value.body))
      .catch(failure => { if (!request.signal.aborted) setDetailError(String(failure)); });
    return () => request.abort();
  }, [selectedId]);

  useEffect(() => {
    if (!selectedId) return;
    const request = new AbortController();
    setReceipts(undefined);
    const cursor = searchedRecipient ? searchedRecipient - 1 : after;
    api<Receipt[]>(`/campaigns/${selectedId}/receipts?after=${cursor}&limit=${searchedRecipient ? 1 : 30}`, { signal: request.signal })
      .then(rows => setReceipts(searchedRecipient ? rows.filter(row => row.seq === searchedRecipient) : rows))
      .catch(failure => { if (!request.signal.aborted) setDetailError(String(failure)); });
    return () => request.abort();
  }, [selectedId, after, searchedRecipient]);

  async function search(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError('');
    setResult(undefined);
    setSelected(undefined);
    try {
      const selectedRecipient = recipient ? Number(recipient) : null;
      const response = await api<SearchResult>('/archive/search', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query, priority, recipient: selectedRecipient, ...weights,
          scheduled_from: from ? new Date(from).toISOString() : null,
          scheduled_to: to ? new Date(to).toISOString() : null }),
      });
      setSearchedRecipient(selectedRecipient);
      setResult(response);
    } catch (failure) { setError(String(failure)); }
    finally { setBusy(false); }
  }

  const total = Object.values(weights).reduce((sum, value) => sum + value, 0);
  return <>
    <div className="stats archive-stats">
      <div className="stat"><small>Indexed content groups</small><b>{status?.indexed.toLocaleString() ?? '—'}</b></div>
      <div className="stat"><small>Searchable delivered emails</small><b>{status?.searchable_emails.toLocaleString() ?? '—'}</b></div>
      <div className="stat"><small>Awaiting indexing</small><b>{status?.pending.toLocaleString() ?? '—'}</b></div>
    </div>
    {statusError && <p className="error" role="alert">Archive status unavailable. {statusError}</p>}
    <form className="panel archive-form" onSubmit={search}>
      <div className="panelhead"><div><h2><SearchIcon size={20}/> Find emails</h2><p>Search archived message content and adjust how results rank.</p></div><Database size={22}/></div>
      <label>Search message content<input required maxLength={1000} placeholder="e.g. payment due or service interruption" value={query} onChange={e => setQuery(e.target.value)}/></label>
      <div className="fields">
        <label>Priority<select value={priority} onChange={e => setPriority(e.target.value)}><option value="all">All</option><option value="urgent">Urgent</option><option value="standard">Standard</option></select></label>
        <label>Recipient number<input type="number" min={1} max={10000000} value={recipient} onChange={e => setRecipient(e.target.value)}/></label>
        <label>Scheduled from<input type="datetime-local" value={from} onChange={e => setFrom(e.target.value)}/></label>
        <label>Scheduled to<input type="datetime-local" value={to} onChange={e => setTo(e.target.value)}/></label>
      </div>
      <div className="panelhead"><h2><SlidersHorizontal size={18}/> Ranking weights</h2><button type="button" onClick={() => setWeights(defaults)}>Reset weights</button></div>
      <div className="fields">{(Object.keys(weights) as (keyof typeof weights)[]).map(key =>
        <label key={key}>{weightLabels[key]} · {total ? Math.round(weights[key] / total * 100) : 0}%
          <input type="range" min={0} max={1} step={.05} value={weights[key]} onChange={e => setWeights(previous => ({ ...previous, [key]: Number(e.target.value) }))}/>
        </label>)}</div>
      <button className="primary" disabled={busy || !total}>{busy ? 'Searching…' : 'Search archive'}</button>
      {!total && <p role="status">Set at least one ranking weight above zero.</p>}
    </form>
    {error && <p className="error" role="alert">{error}</p>}
    {result && <div className="panel">
      <div className="panelhead"><div><h2>Ranked email groups</h2><p>{result.results.length} results · {result.candidate_count} candidate groups · {result.model}</p></div></div>
      {!result.semantic_available && <p role="status">Semantic matching is unavailable. Results use the remaining ranking weights.</p>}
      {!result.results.length && <p>No indexed matches. Check filters or wait for indexing.</p>}
      {result.results.map((hit, index) => <article key={hit.id} className="archive-hit">
        <div className="panelhead"><div><span className="tag standard">#{index + 1}</span> <span className={'tag ' + hit.priority}>{hit.priority}</span>
          <h2>{hit.subject}</h2><p>{hit.name} · {hit.accepted.toLocaleString()} accepted recipients · {new Date(hit.scheduled_at).toLocaleString()}</p></div>
          <div className="rank-score"><b>{(hit.score * 100).toFixed(1)}</b><small>ranking score</small></div></div>
        <p className="archive-preview">{hit.preview}{hit.truncated ? '…' : ''}</p>
        <div className="score-grid">{Object.entries(hit.contributions).map(([key, value]) => <div key={key}>
          <small>{componentLabels[key]} · {(value * 100).toFixed(1)} points</small>
          <div className="progress"><i style={{ width: `${value * 100}%` }}/></div>
        </div>)}</div>
        <p className="muted">{hit.receipts.map(item => `recipient-${item.seq}@emailflow.test`).join(' · ')}</p>
        <button onClick={() => { setAfter(0);setBody(undefined);setReceipts(undefined);setDetailError('');setSelected(hit); }}>Inspect email and receipts <ChevronRight size={15}/></button>
      </article>)}
    </div>}
    {selected && <div className="overlay"><section className="dialog detail" role="dialog" aria-modal="true" aria-label="Archived email"
      onKeyDown={event => { if (event.key === 'Escape') setSelected(undefined); }}>
      <div className="panelhead"><h2>{selected.subject}</h2><button autoFocus onClick={() => setSelected(undefined)}>Close</button></div>
      {detailError && <p className="error" role="alert">{detailError}</p>}
      {body !== undefined ? <pre className="email-body">{body}</pre> : !detailError && <p role="status">Loading message…</p>}
      <h2>Individual accepted emails</h2>
      {receipts?.map(receipt => <div className="incident" key={receipt.seq}>
        <div>recipient-{receipt.seq}@emailflow.test<small>{receipt.delivery_key}</small></div><small>{new Date(receipt.accepted_at).toLocaleString()}</small>
      </div>)}
      {receipts?.length === 0 && <p>No receipts on this page.</p>}
      {receipts === undefined && !detailError && <p role="status">Loading receipts…</p>}
      {!searchedRecipient && <div className="actions"><button disabled={!after} onClick={() => setAfter(0)}>First page</button>
        <button disabled={(receipts?.length ?? 0) < 30} onClick={() => setAfter(receipts?.at(-1)?.seq ?? 0)}>Next page</button></div>}
    </section></div>}
  </>;
}
