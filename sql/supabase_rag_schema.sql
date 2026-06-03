-- Impuestia RAG schema for Supabase (Postgres + pgvector)
-- Ejecutar una vez en SQL Editor de Supabase.

-- ============================================
-- Extensiones
-- ============================================
create extension if not exists vector;

-- ============================================
-- Tabla: planes de suscripción
-- ============================================
create table if not exists public.plans (
  id text primary key,
  name text not null,
  max_users int not null default 1,
  max_documents int not null default 0,
  max_queries_per_day int null, -- null = ilimitado
  allows_voice boolean not null default false,
  allows_pdf_download boolean not null default false,
  price_monthly_usd decimal(10,2) not null default 0.00,
  created_at timestamptz not null default now()
);

-- Planes por defecto
insert into public.plans (id, name, max_users, max_documents, max_queries_per_day, allows_voice, allows_pdf_download, price_monthly_usd)
values
  ('free', 'Free', 1, 0, 10, false, false, 0.00),
  ('basic', 'Basic', 3, 50, 100, true, true, 29.00),
  ('pro', 'Pro', 10, 500, null, true, true, 79.00),
  ('enterprise', 'Enterprise', 999999, 999999, null, true, true, 299.00)
on conflict (id) do nothing;

-- ============================================
-- Tabla: organizaciones
-- ============================================
create table if not exists public.organizations (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  slug text unique not null,
  plan_id text not null default 'free' references public.plans(id),
  settings jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- ============================================
-- Tabla: usuarios
-- ============================================
create table if not exists public.users (
  id uuid primary key default gen_random_uuid(),
  organization_id uuid not null references public.organizations(id) on delete cascade,
  telegram_user_id bigint unique,
  email text,
  role text not null default 'member' check (role in ('owner', 'admin', 'member')),
  created_at timestamptz not null default now()
);

-- ============================================
-- Tabla: grupos de Telegram
-- ============================================
create table if not exists public.telegram_groups (
  id uuid primary key default gen_random_uuid(),
  organization_id uuid not null references public.organizations(id) on delete cascade,
  telegram_chat_id bigint unique not null,
  chat_type text not null default 'group' check (chat_type in ('group', 'supergroup', 'channel')),
  is_active boolean not null default true,
  plan_override text null references public.plans(id),
  created_at timestamptz not null default now()
);

-- ============================================
-- Tabla: documentos de organización (metadata)
-- ============================================
create table if not exists public.organization_documents (
  id uuid primary key default gen_random_uuid(),
  organization_id uuid not null references public.organizations(id) on delete cascade,
  filename text not null,
  source_path text not null,
  source_type text not null default 'caso_interno',
  uploaded_by uuid references public.users(id),
  created_at timestamptz not null default now()
);

-- ============================================
-- Tabla: chunks de documentos (RAG)
-- ============================================
create table if not exists public.document_chunks (
  id bigserial primary key,
  chunk_uid text not null unique,
  source_path text not null,
  filename text not null,
  source_type text not null,
  law_tag text null,
  hierarchy_path text null,
  section_level_name text null,
  content text not null,
  metadata jsonb not null default '{}'::jsonb,
  content_hash text not null,
  is_derogada boolean not null default false,
  embedding vector(1536) not null,
  organization_id uuid null references public.organizations(id) on delete cascade,
  parent_chunk_uid text null,
  chunk_index int not null default 0,
  total_chunks int not null default 1,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- Índices
create index if not exists idx_document_chunks_source_path
  on public.document_chunks (source_path);

create index if not exists idx_document_chunks_source_type
  on public.document_chunks (source_type);

create index if not exists idx_document_chunks_law_tag
  on public.document_chunks (law_tag);

create index if not exists idx_document_chunks_is_derogada
  on public.document_chunks (is_derogada);

create index if not exists idx_document_chunks_organization_id
  on public.document_chunks (organization_id);

create index if not exists idx_document_chunks_content_hash
  on public.document_chunks (content_hash);

create index if not exists idx_document_chunks_embedding_cosine
  on public.document_chunks
  using ivfflat (embedding vector_cosine_ops)
  with (lists = 100);

-- ============================================
-- Tabla: logs de uso
-- ============================================
create table if not exists public.usage_logs (
  id bigserial primary key,
  organization_id uuid references public.organizations(id),
  user_id uuid references public.users(id),
  telegram_chat_id bigint,
  query_type text not null,
  tokens_used int,
  created_at timestamptz not null default now()
);

create index if not exists idx_usage_logs_org_date
  on public.usage_logs (organization_id, created_at);

-- ============================================
-- Función: búsqueda semántica con filtros
-- ============================================
create or replace function public.match_document_chunks(
  query_embedding vector(1536),
  match_count int default 10,
  filter_source_type text default null,
  filter_law_tag text default null,
  include_derogadas boolean default false,
  filter_organization_id uuid default null
)
returns table (
  chunk_uid text,
  source_path text,
  filename text,
  source_type text,
  law_tag text,
  hierarchy_path text,
  section_level_name text,
  content text,
  metadata jsonb,
  organization_id uuid,
  similarity float
)
language sql
stable
as $$
  select
    c.chunk_uid,
    c.source_path,
    c.filename,
    c.source_type,
    c.law_tag,
    c.hierarchy_path,
    c.section_level_name,
    c.content,
    c.metadata,
    c.organization_id,
    1 - (c.embedding <=> query_embedding) as similarity
  from public.document_chunks c
  where
    (filter_source_type is null or c.source_type = filter_source_type)
    and (filter_law_tag is null or c.law_tag = filter_law_tag)
    and (include_derogadas or c.is_derogada = false)
    and (
      filter_organization_id is null
      or c.organization_id is null
      or c.organization_id = filter_organization_id
    )
  order by c.embedding <=> query_embedding
  limit greatest(1, least(match_count, 50));
$$;

-- ============================================
-- Políticas RLS (Row Level Security)
-- ============================================
alter table public.organizations enable row level security;
alter table public.users enable row level security;
alter table public.telegram_groups enable row level security;
alter table public.organization_documents enable row level security;
alter table public.document_chunks enable row level security;
alter table public.usage_logs enable row level security;

-- ============================================
-- Tabla: grafo de conocimiento (GraphRAG)
-- ============================================
create table if not exists public.knowledge_graph (
  id bigserial primary key,
  source_chunk_uid text not null,
  target_chunk_uid text not null,
  relation_type text not null,
  confidence float not null default 1.0,
  extracted_by text not null default 'llm',
  created_at timestamptz not null default now()
);

create index if not exists idx_kg_source on knowledge_graph(source_chunk_uid);
create index if not exists idx_kg_target on knowledge_graph(target_chunk_uid);
create index if not exists idx_kg_type on knowledge_graph(relation_type);

-- Política: document_chunks visibles para todos (docs públicos) + docs de la org
-- Nota: las políticas RLS con vector requieren cuidado; para el bot usamos service key
-- así que estas políticas son más para el dashboard web con anon key.
create policy "document_chunks_public_or_org"
  on public.document_chunks
  for select
  using (
    organization_id is null
    or organization_id = auth.uid()  -- simplificado; en producción usar claims JWT
  );
