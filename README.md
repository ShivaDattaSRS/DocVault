# DocVault — File Upload & Document Management System

Secure upload, storage, processing and management of documents (PDF, images, CSV, DOCX) with email-OTP login, per-user quotas, background processing and an admin dashboard.

**Stack:** HTML/CSS/Vanilla JavaScript · FastAPI (Python) · PostgreSQL · Redis + RQ · SMTP

---

## Quick Start — Windows

This project runs locally on Windows. PostgreSQL runs locally on Windows, while Redis can run through WSL/Ubuntu. Redis is optional because DocVault has an in-process thread-pool fallback.

### 1. Requirements

Install:

- Python 3.12 or compatible Python version
- PostgreSQL
- Redis through WSL/Ubuntu (optional)
- A Gmail account with an App Password if email OTP is required

Check:

```powershell
python --version
psql --version
```

If PostgreSQL is not in PATH:

```powershell
& "C:\Program Files\PostgreSQL\18\bin\psql.exe" --version
```

---

## 2. PostgreSQL Setup

Make sure the PostgreSQL Windows service is running.

Check:

```powershell
Get-Service *postgres*
```

Connect:

```powershell
& "C:\Program Files\PostgreSQL\18\bin\psql.exe" -U postgres
```

Inside `psql`, run:

```sql
CREATE USER dms_user WITH PASSWORD 'dms_password';
CREATE DATABASE docvault OWNER dms_user;
GRANT ALL PRIVILEGES ON DATABASE docvault TO dms_user;
\c docvault
GRANT ALL ON SCHEMA public TO dms_user;
ALTER SCHEMA public OWNER TO dms_user;
\q
```

**Important:** PowerShell commands such as `& "C:\Program Files\PostgreSQL\18\bin\psql.exe"` must be run in PowerShell, not inside the PostgreSQL prompt.

The application creates its tables automatically at startup using SQLAlchemy `Base.metadata.create_all()`.

---

## 3. Redis Setup — WSL/Ubuntu

Redis is optional.

From Ubuntu/WSL:

```bash
sudo service redis-server start
redis-cli ping
```

Expected:

```text
PONG
```

Stop Redis:

```bash
sudo service redis-server stop
```

Restart:

```bash
sudo service redis-server restart
```

Status:

```bash
sudo service redis-server status
```

The application uses:

```text
redis://localhost:6379/0
```

If Redis is unavailable, DocVault automatically uses an in-process `ThreadPoolExecutor`.

Check:

```text
http://127.0.0.1:8000/api/health
```

You will see either:

```json
{"job_backend": "redis-rq"}
```

or:

```json
{"job_backend": "thread-pool"}
```

When using `thread-pool`, no RQ worker terminal is required.

---

## 4. Python Virtual Environment

From the project root:

```powershell
python -m venv .venv
```

Activate:

```powershell
.\.venv\Scripts\Activate.ps1
```

If using Command Prompt:

```cmd
.venv\Scripts\activate.bat
```

Then install dependencies:

```powershell
python -m pip install --upgrade pip
pip install -r backend\requirements.txt
```

Optional PDF thumbnail support:

```powershell
pip install pypdfium2
```

---

## 5. Configure `.env`

Create:

```text
backend\.env
```

Example:

```ini
APP_NAME=DocVault
SECRET_KEY=change-this-to-a-long-random-secret
ACCESS_TOKEN_EXPIRE_MINUTES=720
FRONTEND_ORIGINS=http://localhost:8000,http://127.0.0.1:8000

DATABASE_URL=postgresql+psycopg://dms_user:dms_password@localhost:5432/docvault
REDIS_URL=redis://localhost:6379/0

STORAGE_DIR=../storage
MAX_FILE_SIZE_MB=50
DEFAULT_QUOTA_MB=512

SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your-email@gmail.com
SMTP_PASSWORD=your-16-character-gmail-app-password
SMTP_FROM=your-email@gmail.com
SMTP_TLS=true
SMTP_CONSOLE_FALLBACK=true

OTP_TTL_SECONDS=300
OTP_MAX_ATTEMPTS=5

ADMIN_EMAIL=admin@example.com
```

The Python code accesses these as lowercase settings, for example:

```python
settings.database_url
```

The environment variable remains:

```ini
DATABASE_URL=...
```

Check the loaded database URL:

```powershell
cd backend
python -c "from app.config import settings; print(settings.database_url)"
```

---

## Gmail SMTP / OTP

For Gmail:

1. Enable 2-Step Verification.
2. Create a Google App Password.
3. Put the App Password in `SMTP_PASSWORD`.
4. Do not use your normal Gmail password.

