-- Migración aditiva: agrega el texto de la consulta a usage_logs.
-- Hoy la tabla registra query_type/tokens_used pero nunca el texto real,
-- lo que hace imposible medir con evidencia (scripts/eval_graph_lift.py)
-- si el grafo de conocimiento mejora las respuestas sobre consultas reales.
-- Ejecutar una vez en el SQL Editor de Supabase (no destructivo).

alter table public.usage_logs
  add column if not exists query_text text;

create index if not exists idx_usage_logs_created_at
  on public.usage_logs (created_at desc);
