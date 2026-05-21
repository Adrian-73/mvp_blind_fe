create extension if not exists "pgcrypto";

-- Create rooms first without foreign keys to users
create table rooms (
  id uuid primary key default gen_random_uuid(),
  user_a uuid not null,
  user_b uuid not null,
  created_by_admin boolean not null default true,
  is_active boolean not null default true,
  created_at timestamptz not null default now()
);

-- Create users table referencing rooms
create table users (
  id uuid primary key default gen_random_uuid(),
  email text unique not null,
  display_name text not null,
  avatar_seed text not null,
  quiz_answers jsonb not null default '{}',
  status text not null default 'waiting' check (status in ('waiting', 'matched')),
  room_id uuid references rooms(id) on delete set null,
  created_at timestamptz not null default now()
);

-- Add foreign keys to rooms to break circular dependency
alter table rooms add constraint fk_user_a foreign key (user_a) references users(id);
alter table rooms add constraint fk_user_b foreign key (user_b) references users(id);

-- Create messages table
create table messages (
  id uuid primary key default gen_random_uuid(),
  room_id uuid not null references rooms(id) on delete cascade,
  sender_id uuid not null references users(id),
  content text not null,
  sent_at timestamptz not null default now()
);

-- Create user credentials table for self-contained bcrypt authentication
create table user_credentials (
  user_id uuid primary key references users(id) on delete cascade,
  password_hash text not null
);

create index idx_messages_room_sent on messages(room_id, sent_at asc);

-- Create table for managing short-lived Email OTPs
create table email_otps (
  email text primary key,
  otp_code text not null,
  expires_at timestamptz not null
);