Example:

```ini
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your-email@gmail.com
SMTP_PASSWORD=your-app-password
SMTP_FROM=your-email@gmail.com
SMTP_TLS=true
```

For development:

```ini
SMTP_CONSOLE_FALLBACK=true
```

If SMTP fails, the OTP is printed in the backend console and returned as a development OTP.

For production:

```ini
SMTP_CONSOLE_FALLBACK=false
```

Never commit a real App Password to Git.

---

# 6. Start FastAPI

Open PowerShell:

```powershell
cd "C:\path\to\DocVault"
.\.venv\Scripts\Activate.ps1
cd backend
python -m uvicorn app.main:app --reload
```

Open:

```text
http://127.0.0.1:8000
```

API documentation:

```text
http://127.0.0.1:8000/docs
```

Health check:

```text
http://127.0.0.1:8000/api/health
```

---

# 7. Start RQ Worker

Only required when Redis is running and `/api/health` reports `redis-rq`.

Open a second PowerShell:

```powershell
cd "C:\path\to\DocVault"
.\.venv\Scripts\Activate.ps1
cd backend
python -m rq worker docvault --url redis://localhost:6379/0
```

Keep this terminal open.

The flow is:

```text
Browser
   |
   v
FastAPI
   |
   +---- PostgreSQL
   |
   +---- Redis/RQ ----> RQ Worker
   |
   +---- Thread Pool fallback if Redis unavailable
```

---

# Running Without Redis

Redis is optional.

Start only FastAPI:

```powershell
.\.venv\Scripts\Activate.ps1
cd backend
python -m uvicorn app.main:app --reload
```

Do not start the RQ worker.

DocVault will automatically process jobs with its local thread pool.

---

# Application Startup Flow

```text
python -m uvicorn app.main:app --reload
        |
        v
app.main:app
        |
        v
FastAPI application
        |
        v
lifespan()
        |
        +--> init_db()
        |       |
        |       +--> Create missing PostgreSQL tables
        |
        +--> Create/check storage directories
        |
        +--> Detect Redis/RQ
        |
        v
FastAPI starts on port 8000
        |
        v
Browser loads frontend
```

---

# Authentication Flow

Registration:

```text
Register
   |
   v
Validate request
   |
   v
Check PostgreSQL users
   |
   v
Hash password
   |
   v
Create User
   |
   v
Generate 6-digit OTP
   |
   v
Hash OTP and save it
   |
   v
Send OTP through SMTP
   |
   v
User enters OTP
   |
   v
Verify OTP
   |
   v
Mark user verified
   |
   v
Create JWT
   |
   v
Dashboard
```

Login:

```text
Email + Password
       |
       v
Find user
       |
       v
Verify password
       |
       v
Generate OTP
       |
       v
Send OTP
       |
       v
User enters OTP
       |
       v
Verify hashed OTP
       |
       v
Create JWT
       |
       v
Dashboard
```

---

# File Upload Flow

```text
Select File
     |
     v
Validate extension/type/size
     |
     v
Check magic bytes
     |
     v
Check user quota
     |
     v
Calculate SHA-256
     |
     v
Check duplicate
     |
     v
Store content-addressed blob
     |
     v
Save FileRecord
     |
     v
status = processing
     |
     v
Enqueue background job
     |
     +--------------------+
     |                    |
     v                    v
 Redis/RQ             Thread Pool
     |                    |
     +---------+----------+
               |
               v
        process_file()
               |
       +-------+-------+-------+
       |       |       |       |
      PDF    Image    CSV    DOCX
       |       |       |       |
       v       v       v       v
    metadata thumbnail parse metadata
               |
               v
       Update PostgreSQL
               |
               v
        status = ready
```

The API returns after metadata is saved, so large files do not wait for document processing. The dashboard can poll until the file becomes `ready` or `failed`.

---

# Background Jobs

`services/jobs.py` decides how processing is executed.

With Redis:

```text
FastAPI
  |
  v
Redis/RQ
  |
  v
RQ worker
  |
  v
process_file()
```

Without Redis:

```text
FastAPI
  |
  v
ThreadPoolExecutor
  |
  v
process_file()
```

`services/processing.py` handles:

- Image thumbnails and dimensions
- PDF page count and title
- PDF first-page thumbnails when `pypdfium2` is installed
- CSV delimiter, encoding, headers, rows and preview
- DOCX metadata
- Processing failures

---

# Project Layout

