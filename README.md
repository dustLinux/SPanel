# Spanel

A lightweight, self-hosted system monitoring and container management panel built with FastAPI + vanilla JS.

# WARNING: Vibe-coded project (im very lazy), so dont use it in prod

## Features

- **Dashboard** — Real-time CPU, Memory, Disk charts
- **Terminal** — Full PTY shell access via WebSocket (xterm.js)
- **Process Manager** — Start/stop/monitor processes
- **File Manager** — Upload, download, rename, delete files
- **Rootless Containers** — Deploy containers via proot (no root required)
- **Sandbox** — Instant Alpine Linux shell environment
- **Multi-node** — Connect multiple headless servers from one panel
- **Mobile-friendly** — Responsive UI with on-screen keyboard
- **Auth** — Session-based login with bcrypt password hashing

## Quick Start

### Bare Metal

```bash
pip install -r requirements.txt
python3 -m uvicorn main:app --host 0.0.0.0 --port 8000
```

Default credentials: `admin` / `admin` — **change after first login!**

### Docker

```bash
# Panel (full UI)
docker build -t spanel .
docker run -d -p 8000:8000 --name spanel spanel

# Headless node (API only)
docker build -f Dockerfile.server -t spanel-server .
docker run -d -p 8001:8001 --name spanel-node spanel-server
```

## Project Structure

```
├── main.py                 # Panel backend (UI + API + Auth)
├── server.py               # Headless node backend (API only)
├── static/
│   ├── index.html          # Main dashboard UI
│   ├── login.html          # Login page
│   ├── style.css           # Stylesheet
│   └── script.js           # Frontend logic
├── Dockerfile              # Panel Docker image
├── Dockerfile.server       # Headless node Docker image
├── requirements.txt        # Panel dependencies
└── requirements.server.txt # Headless node dependencies
```

## Architecture

```
┌─────────────────────────────────────────┐
│            Spanel Panel (main.py)       │
│  - Web UI (static/)                     │
│  - Auth (SQLite + bcrypt)               │
│  - System metrics (psutil)              │
│  - PTY terminal (WebSocket)             │
│  - Container supervisor (proot)         │
│  - File manager                         │
└─────────────┬────────────┬──────────────┘
              │            │
    ┌─────────▼──┐   ┌─────▼──────────┐
    │ Remote Node │   │ Remote Node    │
    │ (server.py) │   │ (server.py)    │
    └─────────────┘   └────────────────┘
```

## Sandbox

Alpine-based sandbox for testings

## Security
- ANOTHER WARNING: Vibe-Coded project.
- Path traversal protection on all file operations
- Tar-slip protection on archive extraction
- Session-based authentication with bcrypt
- CORS restricted to same-origin

## License

GPL
