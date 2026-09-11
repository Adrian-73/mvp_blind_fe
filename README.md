# Private Blind-Dating Chat Room API Backend

This is a secure, high-performance, real-time private blind-dating chat room application backend built with FastAPI, WebSockets, and Supabase PostgreSQL.

## Core Features
1. **Secure Anonymity**: Users sign up with an email and password but are automatically assigned random pleasant usernames (e.g. "CalmRiver") and 16-char hex avatar seeds (rendered using DiceBear avatar APIs).
2. **Robust Multi-Layer Auth**:
   - Standard user tokens valid for 30 days, signed using `JWT_SECRET`.
   - Separate admin credential verification with passwords hashed with bcrypt, generating 8-hour admin tokens signed using `ADMIN_JWT_SECRET`.
3. **Admin Matchmaking**: Admin manually pairs any two waiting users into private rooms.
4. **WebSocket Messaging**: Real-time message exchange within active rooms with permanent messaging storage.
5. **Streaming CSV Exports**: High-performance streaming of user records and message logs using server-side cursors to maintain near-zero memory footprint.
6. **Robust Real-Time Closures**: Automatic client redirection via custom close codes on room deactivation by an admin.

---

## Technical Stack
- **Python**: 3.11+
- **FastAPI**: Core framework and native WebSockets.
- **asyncpg**: High-speed asynchronous PostgreSQL interface.
- **python-jose**: JWT signature validation and generation.
- **passlib (bcrypt)**: Secure password hashing.

---

## Database Migrations

Run the SQL migration in `schema.sql` within your Supabase SQL editor:
```sql
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

-- Create users table
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
```

---

## Environment Setup

Create a `.env` file at the project root based on `.env.example`:
```env
DATABASE_URL=postgresql://postgres.yourproject:yourpassword@aws-0-us-east-1.pooler.supabase.com:5432/postgres
JWT_SECRET=your_jwt_signing_secret_for_users_minimum_32_characters
ADMIN_JWT_SECRET=your_admin_jwt_signing_secret_minimum_32_characters
ADMIN_USERNAME=dating_admin
ADMIN_PASSWORD_HASH=paste_the_output_of_hash_password.py_here
FRONTEND_URL=https://private-blind-dating.vercel.app
```
*(Generate `ADMIN_PASSWORD_HASH` by running `python hash_password.py` from the project root and pasting the line it prints.)*

---

## Quick Start

### 1. Install Dependencies
```bash
pip install -r requirements.txt
```

### 2. Start Application Locally
```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```
Open interactive docs at `http://localhost:8000/docs`.

---

## Deployment to Render

To deploy onto **Render free tier** using the blueprint `render.yaml` configuration:
1. Connect your repository to Render.
2. Select **New Web Service** and connect your repo.
3. Render automatically picks up `render.yaml` configurations:
   - **Runtime**: Python
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
4. Set up environment variables inside your Render dashboard:
   - `DATABASE_URL`
   - `JWT_SECRET`
   - `ADMIN_JWT_SECRET`
   - `ADMIN_USERNAME`
   - `ADMIN_PASSWORD_HASH`
   - `FRONTEND_URL`

---

## API Documentation

### Unauthenticated Endpoints
- **GET** `/health`: Return `{"status": "ok"}`.

### Standard User Endpoints
- **POST** `/api/signup`: Creates user + password credentials, returns display name, avatar, and User JWT.
- **POST** `/api/auth/login`: Verifies password and returns User JWT.
- **GET** `/api/me/status`: Returns matchmaking status and active room partner information (Polled every 4s).
- **POST** `/api/user/quiz`: Submit or update quiz answers.
- **GET** `/api/rooms/{room_id}/messages`: Paginated message history (last 50 messages, ordered ascending).

### Admin Endpoints
- **POST** `/api/admin/login`: Verifies admin password and issues custom Admin JWT.
- **GET** `/api/admin/users`: Searchable and paginated user list.
- **POST** `/api/admin/match`: Pair two waiting users into an active room and dispatches WebSocket status updates.
- **GET** `/api/admin/rooms`: Lists all active rooms.
- **POST** `/api/admin/rooms/{room_id}/deactivate`: Closes room, releases users, and cleans up sockets.
- **GET** `/api/admin/users/export`: High-performance Streaming CSV export of all users.
- **GET** `/api/admin/messages/export`: High-performance Streaming CSV export of messaging history.

### Real-Time WebSocket Server
- **WS** `/ws/{room_id}?token=<User JWT>`
  - Handles duplex real-time messages.
  - Formats:
    - Receive: `{"content": "hello"}`
    - Broadcast: `{"type": "message", "id": "<uuid>", "sender_id": "<uuid>", "content": "hello", "sent_at": "<iso8601>"}`
