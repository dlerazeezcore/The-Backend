create extension if not exists pgcrypto;

create table if not exists public.support_conversations (
  id uuid primary key default gen_random_uuid(),
  customer_user_id text not null,
  customer_display_name text not null default '',
  status text not null default 'open' check (status in ('open', 'closed', 'pending')),
  source text not null default 'in_app',
  telegram_chat_id text,
  telegram_thread_id bigint,
  latest_customer_message_preview text not null default '',
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now()),
  last_message_at timestamptz not null default timezone('utc', now())
);

create index if not exists support_conversations_customer_user_id_idx
  on public.support_conversations (customer_user_id);

create index if not exists support_conversations_status_idx
  on public.support_conversations (status, last_message_at desc);

create table if not exists public.support_messages (
  id uuid primary key default gen_random_uuid(),
  conversation_id uuid not null references public.support_conversations(id) on delete cascade,
  sender_type text not null check (sender_type in ('customer', 'support', 'system')),
  sender_user_id text not null default '',
  sender_display_name text not null default '',
  body text not null,
  telegram_chat_id text,
  telegram_message_id bigint,
  reply_to_telegram_message_id bigint,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default timezone('utc', now())
);

create index if not exists support_messages_conversation_created_at_idx
  on public.support_messages (conversation_id, created_at asc);

create table if not exists public.support_telegram_map (
  id uuid primary key default gen_random_uuid(),
  conversation_id uuid not null references public.support_conversations(id) on delete cascade,
  app_message_id uuid not null references public.support_messages(id) on delete cascade,
  telegram_chat_id text not null,
  telegram_message_id bigint not null,
  telegram_thread_id bigint,
  direction text not null check (direction in ('to_support', 'from_support')),
  created_at timestamptz not null default timezone('utc', now()),
  unique (telegram_chat_id, telegram_message_id)
);

create index if not exists support_telegram_map_conversation_idx
  on public.support_telegram_map (conversation_id, created_at desc);

alter table public.support_conversations enable row level security;
alter table public.support_messages enable row level security;
alter table public.support_telegram_map enable row level security;

drop policy if exists "support conversations service role only" on public.support_conversations;
create policy "support conversations service role only"
  on public.support_conversations
  for all
  to service_role
  using (true)
  with check (true);

drop policy if exists "support messages service role only" on public.support_messages;
create policy "support messages service role only"
  on public.support_messages
  for all
  to service_role
  using (true)
  with check (true);

drop policy if exists "support telegram map service role only" on public.support_telegram_map;
create policy "support telegram map service role only"
  on public.support_telegram_map
  for all
  to service_role
  using (true)
  with check (true);