```text
DocVault/
│
├── backend/
│   ├── app/
│   │   ├── main.py
│   │   ├── config.py
│   │   ├── database.py
│   │   ├── models.py
│   │   ├── schemas.py
│   │   ├── security.py
│   │   ├── deps.py
│   │   │
│   │   ├── routers/
│   │   │   ├── auth.py
│   │   │   ├── files.py
│   │   │   ├── folders.py
│   │   │   ├── share.py
│   │   │   └── admin.py
│   │   │
│   │   └── services/
│   │       ├── validation.py
│   │       ├── storage.py
│   │       ├── processing.py
│   │       ├── jobs.py
│   │       └── mailer.py
│   │
│   ├── .env
│   └── requirements.txt
│
├── frontend/
│   ├── index.html
│   ├── dashboard.html
│   ├── admin.html
│   ├── share.html
│   ├── css/
│   │   └── styles.css
│   └── js/
│       ├── api.js
│       ├── auth.js
│       ├── dashboard.js
│       ├── admin.js
│       └── share.js
│
├── storage/
│   ├── blobs/
│   └── thumbnails/
│
└── README.md
```

---

# Main Backend Files

### `main.py`

Creates the FastAPI application, configures CORS, initializes the database, registers routers, exposes health/config endpoints and serves the frontend.

### `config.py`

Loads configuration from `backend/.env`, including database, Redis, SMTP, storage, quota, JWT and admin settings.

### `database.py`

Creates the SQLAlchemy engine and sessions. `init_db()` creates missing tables from the SQLAlchemy models.

### `models.py`

Defines database models such as User, OTPCode, Folder, Blob, FileRecord and ActivityLog.

### `schemas.py`

Defines Pydantic request/response models such as registration, login, OTP, file, folder and admin schemas.

### `security.py`

Handles password hashing, OTP generation/hashing/verification and JWT creation.

### `deps.py`

Provides current-user authentication and activity logging dependencies.

### `routers/auth.py`

Handles registration, login, OTP verification, resend OTP, current user and logout.

### `routers/files.py`

Handles upload, list, rename, move, visibility, download and delete.

### `routers/folders.py`

Handles folder CRUD operations.

### `routers/share.py`

Handles public share links.

### `routers/admin.py`

Handles administrator statistics, users, files and activity logs.

### `services/validation.py`

Validates extensions, MIME types, magic bytes, size, empty files and filenames.

### `services/storage.py`

Handles streaming uploads, SHA-256 hashing, temporary files, content-addressed storage and deduplication.

### `services/processing.py`

Performs background document analysis and thumbnail generation.

### `services/jobs.py`

Handles Redis/RQ, thread-pool fallback and rate limiting.

### `services/mailer.py`

Creates and sends OTP email messages through SMTP.

---

# Configuration Reference

| Variable | Example | Purpose |
|---|---|---|
| `APP_NAME` | `DocVault` | Application name |
| `SECRET_KEY` | long random value | JWT/OTP security |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `720` | JWT lifetime |
| `FRONTEND_ORIGINS` | `http://localhost:8000,...` | CORS origins |
| `DATABASE_URL` | PostgreSQL URL | Database connection |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis connection |
| `STORAGE_DIR` | `../storage` | File storage |
| `MAX_FILE_SIZE_MB` | `50` | Maximum file size |
| `DEFAULT_QUOTA_MB` | `512` | Default user quota |
| `SMTP_HOST` | `smtp.gmail.com` | SMTP server |
| `SMTP_PORT` | `587` | SMTP port |
| `SMTP_USER` | Gmail address | SMTP username |
| `SMTP_PASSWORD` | App Password | SMTP password |
| `SMTP_FROM` | Gmail address | Sender |
| `SMTP_TLS` | `true` | STARTTLS |
| `SMTP_CONSOLE_FALLBACK` | `true` | Development OTP fallback |
| `OTP_TTL_SECONDS` | `300` | OTP lifetime |
| `OTP_MAX_ATTEMPTS` | `5` | OTP attempt limit |
| `ADMIN_EMAIL` | admin email | Administrator email |

---

# Features

| Area | What it does |
|---|---|
| **Authentication** | Registration/login with bcrypt password and 6-digit OTP |
| **OTP security** | Hashed, single-use, expiring OTPs with attempt limits |
| **Rate limiting** | Redis-backed when Redis is available |
| **Validation** | Extension, MIME, magic-byte, size and empty-file validation |
| **Safe filenames** | Filename sanitisation and traversal protection |
| **Duplicates** | SHA-256 duplicate detection |
| **Storage** | Content-addressed blob storage and deduplication |
| **Processing** | Image, PDF, CSV and DOCX processing |
| **Folders** | Nested folder management |
| **Access control** | Private/public visibility |
| **Downloads** | Signed, expiring download links |
| **Quotas** | Per-user storage quotas |
| **Logging** | Activity logging with IP and user agent |
| **Admin** | Statistics, user management, files and activity logs |

