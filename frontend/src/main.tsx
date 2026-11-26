import SignIn from './SignIn';
import Archive from './Search';
import { api, setCsrf, type User } from './session';
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import {
  Activity,
  ArrowUpRight,
  Boxes,
  CalendarClock,
  CheckCircle2,
  ChevronRight,
  Clock,
  FlaskConical,
  Inbox,
  LayoutDashboard,
  Mail,
  Plus,
  Radio,
  RefreshCw,
  Send,
  ShieldCheck,
  Zap,
} from 'lucide-react';
import './style.css';
type Campaign = {
  id: string;
  name: string;
  subject: string;
  quantity: number;
  priority: string;
  scheduled_at: string;
  status: string;
  published: number;
  accepted: number;
  completed: number;
  fault: string;
  mirror_limit: number;
};
type Summary = {
  campaigns: number;
  total: number;
  published: number;
  accepted: number;
  completed: number;
  scheduled: number;
  urgent_remaining: number;
};
type Incident = {
  id: number;
  campaign_id: string;
  service: string;
  kind: string;
  severity: string;
  detail: string;
  trace_id: string | null;
  created_at: string;
  resolved_at: string | null;
};
type Infra = {
  workers: { name: string; desired?: number; ready?: number; lag?: number; partitions?: number }[];
  kubernetes_error: string | null;
  kafka_error: string | null;
  logs: string | null;
};
type Receipt = {
  seq: number;
  accepted_at: string;
  delivery_key: string;
  mirror_status: string | null;
};
type Trace = { trace_id: string; spans: number; operation: string };
const fmt = (n: number | undefined) => (n === undefined ? '—' : n.toLocaleString());
const date = (s: string) => new Date(s).toLocaleString();
const local = (d: Date) =>
  new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
