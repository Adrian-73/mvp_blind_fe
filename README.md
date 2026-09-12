# Private Blind-Dating Chat Room API Backend

This is a secure, high-performance, real-time private blind-dating chat room application backend built with FastAPI, WebSockets, and Supabase PostgreSQL.

## Core Features
1. **Secure Anonymity**: Users sign up with an email and password, confirming the email with a 6-digit code, but are automatically assigned random pleasant usernames (e.g. "CalmRiver") and 16-char hex avatar seeds (rendered using DiceBear avatar APIs).
2. **Server-Side Sessions**:
   - Logging in stores a row in `sessions` and gives the browser a random token in an httpOnly, SameSite=Lax cookie (`Secure` over https). Only the token's SHA-256 hash is stored, and page JavaScript never sees the token.
   - User sessions end after 30 days without activity or 90 days in total; admin sessions after 1 hour idle or 8 hours in total. Activity slides the expiry forward.
   - Logout deletes the session and closes its chat sockets, and users can log out on every device at once. Changing `ADMIN_USERNAME` or `ADMIN_PASSWORD_HASH` ends every admin session.
   - State-changing requests and chat handshakes coming from other sites are rejected (CSRF protection).
3. **Admin Matchmaking**: Admin manually pairs any two waiting users into private rooms.
4. **WebSocket Messaging**: Real-time message exchange within active rooms with permanent messaging storage.
5. **Streaming CSV Exports**: High-performance streaming of user records and message logs using server-side cursors to maintain near-zero memory footprint.
6. **Robust Real-Time Closures**: Automatic client redirection via custom close codes on room deactivation by an admin.
7. **Match Emails (optional)**: When matching, the admin can email both users their match's codename and a link back to chat. Works with any SMTP provider and stays off until SMTP is configured.

---

## Technical Stack
- **Python**: 3.11+
- **FastAPI**: Core framework and native WebSockets.
- **asyncpg**: High-speed asynchronous PostgreSQL interface.
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
  date_of_birth date,
  gender text check (gender in ('male', 'female', 'non_binary')),
  interested_in text[] check (interested_in <@ array['male', 'female', 'non_binary']),
  state text,
  bio text,
  single_reason text,
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

**Upgrading a database created before sessions existed?** Run the `create table sessions` block from `schema.sql` along with its index and `enable row level security` statement. Everyone has to log in once more after the deploy, because the old JWTs are no longer accepted.

---

## Environment Setup

Create a `.env` file at the project root based on `.env.example`:
```env
DATABASE_URL=postgresql://postgres.yourproject:yourpassword@aws-0-us-east-1.pooler.supabase.com:5432/postgres
ADMIN_USERNAME=dating_admin
ADMIN_PASSWORD_HASH=paste_the_output_of_hash_password.py_here
FRONTEND_URL=https://private-blind-dating.vercel.app
# Sign-up codes and match emails (without SMTP, sign-up codes only go to the server log)
SMTP_HOST=smtp-relay.brevo.com
SMTP_PORT=587
SMTP_USERNAME=your_smtp_login
SMTP_PASSWORD=your_smtp_password
SMTP_FROM=Loom <matchmaker@yourdomain.com>
```
*(Generate `ADMIN_PASSWORD_HASH` by running `python hash_password.py` from the project root and pasting the line it prints.)*

**Email** carries sign-up codes and match emails. With `SMTP_HOST` empty, sign-up codes are only printed to the server log (handy locally, but nobody can finish signing up in production) and the admin panel's match email toggle stays disabled. `FRONTEND_URL` must be the public app URL because match emails link to `FRONTEND_URL/waiting`. Vercel only blocks outbound port 25, so 465 and 587 work there; Render's free tier also blocks 465 and 587, so use your provider's alternate port on it (2525 for Brevo or Mailgun, 2587 for Resend). Set `SMTP_USE_SSL=true` if your provider's port expects TLS from the first byte (465 does this automatically).

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
   - `ADMIN_USERNAME`
   - `ADMIN_PASSWORD_HASH`
   - `FRONTEND_URL`
   - `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM` (for sign-up codes and match emails)

---

## API Documentation

### Unauthenticated Endpoints
- **GET** `/health`: Return `{"status": "ok"}`.

### Standard User Endpoints
- **POST** `/api/auth/send-otp`: Emails a 6-digit sign-up code, valid for 10 minutes. The same address can ask for another after 60 seconds.
- **POST** `/api/signup`: Checks the emailed `otp_code`, creates user + password credentials, returns display name and avatar, and sets the session cookie.
- **POST** `/api/auth/verify-otp`: Logs an existing user in with an emailed code. Emails without an account get a 404.
- **POST** `/api/auth/login`: Verifies password, returns the profile and sets a fresh session cookie.
- **GET** `/api/auth/session`: Returns the logged-in user, or 401 without a live session.
- **POST** `/api/auth/logout`: Ends this browser's session.
- **POST** `/api/auth/logout-all`: Ends all of the user's sessions, on every device.
- **GET** `/api/me/status`: Returns matchmaking status and active room partner information (Polled every 4s).
- **POST** `/api/user/quiz`: Submit or update quiz answers.
- **GET** `/api/rooms/{room_id}/messages`: Paginated message history (last 50 messages, ordered ascending).

### Admin Endpoints
- **POST** `/api/admin/login`: Verifies the admin password and sets the admin session cookie.
- **GET** `/api/admin/session`: Returns the admin username, or 401 without a live admin session.
- **POST** `/api/admin/logout`: Ends this browser's admin session.
- **GET** `/api/admin/users`: Searchable and paginated user list.
- **POST** `/api/admin/match`: Pair two waiting users into an active room and dispatches WebSocket status updates. Send `"notify_by_email": true` to also email both users; the response's `email_status` is `sent`, `partial`, `failed`, `not_configured` or `skipped`.
- **GET** `/api/admin/email-config`: Returns `{"enabled": true}` when SMTP is configured, so the admin panel knows whether match emails can be sent.
- **GET** `/api/admin/rooms`: Lists all active rooms.
- **POST** `/api/admin/rooms/{room_id}/deactivate`: Closes room, releases users, and cleans up sockets.
- **GET** `/api/admin/users/export`: High-performance Streaming CSV export of all users.
- **GET** `/api/admin/messages/export`: High-performance Streaming CSV export of messaging history.

### Real-Time WebSocket Server
- **WS** `/ws/{room_id}`
  - Authenticated by the session cookie the browser sends with the handshake. Closes with `4001` when not logged in or when the session ends, and `4003` for rooms the user can't join.
  - Handles duplex real-time messages.
  - Formats:
    - Receive: `{"content": "hello"}`
    - Broadcast: `{"type": "message", "id": "<uuid>", "sender_id": "<uuid>", "content": "hello", "sent_at": "<iso8601>"}`
