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

-- v2 migration (run only as schema owner)
CREATE EXTENSION IF NOT EXISTS vector;
CREATE TABLE IF NOT EXISTS archive_documents (
 campaign_id uuid PRIMARY KEY REFERENCES campaigns(id),
 indexed_at timestamptz NOT NULL DEFAULT now(), model text NOT NULL,
 search_text tsvector NOT NULL
);
CREATE INDEX IF NOT EXISTS archive_fts ON archive_documents USING gin(search_text);
CREATE TABLE IF NOT EXISTS archive_vectors (
 campaign_id uuid NOT NULL REFERENCES archive_documents(campaign_id) ON DELETE CASCADE,
 part integer NOT NULL, embedding vector(384) NOT NULL, PRIMARY KEY(campaign_id,part)
);
CREATE INDEX IF NOT EXISTS archive_hnsw ON archive_vectors USING hnsw (embedding vector_cosine_ops);
CREATE TABLE IF NOT EXISTS archive_index_retries (
 campaign_id uuid PRIMARY KEY REFERENCES campaigns(id) ON DELETE CASCADE,
 next_attempt timestamptz NOT NULL
);
CREATE TABLE IF NOT EXISTS auth_users (
 id uuid PRIMARY KEY, username text UNIQUE NOT NULL, password_hash text NOT NULL,
 totp_secret text NOT NULL, last_totp_step bigint NOT NULL DEFAULT -1,
 role text NOT NULL CHECK(role IN ('admin','operator','viewer')), enabled boolean NOT NULL DEFAULT true,
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE OR REPLACE FUNCTION public.register_local_operator(
 account_id uuid, email text, hash text, secret text, verified_step bigint
) RETURNS uuid
LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, pg_temp
AS $$
 INSERT INTO public.auth_users(id,username,password_hash,totp_secret,last_totp_step,role)
 SELECT account_id,email,hash,secret,verified_step,'operator'
 WHERE secret <> '' AND verified_step >= 0
 ON CONFLICT (username) DO NOTHING RETURNING id
$$;
REVOKE ALL ON FUNCTION public.register_local_operator(uuid,text,text,text,bigint) FROM PUBLIC;
CREATE TABLE IF NOT EXISTS auth_sessions (
 token_hash text PRIMARY KEY, user_id uuid NOT NULL REFERENCES auth_users(id) ON DELETE CASCADE,
 csrf_hash text NOT NULL, expires_at timestamptz NOT NULL, last_seen timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS auth_session_user ON auth_sessions(user_id);
CREATE INDEX IF NOT EXISTS auth_session_expiry ON auth_sessions(expires_at);
CREATE TABLE IF NOT EXISTS auth_limits (
 scope text PRIMARY KEY, window_start timestamptz NOT NULL DEFAULT now(), attempts integer NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS security_audit (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, actor text NOT NULL, action text NOT NULL,
 outcome text NOT NULL, target text, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS campaigns_list_order ON campaigns(created_at DESC,id DESC);
CREATE INDEX IF NOT EXISTS campaigns_archive_order ON campaigns(created_at,id) WHERE accepted>0;
