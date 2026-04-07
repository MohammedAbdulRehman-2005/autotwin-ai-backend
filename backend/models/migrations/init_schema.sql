-- ══════════════════════════════════════════════════════════════
-- AutoTwin AI — Supabase PostgreSQL Schema
-- Created via Supabase Dashboard
-- ══════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS public.agent_logs (
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  invoice_id uuid,
  user_id text NOT NULL,
  agent text NOT NULL,
  action text NOT NULL,
  result text NOT NULL,
  confidence real,
  attempt integer DEFAULT 1,
  details text,
  duration_ms integer,
  created_at timestamp without time zone DEFAULT now(),
  CONSTRAINT agent_logs_pkey PRIMARY KEY (id)
);

CREATE TABLE IF NOT EXISTS public.approvals (
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  invoice_id uuid,
  user_id text NOT NULL,
  status text DEFAULT 'pending'::text,
  requested_by text,
  notes text,
  created_at timestamp without time zone DEFAULT now(),
  resolved_at timestamp without time zone,
  CONSTRAINT approvals_pkey PRIMARY KEY (id)
);

CREATE TABLE IF NOT EXISTS public.chat_messages (
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  user_id text NOT NULL,
  role text NOT NULL,
  content text NOT NULL,
  created_at timestamp without time zone DEFAULT now(),
  CONSTRAINT chat_messages_pkey PRIMARY KEY (id)
);

CREATE TABLE IF NOT EXISTS public.extracted_documents (
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  user_id text NOT NULL,
  invoice_id text NOT NULL,
  vendor text NOT NULL,
  amount real NOT NULL,
  date text,
  anomaly boolean DEFAULT false,
  confidence real NOT NULL,
  status text NOT NULL,
  decision text NOT NULL,
  explanation text,
  anomaly_details jsonb,
  confidence_breakdown jsonb,
  logs jsonb,
  risk_score real NOT NULL,
  processing_time_ms real,
  file_url text,
  created_at timestamp without time zone DEFAULT now(),
  CONSTRAINT extracted_documents_pkey PRIMARY KEY (id)
);

CREATE TABLE IF NOT EXISTS public.integrations (
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  user_id text NOT NULL,
  provider text NOT NULL,
  enabled boolean DEFAULT false,
  access_token text,
  last_synced_at timestamp without time zone,
  created_at timestamp without time zone DEFAULT now(),
  CONSTRAINT integrations_pkey PRIMARY KEY (id)
);

CREATE TABLE IF NOT EXISTS public.invoices (
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  user_id text NOT NULL,
  vendor text NOT NULL,
  invoice_no text NOT NULL,
  amount real NOT NULL,
  currency text DEFAULT 'INR'::text,
  status text DEFAULT 'pending'::text,
  confidence real NOT NULL DEFAULT 0,
  category text,
  file_url text,
  due_date timestamp without time zone,
  created_at timestamp without time zone DEFAULT now(),
  CONSTRAINT invoices_pkey PRIMARY KEY (id)
);

CREATE TABLE IF NOT EXISTS public.transactions (
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  user_id text NOT NULL,
  category text NOT NULL,
  amount real NOT NULL,
  vendor text NOT NULL,
  date timestamp without time zone NOT NULL,
  anomaly_score real DEFAULT 0,
  created_at timestamp without time zone DEFAULT now(),
  CONSTRAINT transactions_pkey PRIMARY KEY (id)
);

CREATE TABLE IF NOT EXISTS public.user_settings (
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  user_id text NOT NULL UNIQUE,
  confidence_auto_approve real DEFAULT 95,
  confidence_hitl real DEFAULT 70,
  notify_email boolean DEFAULT true,
  notify_alerts boolean DEFAULT true,
  notify_workflow boolean DEFAULT false,
  plan text DEFAULT 'free'::text,
  created_at timestamp without time zone DEFAULT now(),
  updated_at timestamp without time zone DEFAULT now(),
  CONSTRAINT user_settings_pkey PRIMARY KEY (id)
);

CREATE TABLE IF NOT EXISTS public.workflow_runs (
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  user_id text NOT NULL,
  name text NOT NULL,
  status text DEFAULT 'running'::text,
  steps_json jsonb,
  trigger_type text DEFAULT 'manual'::text,
  started_at timestamp without time zone DEFAULT now(),
  finished_at timestamp without time zone,
  CONSTRAINT workflow_runs_pkey PRIMARY KEY (id)
);

-- Note: The foreign key is added here after the invoices table is created.
ALTER TABLE IF EXISTS public.approvals
  ADD CONSTRAINT approvals_invoice_id_invoices_id_fk FOREIGN KEY (invoice_id) REFERENCES public.invoices(id) ON DELETE CASCADE;
