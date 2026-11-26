CREATE TABLE IF NOT EXISTS campaigns (
 id uuid PRIMARY KEY, name text NOT NULL, subject text NOT NULL, body text NOT NULL,
 quantity integer NOT NULL CHECK(quantity BETWEEN 1 AND 10000000),
 priority text NOT NULL CHECK(priority IN ('standard','urgent')),
 scheduled_at timestamptz NOT NULL, chunk_size integer NOT NULL CHECK(chunk_size BETWEEN 1 AND 1000),
 mirror_limit integer NOT NULL CHECK(mirror_limit BETWEEN 0 AND 100),
 fault text NOT NULL CHECK(fault IN ('none','gateway_after_accept','worker_after_ack')),
 created_at timestamptz NOT NULL DEFAULT now(), published integer NOT NULL DEFAULT 0,
 accepted integer NOT NULL DEFAULT 0, completed integer NOT NULL DEFAULT 0,
 status text NOT NULL DEFAULT 'scheduled' CHECK(status IN ('scheduled','dispatching','queued','completed','cancelled')),
 CHECK(published BETWEEN 0 AND quantity), CHECK(accepted BETWEEN 0 AND quantity), CHECK(completed BETWEEN 0 AND quantity)
);
CREATE INDEX IF NOT EXISTS campaign_due ON campaigns (priority,scheduled_at,id) WHERE status IN ('scheduled','dispatching');
CREATE TABLE IF NOT EXISTS receipts (
 campaign_id uuid NOT NULL REFERENCES campaigns(id), seq integer NOT NULL,
 accepted_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(campaign_id,seq)
);
CREATE TABLE IF NOT EXISTS completed_chunks (
 campaign_id uuid NOT NULL REFERENCES campaigns(id), start_seq integer NOT NULL, end_seq integer NOT NULL,
 completed_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(campaign_id,start_seq)
);
CREATE TABLE IF NOT EXISTS fault_claims (
 campaign_id uuid NOT NULL REFERENCES campaigns(id), kind text NOT NULL, start_seq integer NOT NULL, created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(campaign_id,kind)
);
CREATE TABLE IF NOT EXISTS incidents (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, campaign_id uuid REFERENCES campaigns(id),
 service text NOT NULL, kind text NOT NULL, severity text NOT NULL, detail text NOT NULL,
 trace_id text, created_at timestamptz NOT NULL DEFAULT now(), resolved_at timestamptz
);
CREATE INDEX IF NOT EXISTS incident_campaign ON incidents(campaign_id,kind) WHERE resolved_at IS NULL;
CREATE TABLE IF NOT EXISTS mirror_jobs (
 campaign_id uuid NOT NULL REFERENCES campaigns(id), seq integer NOT NULL,
 status text NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','sending','sent','unknown')),
 updated_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(campaign_id,seq)
);
CREATE INDEX IF NOT EXISTS mirror_pending ON mirror_jobs(updated_at) WHERE status IN ('pending','sending');