function newCampaign() {
  return {
    name: '',
    subject: 'Service notification',
    body: 'This is a simulated EmailFlow message.',
    quantity: 1000,
    priority: 'standard',
    scheduled_at: local(new Date(Date.now() + 120000)),
    chunk_size: 100,
    mirror_limit: 5,
    fault: 'none',
  };
}
const pageSize = 50;
function App({ user, onLogout }: { user: User; onLogout: () => void }) {
  const canWrite = user.role !== 'viewer';
  const [page, setPage] = useState('Overview'),
    [campaigns, setCampaigns] = useState<Campaign[]>([]),
    [summary, setSummary] = useState<Summary>(),
    [incidents, setIncidents] = useState<Incident[]>([]),
    [infra, setInfra] = useState<Infra>(),
    [traces, setTraces] = useState<Trace[]>([]),
    [error, setError] = useState(''),
    [modal, setModal] = useState(false),
    [selected, setSelected] = useState<Campaign | null>(null),
    [receipts, setReceipts] = useState<Receipt[]>([]),
    [after, setAfter] = useState(0),
    [filter, setFilter] = useState('all');
  const [form, setForm] = useState(newCampaign);
  const [busy, setBusy] = useState(false);
  const [cursors, setCursors] = useState<string[]>([]);
  const [hasNext, setHasNext] = useState(false);
  const [loadingCampaigns, setLoadingCampaigns] = useState(true);
  const [refreshError, setRefreshError] = useState('');
  const inFlight = useRef<AbortController>();
  const cursor = cursors.at(-1);
  const refresh = useCallback(async () => {
    inFlight.current?.abort();
    const request = new AbortController();
    inFlight.current = request;
    const options = { signal: request.signal };
    const query = new URLSearchParams({ limit: String(pageSize + 1), priority: filter });
    if (cursor) query.set('before', cursor);
    try {
      const [rows, totals, events] = await Promise.all([
        api<Campaign[]>('/campaigns?' + query, options),
        api<Summary>('/summary', options),
        user.role === 'admin'
          ? api<Incident[]>('/incidents', options)
          : Promise.resolve([] as Incident[]),
      ]);
      if (!request.signal.aborted) {
        setCampaigns(rows.slice(0, pageSize));
        setHasNext(rows.length > pageSize);
        setSummary(totals);
        setIncidents(events);
        setRefreshError('');
      }
    } catch (failure) {
      if (!request.signal.aborted) setRefreshError(String(failure));
    } finally {
      if (!request.signal.aborted) setLoadingCampaigns(false);
    }
  }, [cursor, filter, user.role]);
  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      await refresh();
      if (active) timer = setTimeout(poll, 3000);
    }
    setLoadingCampaigns(true);
    void poll();
    return () => {
      active = false;
      clearTimeout(timer);
      inFlight.current?.abort();
    };
  }, [refresh]);
  useEffect(() => {
    if (page !== 'Observability') return;
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    async function load() {
      try {
        const [i, t] = await Promise.all([api<Infra>('/infra'), api<Trace[]>('/traces')]);
        if (active) {
          setInfra(i);
          setTraces(t);
        }
      } catch (e) {
        if (active) setError(String(e));
      }
      if (active) timer = setTimeout(load, 5000);
    }
    void load();
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [page]);
  useEffect(() => {
    if (!selected) return;
    let active = true;
    async function load() {
      try {
        const r = await api<Receipt[]>(`/campaigns/${selected!.id}/receipts?after=${after}`);
        if (active) setReceipts(r);
      } catch (e) {
        if (active) setError(String(e));
      }
    }
    void load();
    const t = setInterval(load, 3000);
    return () => {
      active = false;
      clearInterval(t);
    };
  }, [selected?.id, after]);
  const current = campaigns.find((c) => c.id === selected?.id) || selected;
  const update = (key: string, value: string | number) => setForm((f) => ({ ...f, [key]: value }));
  function openCampaign() {
    setForm(newCampaign());
    setError('');
    setModal(true);
  }
  function preset(n: number, fault = 'none') {
    setForm({
      ...newCampaign(),
      name:
        fault === 'none'
          ? `${fmt(n)} urgent simulation`
          : fault === 'gateway_after_accept'
            ? 'Gateway crash recovery'
            : 'Worker crash recovery',
      quantity: n,
      priority: 'urgent',
      fault,
      scheduled_at: local(new Date(Date.now() + 60000)),
    });
    setError('');
    setModal(true);
  }
  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setError('');
    setBusy(true);
    try {
      await api('/campaigns', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...form, scheduled_at: new Date(form.scheduled_at).toISOString() }),
      });
      setModal(false);
      setPage('Campaigns');
      if (cursor || filter !== 'all') {
        setCursors([]);
        setFilter('all');
      } else await refresh();
    } catch (failure) {
      setError(String(failure));
    } finally {
      setBusy(false);
    }
  }
  const links = [
    ['Overview', LayoutDashboard],
    ['Campaigns', CalendarClock],
    ['Simulation lab', FlaskConical],
    ['Email search', Inbox],
    ['Observability', Activity],
    ['Security', ShieldCheck],
  ] as const;
  function table() {
    return (
      <div className="panel tablepanel">
        <div className="panelhead">
          <div>
            <h2>Campaigns</h2>
            <p>Scheduled and active delivery workloads</p>
          </div>
          <select
            aria-label="Priority filter"
            value={filter}
            onChange={(e) => {
              setFilter(e.target.value);
              setCursors([]);
            }}
          >
            <option value="all">All priorities</option>
            <option value="urgent">Urgent</option>
            <option value="standard">Standard</option>
          </select>
        </div>
        <div className="tablewrap">
          <table>
            <thead>
              <tr>
                <th>Campaign</th>
                <th>Priority</th>
                <th>Scheduled</th>
                <th>Delivery progress</th>
                <th>Status</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {campaigns.map((c) => (
                <tr
                  key={c.id}
                  onClick={() => {
                    setAfter(0);
                    setReceipts([]);
                    setSelected(c);
                  }}
                >
                  <td>
                    <strong>{c.name}</strong>
                    <small>{fmt(c.quantity)} emails</small>
                  </td>
                  <td>
                    <span className={'tag ' + c.priority}>
                      {c.priority === 'urgent' && <Zap size={12} />} {c.priority}
                    </span>
                  </td>
                  <td>{date(c.scheduled_at)}</td>
                  <td>
                    <div className="progress">
                      <i style={{ width: `${(c.completed / c.quantity) * 100}%` }} />
                    </div>
                    <small>
                      {fmt(c.completed)} / {fmt(c.quantity)}
                    </small>
                  </td>
                  <td>
                    <span className={'tag ' + c.status}>{c.status}</span>
                  </td>
                  <td>
                    <button aria-label={`View ${c.name}`} className="iconbtn">
                      <ChevronRight size={17} />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {loadingCampaigns && <p role="status">Loading campaigns...</p>}
          {!loadingCampaigns && campaigns.length === 0 && (
            <div className="empty">
              <Inbox size={32} />
              <h3>No campaigns yet</h3>
              <p>Create a campaign or run a simulation.</p>
              {canWrite && <button onClick={openCampaign}>Create campaign</button>}
            </div>
          )}
        </div>
        {(cursors.length > 0 || hasNext) && (
          <div className="actions">
            <button
              disabled={loadingCampaigns || cursors.length === 0}
              onClick={() => setCursors((items) => items.slice(0, -1))}
            >
              Previous page
            </button>
            <button
              disabled={loadingCampaigns || !hasNext}
              onClick={() => {
                const last = campaigns.at(-1);
                if (last) setCursors((items) => [...items, last.id]);
              }}
            >
              Next page
            </button>
          </div>
        )}
      </div>
    );
  }
  return (
    <div className="shell">
      <aside>
        <div className="brand">
          <span className="brandicon">
            <Send size={22} />
          </span>
          <div>
            EmailFlow<small>DELIVERY OPERATIONS</small>
          </div>
        </div>
        <div className="navlabel">WORKSPACE</div>
        {links
          .filter(
            ([name]) =>
              user.role === 'admin' ||
              !['Observability', 'Simulation lab', 'Security'].includes(name),
          )
          .map(([name, Icon]) => (
            <button
              key={name}
              className={'nav ' + (page === name ? 'active' : '')}
              onClick={() => {
                setPage(name);
                setSelected(null);
              }}
            >
              <Icon size={19} />
              {name}
            </button>
          ))}
        {user.role === 'admin' && (
          <>
            <div className="navlabel tools">LOCAL TOOLS</div>
            <a className="nav" href="http://localhost:1080" target="_blank" rel="noreferrer">
              <Mail size={19} />
              MailCatcher
              <ArrowUpRight size={14} />
            </a>
            <a className="nav" href="http://localhost:16686" target="_blank" rel="noreferrer">
              <Radio size={19} />
              Jaeger
              <ArrowUpRight size={14} />
            </a>
          </>
        )}
        <div className="navlabel tools">ACCOUNT</div>
        <div className="account-label">
          {user.username} · {user.role}
        </div>
        <button className="nav" onClick={onLogout}>
          Sign out
        </button>
        <div className="sidefoot">
          <span className="dot" />
          Local simulation<span>Safe · no external email</span>
        </div>
      </aside>
      <main>
        <header>
          <div className="crumb">
            Workspace <ChevronRight size={14} /> {page}
          </div>
          <div className="headeractions">
            <span className="live">
              <span className="dot" /> Live updates
            </span>
            <button className="iconbtn" aria-label="Refresh" onClick={refresh}>
              <RefreshCw size={18} />
            </button>
            {canWrite && (
              <button className="primary" onClick={openCampaign}>
                <Plus size={18} /> New campaign
              </button>
            )}
          </div>
        </header>
        <div className="content">
          {refreshError && (
            <p role="alert" className="error">
              Dashboard refresh failed. Showing the last loaded data. {refreshError}
            </p>
          )}
          {error && (
            <div role="alert" className="error">
              {error}
              <button onClick={() => setError('')}>Dismiss</button>
            </div>
          )}
          <div className="intro">
            <div>
              <div className="eyebrow">EMAILFLOW / CONTROL CENTER</div>
              <h1>{page}</h1>
              <p>
                {
                  (
                    {
                      Overview: 'Monitor scheduled delivery, throughput and urgent workloads.',
                      Campaigns: 'Create, schedule and inspect email delivery campaigns.',
                      'Simulation lab':
                        'Reproduce failures and test recovery using synthetic recipients.',
                      Observability:
                        'Inspect live infrastructure, incidents and distributed traces.',
                      'Email search': 'Find archived emails and tune the ranking policy.',
                      Security: 'Review access events and administrative audit history.',
                    } as Record<string, string>
                  )[page]
                }
              </p>
            </div>
            <span className="environment">
              <ShieldCheck size={15} /> Sandbox environment
            </span>
          </div>
          {page === 'Overview' && (
            <>
              <div className="stats">
                {[
                  [Mail, 'Scheduled volume', summary?.total, 'All non-cancelled campaigns'],
                  [CheckCircle2, 'Durably accepted', summary?.accepted, 'Unique gateway receipts'],
                  [Send, 'Completed', summary?.completed, 'Worker-confirmed deliveries'],
                  [Zap, 'Urgent remaining', summary?.urgent_remaining, 'Due urgent workload'],
                ].map(([Icon, label, n, sub], i) => {
                  const I = Icon as typeof Mail;
                  return (
                    <div key={i} className="stat">
                      <div className="stat-top">
                        <span>{label as string}</span>
                        <I size={19} />
                      </div>
                      <b>{fmt(n as number | undefined)}</b>
                      <small>{sub as string}</small>
                    </div>
                  );
                })}
              </div>
              <div className="panel pipeline">
                <div className="panelhead">
                  <div>
                    <h2>Delivery pipeline</h2>
                    <p>Durable state across all campaigns</p>
                  </div>
                  <span className="tag standard">Live data</span>
                </div>
                <div className="stages">
                  {[
                    ['Queued to Kafka', summary?.published],
                    ['Accepted by gateway', summary?.accepted],
                    ['Worker completed', summary?.completed],
                  ].map(([label, n], i) => (
                    <div key={i}>
                      <small>{label}</small>
                      <b>{fmt(n as number | undefined)}</b>
                      <div className="progress">
                        <i
                          style={{
                            width: `${Math.min(100, (Number(n || 0) / Math.max(1, summary?.total || 0)) * 100)}%`,
                          }}
                        />
                      </div>
                    </div>
                  ))}
                </div>
              </div>
              {table()}
            </>
          )}
          {page === 'Email search' && <Archive />} {page === 'Security' && <SecurityAudit />}
          {page === 'Campaigns' && table()}
          {page === 'Simulation lab' && (
            <>
              <div className="labgrid">
                {[
                  {
                    title: 'Urgent burst',
                    desc: 'Release one million synthetic emails from a single timestamp. Observe Kafka lag and KEDA worker scaling.',
                    n: 1000000,
                    f: 'none',
                    Icon: Zap,
                  },
                  {
                    title: 'Gateway crashes after accept',
                    desc: 'Commit durable receipts, terminate the gateway before acknowledgement, then retry without duplicate receipts.',
                    n: 1000,
                    f: 'gateway_after_accept',
                    Icon: Boxes,
                  },
                  {
                    title: 'Worker crashes after ACK',
                    desc: 'Receive the gateway response, terminate the worker before checkpointing, then replay the Kafka task.',
                    n: 1000,
                    f: 'worker_after_ack',
                    Icon: Activity,
                  },
                ].map((x) => (
                  <div className="panel lab" key={x.title}>
                    <x.Icon size={25} />
                    <h2>{x.title}</h2>
                    <p>{x.desc}</p>
                    <button className="primary" onClick={() => preset(x.n, x.f)}>
                      Configure simulation <ChevronRight size={16} />
                    </button>
                  </div>
                ))}
              </div>
              <div className="panel note">
                <h2>Delivery semantics</h2>
                <p>
                  Every simulated email has a unique receipt. MailCatcher receives only the selected
                  number of sample emails. SMTP uncertainty is reported separately, never silently
                  retried.
                </p>
              </div>
              {table()}
            </>
          )}
          {page === 'Observability' && (
            <>
              <div className="panel">
                <div className="panelhead">
                  <div>
                    <h2>Infrastructure & queue lag</h2>
                    <p>Actual Kubernetes deployments and Kafka consumer offsets</p>
                  </div>
                  <span className="tag standard">Polling</span>
                </div>
                {infra?.kubernetes_error && <p className="muted">{infra.kubernetes_error}</p>}
                {infra?.kafka_error && <p className="muted">Kafka: {infra.kafka_error}</p>}
                <div className="labgrid infra">
                  {infra?.workers.map((w) => (
                    <div className="stat" key={w.name}>
                      <small>{w.name}</small>
                      <b>{w.lag !== undefined ? fmt(w.lag) : `${w.ready} / ${w.desired}`}</b>
                      <small>
                        {w.lag !== undefined
                          ? `Kafka tasks pending · ${w.partitions} partitions`
                          : 'Ready / desired replicas'}
                      </small>
                    </div>
                  )) || <p>Loading infrastructure…</p>}
                </div>
              </div>
              <div className="panel">
                <div className="panelhead">
                  <div>
                    <h2>Incident timeline</h2>
                    <p>Durable, trace-correlated failure and recovery records</p>
                  </div>
                  <span className="tag urgent">
                    {incidents.filter((i) => !i.resolved_at).length} open
                  </span>
                </div>
                {incidents.length === 0 && <p className="muted">No incidents recorded.</p>}
                {incidents.map((i) => (
                  <div className="incident" key={i.id}>
                    <div>
                      <strong>{i.kind.replaceAll('_', ' ')}</strong>
                      <p>{i.detail}</p>
                      <small>
                        {i.service} · {date(i.created_at)}
                      </small>
                    </div>
                    <div>
                      <span className={'tag ' + (i.resolved_at ? 'completed' : 'urgent')}>
                        {i.resolved_at ? 'Recovered' : 'Open'}
                      </span>
                      {i.trace_id && (
                        <a
                          href={`http://localhost:16686/trace/${i.trace_id}`}
                          target="_blank"
                          rel="noreferrer"
                        >
                          Trace <ArrowUpRight size={13} />
                        </a>
                      )}
                    </div>
                  </div>
                ))}
              </div>
              <div className="panel">
                <div className="panelhead">
                  <div>
                    <h2>Recent worker traces</h2>
                    <p>OpenTelemetry spans in Jaeger</p>
                  </div>
                  <a href="http://localhost:16686" target="_blank" rel="noreferrer">
                    Open Jaeger ↗
                  </a>
                </div>
                {traces.map((t) => (
                  <div className="incident" key={t.trace_id}>
                    <span>
                      {t.operation} · {t.spans} spans
                    </span>
                    <a
                      href={`http://localhost:16686/trace/${t.trace_id}`}
                      target="_blank"
                      rel="noreferrer"
                    >
                      {t.trace_id.slice(0, 12)} ↗
                    </a>
                  </div>
                ))}
                {!traces.length && <p className="muted">No worker traces returned yet.</p>}
              </div>
              <div className="panel">
                <h2>Collector log tail</h2>
                <p>Recent OpenTelemetry collector output (Kubernetes only).</p>
                <pre>{infra?.logs || 'No collector logs available.'}</pre>
              </div>
            </>
          )}
        </div>
      </main>
      {modal && (
        <div className="overlay">
          <form className="dialog" onSubmit={submit}>
            <div className="panelhead">
              <div>
                <h2>New email campaign</h2>
                <p>All recipients are generated locally.</p>
              </div>
              <button type="button" className="iconbtn" onClick={() => setModal(false)}>
                ✕
              </button>
            </div>
            <label>
              Campaign name
              <input
                required
                maxLength={120}
                value={form.name}
                onChange={(e) => update('name', e.target.value)}
              />
            </label>
            <label>
              Subject
              <input
                required
                maxLength={200}
                value={form.subject}
                onChange={(e) => update('subject', e.target.value)}
              />
            </label>
            <label>
              Email body
              <textarea
                required
                value={form.body}
                onChange={(e) => update('body', e.target.value)}
              />
            </label>
            <div className="fields">
              <label>
                Number of emails
                <input
                  type="number"
                  required
                  min={1}
                  max={10000000}
                  value={form.quantity}
                  onChange={(e) => update('quantity', Number(e.target.value))}
                />
              </label>
              <label>
                Priority
                <select value={form.priority} onChange={(e) => update('priority', e.target.value)}>
                  <option value="standard">Standard</option>
                  <option value="urgent">Urgent</option>
                </select>
              </label>
              <label>
                Send at (local time)
                <input
                  type="datetime-local"
                  required
                  value={form.scheduled_at}
                  onChange={(e) => update('scheduled_at', e.target.value)}
                />
              </label>
              <label>
                Emails per Kafka task
                <input
                  type="number"
                  min={1}
                  max={1000}
                  required
                  value={form.chunk_size}
                  onChange={(e) => update('chunk_size', Number(e.target.value))}
                />
              </label>
              <label>
                MailCatcher sample count
                <input
                  type="number"
                  min={0}
                  max={100}
                  required
                  value={form.mirror_limit}
                  onChange={(e) => update('mirror_limit', Number(e.target.value))}
                />
              </label>
              <label>
                Failure simulation
                <select value={form.fault} onChange={(e) => update('fault', e.target.value)}>
                  <option value="none">None</option>
                  {user.role === 'admin' && (
                    <>
                      <option value="gateway_after_accept">Gateway after accept</option>
                      <option value="worker_after_ack">Worker after ACK</option>
                    </>
                  )}
                </select>
              </label>
            </div>
            <div className="note">
              <Clock size={16} /> {fmt(Math.ceil(form.quantity / Math.max(1, form.chunk_size)))}{' '}
              Kafka tasks · one schedule record
            </div>
            {error && <p className="error">{error}</p>}
            <div className="actions">
              <button type="button" onClick={() => setModal(false)}>
                Cancel
              </button>
              <button className="primary" disabled={busy}>
                {busy ? 'Scheduling…' : 'Schedule campaign'}
              </button>
            </div>
          </form>
        </div>
      )}
      {current && (
        <div className="overlay">
          <section className="dialog detail">
            <div className="panelhead">
              <div>
                <h2>{current.name}</h2>
                <p>{current.id}</p>
              </div>
              <button className="iconbtn" onClick={() => setSelected(null)}>
                ✕
              </button>
            </div>
            <div className="stats detailstats">
              {[
                ['Published', current.published],
                ['Accepted', current.accepted],
                ['Completed', current.completed],
              ].map(([l, n]) => (
                <div className="stat" key={l}>
                  <small>{l}</small>
                  <b>{fmt(n as number)}</b>
                </div>
              ))}
            </div>
            <p>
              Scheduled: {date(current.scheduled_at)} · {current.status} · {current.priority}
            </p>
            <p>
              Fault: {current.fault} · MailCatcher samples: {current.mirror_limit}
            </p>
            {canWrite && current.status === 'scheduled' && (
              <button
                onClick={async () => {
                  try {
                    await api(`/campaigns/${current.id}/cancel`, { method: 'POST' });
                    await refresh();
                  } catch (e) {
                    setError(String(e));
                  }
                }}
              >
                Cancel scheduled campaign
              </button>
            )}
            <h2>Unique delivery receipts</h2>
            <div className="tablewrap">
              <table>
                <thead>
                  <tr>
                    <th>Recipient</th>
                    <th>Accepted at</th>
                    <th>SMTP sample</th>
                  </tr>
                </thead>
                <tbody>
                  {receipts.map((r) => (
                    <tr key={r.seq}>
                      <td>
                        #{r.seq}
                        <small>{r.delivery_key}</small>
                      </td>
                      <td>{date(r.accepted_at)}</td>
                      <td>{r.mirror_status || 'Not sampled'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {!receipts.length && <p>No receipts on this page yet.</p>}
            <div className="actions">
              <button onClick={() => setAfter(0)}>First page</button>
              <button
                disabled={receipts.length < 30}
                onClick={() => setAfter(receipts.at(-1)!.seq)}
              >
                Next page
              </button>
            </div>
          </section>
        </div>
      )}
    </div>
  );
}
function SecurityAudit() {
  const [rows, setRows] = useState<
      {
        id: number;
        actor: string;
        action: string;
        outcome: string;
        target: string | null;
        created_at: string;
      }[]
    >([]),
    [err, setErr] = useState('');
  useEffect(() => {
    let active = true;
    async function load() {
      try {
        const r = await api<typeof rows>('/auth/audit');
        if (active) setRows(r);
      } catch (e) {
        if (active) setErr(String(e));
      }
    }
    void load();
    const t = setInterval(load, 5000);
    return () => {
      active = false;
      clearInterval(t);
    };
  }, []);
  return (
    <div className="panel">
      <h2>Security audit events</h2>
      <p>
        Administrative actions, sign-ins, sign-outs and archive search activity. Latest 100 events.
      </p>
      {err && <p className="error">{err}</p>}
      <div className="tablewrap">
        <table>
          <thead>
            <tr>
              <th>Time</th>
              <th>Actor</th>
              <th>Action</th>
              <th>Outcome</th>
              <th>Target</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id}>
                <td>{date(r.created_at)}</td>
                <td>{r.actor}</td>
                <td>{r.action}</td>
                <td>{r.outcome}</td>
                <td>{r.target || '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
function Root() {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  function applySession(session: User | null) {
    setCsrf(session?.csrf ?? '');
    setUser(session);
  }
  useEffect(() => {
    const request = new AbortController();
    const expired = () => applySession(null);
    window.addEventListener('session-expired', expired);
    api<User>('/auth/me', { signal: request.signal })
      .then(applySession)
      .catch(() => {
        if (!request.signal.aborted) expired();
      })
      .finally(() => {
        if (!request.signal.aborted) setLoading(false);
      });
    return () => {
      request.abort();
      window.removeEventListener('session-expired', expired);
    };
  }, []);

  async function logout() {
    setError('');
    try {
      await api('/auth/logout', { method: 'POST' });
      applySession(null);
    } catch (failure) {
      setError(String(failure));
    }
  }
  if (loading)
    return (
      <div className="access-loading" role="status">
        Loading session...
      </div>
    );
  if (user)
    return (
      <>
        {error && (
          <div role="alert" className="error">
            {error}
          </div>
        )}
        <App user={user} onLogout={() => void logout()} />
      </>
    );
  return <SignIn onSignIn={applySession} />;
}
createRoot(document.getElementById('root')!).render(<Root />);
