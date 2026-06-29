# DriveX — AWS S3 File Manager

A production-ready, secure S3 file manager with a Google Drive-style UI. Built with FastAPI, Boto3, and a dark SaaS-style frontend.

---

## Features

- 🔐 **Secure credential handling** — AES-encrypted in session, never stored to disk or logged
- 🪣 **Multi-bucket support** — connect to several buckets simultaneously
- 📁 **Full file manager** — browse, upload, download, delete, rename, copy, move, create folders
- 🔗 **Pre-signed URLs** — generate time-limited shareable links
- 📦 **Multipart upload** — handles files up to 5 GB automatically
- 🔍 **Search** — search across all objects in a bucket
- 🛡️ **CSRF protection** on all mutating operations
- 📋 **Audit logs** — every action logged with identity/IP, never credentials
- 🎨 **Polished dark UI** — list + grid view, breadcrumb navigation, toast notifications

---

## Project Structure

```
drivex/
├── main.py                         # FastAPI app entry point
├── requirements.txt
├── .env.example
├── .gitignore
├── iam_policy.json                 # Sample AWS IAM policy
├── logs/                           # Audit logs (auto-created)
└── app/
    ├── routes/
    │   ├── auth.py                 # Login / logout routes
    │   ├── dashboard.py            # Dashboard page route
    │   └── s3.py                   # All S3 API endpoints
    ├── services/
    │   └── s3_service.py           # Boto3 S3 operations
    ├── security/
    │   └── utils.py                # Encryption, CSRF, session, audit
    ├── templates/
    │   ├── auth/login.html
    │   └── dashboard/index.html
    └── static/
        ├── css/
        │   ├── login.css
        │   └── dashboard.css
        └── js/
            └── dashboard.js
```

---

## Quick Start

### 1. Clone & install dependencies

```bash
git clone <repo-url>
cd drivex
python -m venv venv
source venv/bin/activate       # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
```

Edit `.env` and set your keys:

```bash
# Generate a secret key
python -c "import secrets; print(secrets.token_hex(32))"

# Generate a Fernet encryption key
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Paste the outputs into `.env`:

```env
SECRET_KEY=<your-secret-key>
ENCRYPTION_KEY=<your-fernet-key>
SESSION_TIMEOUT=3600
DEBUG=False
```

### 3. Run the app

```bash
python main.py
```

Then open [http://localhost:8000](http://localhost:8000) in your browser.

---

## AWS IAM Setup

Create an IAM user (or role) with the policy in `iam_policy.json`.

Replace `YOUR-BUCKET-NAME` with your actual bucket name(s). If using multiple buckets, duplicate the resource blocks:

```json
"Resource": [
  "arn:aws:s3:::bucket-one",
  "arn:aws:s3:::bucket-two"
]
```

Minimum required permissions:

| Permission | Purpose |
|---|---|
| `sts:GetCallerIdentity` | Validate credentials on login |
| `s3:ListBucket` | Browse bucket contents |
| `s3:GetBucketLocation` | Detect bucket region |
| `s3:GetObject` | Download files |
| `s3:PutObject` | Upload files |
| `s3:DeleteObject` | Delete files |
| `s3:CopyObject` | Copy / rename / move |
| `s3:HeadObject` | File metadata |
| `s3:*MultipartUpload*` | Large file uploads |

---

## Security Notes

| Concern | How DriveX handles it |
|---|---|
| Credential storage | Fernet-encrypted in server-side session only, never in DB or disk |
| Credential logging | Never logged — audit logs use the STS ARN identity instead |
| CSRF | Token validated on every mutating request (POST/DELETE) |
| Path traversal | `validate_s3_key()` rejects `../` and absolute paths |
| Bucket scope | Every API call checks the bucket is in the session's allowed list |
| Session expiry | Configurable timeout (default 1 hour), clears on expiry |
| File names | `sanitize_filename()` strips dangerous characters from uploads |
| HTTPS | Set `HTTPS_ONLY=true` in `.env` for production behind TLS |

---

## Production Deployment

### Run with Gunicorn + Uvicorn workers

```bash
pip install gunicorn
gunicorn main:app -w 4 -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:8000
```

### Run behind Nginx (recommended)

```nginx
server {
    listen 443 ssl;
    server_name yourdomain.com;

    ssl_certificate /path/to/cert.pem;
    ssl_certificate_key /path/to/key.pem;

    client_max_body_size 5G;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 300s;
        proxy_send_timeout 300s;
    }
}
```

### Environment for production

```env
SECRET_KEY=<strong-random-key>
ENCRYPTION_KEY=<fernet-key>
SESSION_TIMEOUT=3600
HTTPS_ONLY=true
DEBUG=False
MAX_UPLOAD_SIZE_MB=5000
```

---

## API Reference

All API routes are under `/api/s3/` and require an authenticated session.

| Method | Endpoint | Description |
|---|---|---|
| GET | `/api/s3/list` | List objects in bucket/prefix |
| GET | `/api/s3/search` | Search objects by name |
| GET | `/api/s3/download` | Download an object |
| GET | `/api/s3/presign` | Generate pre-signed URL |
| GET | `/api/s3/metadata` | Get object metadata |
| POST | `/api/s3/upload` | Upload a file |
| POST | `/api/s3/folder/create` | Create a folder |
| POST | `/api/s3/rename` | Rename an object |
| POST | `/api/s3/copy` | Copy an object |
| POST | `/api/s3/move` | Move an object |
| DELETE | `/api/s3/delete` | Delete an object or folder |

---

## Audit Logs

Logs are written to `logs/audit.log`. Each entry includes:

```
2024-01-15 10:23:45 | INFO | action=UPLOAD | identity=arn:aws:iam::123:user/alice | ip=192.168.1.1 | bucket=my-bucket | key=docs/report.pdf | status=SUCCESS | detail=size=204800
```

**Credentials are never included in any log entry.**

---

## License

MIT