---

# Troubleshooting

## `ModuleNotFoundError: No module named 'app'`

Make sure you are inside `backend`:

```powershell
cd backend
```

Activate the environment from the project root before entering backend:

```powershell
..\.venv\Scripts\Activate.ps1
```

Then:

```powershell
python -m uvicorn app.main:app --reload
```

---

## `Settings object has no attribute DATABASE_URL`

Use:

```powershell
python -c "from app.config import settings; print(settings.database_url)"
```

not:

```powershell
python -c "from app.config import settings; print(settings.DATABASE_URL)"
```

---

## `column users.full_name does not exist`

The database schema does not match the current models.

For a new development database, recreate the database and let the application create the tables automatically on startup.

---

## `Did not find any relation named "users"`

The `users` table has not been created yet.

Start:

```powershell
cd backend
python -m uvicorn app.main:app --reload
```

Then check:

```powershell
& "C:\Program Files\PostgreSQL\18\bin\psql.exe" -U dms_user -d docvault -c "\dt"
```

---

## SMTP `WinError 10061`

The configured SMTP server/port is refusing the connection.

For Gmail:

```ini
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_TLS=true
```

During development:

```ini
SMTP_CONSOLE_FALLBACK=true
```

---

## Gmail `535 Username and Password not accepted`

Use a Google App Password, not the normal Gmail password.

Update:

```ini
SMTP_USER=your-email@gmail.com
SMTP_PASSWORD=your-app-password
```

Restart FastAPI after changing `.env`.

---

## Redis unavailable

Check:

```text
http://127.0.0.1:8000/api/health
```

If the backend is:

```text
thread-pool
```

the application is working without Redis.

If Redis should be used, from Ubuntu/WSL:

```bash
redis-cli ping
```

Expected:

```text
PONG
```

---

# Daily Development Startup

### Terminal 1 — Redis (Ubuntu/WSL)

```bash
sudo service redis-server start
redis-cli ping
```

### Terminal 2 — FastAPI (PowerShell)

```powershell
cd "C:\path\to\DocVault"
.\.venv\Scripts\Activate.ps1
cd backend
python -m uvicorn app.main:app --reload
```

### Terminal 3 — RQ Worker (PowerShell, only if Redis is enabled)

```powershell
cd "C:\path\to\DocVault"
.\.venv\Scripts\Activate.ps1
cd backend
python -m rq worker docvault --url redis://localhost:6379/0
```

Open:

```text
http://127.0.0.1:8000
```

---

# Moving DocVault to Another Windows System

Copy the project folder to the new computer.

Install:

1. Python
2. PostgreSQL
3. WSL/Ubuntu and Redis if Redis is required

Then:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r backend\requirements.txt
```

Create the PostgreSQL user/database, create `backend\.env`, and update the configuration for the new computer.

Start FastAPI:

```powershell
cd backend
python -m uvicorn app.main:app --reload
```

If Redis is enabled, start Redis and the RQ worker as described above.

The application creates its database tables automatically on startup.

---

# Production Notes

Before production:

- Use a strong random `SECRET_KEY`.
- Set `SMTP_CONSOLE_FALLBACK=false`.
- Never commit `.env` or real credentials.
- Use HTTPS.
- Protect PostgreSQL and Redis.
- Files are stored unencrypted on disk by default.
- Consider encryption at rest for sensitive documents.
- JWT tokens are stateless; logout does not immediately invalidate an already-issued token.
- Consider shorter JWT expiry or token revocation if required.
- Add Alembic migrations before frequent schema changes.
- Use a production ASGI deployment instead of `--reload`.

---

# Local URLs

Application:

```text
http://127.0.0.1:8000
```

Swagger API docs:

```text
http://127.0.0.1:8000/docs
```

Health:

```text
http://127.0.0.1:8000/api/health
```

Configuration:

```text
http://127.0.0.1:8000/api/config
```

---

# Summary

DocVault can run completely locally on Windows.

Required:

```text
Windows
 ├── Python
 ├── PostgreSQL
 └── FastAPI
```

Optional:

```text
WSL/Ubuntu
 └── Redis
      └── RQ worker
```

Without Redis:

```text
FastAPI → ThreadPoolExecutor
```

Without working SMTP during development:

```text
FastAPI → Console/Development OTP
```

Normal local URL:

```text
http://127.0.0.1:8000
```
