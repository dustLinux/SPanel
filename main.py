import os
import sys
import pty
import json
import signal
import struct
import fcntl
import termios
import asyncio
import sqlite3
import secrets
import platform
import shutil
import tarfile
from datetime import datetime
from collections import deque
import subprocess as _sp
import socket
import re

import psutil
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, Response, Depends, HTTPException, UploadFile, File
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, RedirectResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import bcrypt as _bcrypt

def _hash_pw(password: str) -> str:
    return _bcrypt.hashpw(password.encode(), _bcrypt.gensalt()).decode()

def _check_pw(password: str, hashed: str) -> bool:
    return _bcrypt.checkpw(password.encode(), hashed.encode())

# ──────────────────────────────────────────────
# App
# ──────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app = FastAPI(title="Spanel")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[],  # Same-origin only; add specific origins if needed
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ──────────────────────────────────────────────
# SQLite Database
# ──────────────────────────────────────────────
DB_PATH = os.path.join(BASE_DIR, "spanel.db")

def _db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = _db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS sessions (
            token TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id)
        );
    """)
    # Create default admin if no users exist
    row = conn.execute("SELECT COUNT(*) as c FROM users").fetchone()
    if row["c"] == 0:
        h = _hash_pw("admin")
        conn.execute("INSERT INTO users (username, password_hash) VALUES (?, ?)", ("admin", h))
        conn.commit()
        print("=" * 50)
        print("  SPANEL — Default credentials created")
        print("  Username: admin")
        print("  Password: admin")
        print("  ⚠ Change the password after first login!")
        print("=" * 50)
    conn.close()

init_db()

# ──────────────────────────────────────────────
# Config & Settings
# ──────────────────────────────────────────────
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")

def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r") as f:
            cfg = json.load(f)
        # Ensure new keys exist
        cfg.setdefault("panel_name", "Spanel")
        cfg.setdefault("theme", "dark")
        cfg.setdefault("terminal_theme", "tokyo_night")
        cfg.setdefault("floating_enabled", False)
        cfg.setdefault("osk_floating", True)
        cfg.setdefault("display_hostname", platform.node())
        cfg.setdefault("servers", [
            {"id": "local", "name": platform.node(), "url": "http://127.0.0.1:8000", "is_self": True}
        ])
        return cfg
    default = {
        "panel_name": "Spanel",
        "theme": "dark",
        "terminal_theme": "tokyo_night",
        "floating_enabled": False,
        "osk_floating": True,
        "display_hostname": platform.node(),
        "servers": [
            {"id": "local", "name": platform.node(), "url": "http://127.0.0.1:8000", "is_self": True}
        ]
    }
    save_config(default)
    return default

def save_config(cfg):
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)

config = load_config()

# ──────────────────────────────────────────────
# Security Helpers
# ──────────────────────────────────────────────
def safe_filename(name: str) -> str:
    """Strip path traversal from filenames."""
    name = os.path.basename(name)
    name = re.sub(r'[\.]{2,}', '.', name)  # collapse ..
    return name.lstrip('.')

def safe_extract(tar, path):
    """Extract tarball with tar-slip protection."""
    abs_path = os.path.abspath(path)
    for member in tar.getmembers():
        member_path = os.path.abspath(os.path.join(path, member.name))
        if not member_path.startswith(abs_path + os.sep) and member_path != abs_path:
            raise Exception(f"Blocked tar slip attempt: {member.name}")
    tar.extractall(path)

# ──────────────────────────────────────────────
# Auth helpers
# ──────────────────────────────────────────────
PUBLIC_PATHS = {"/login", "/api/auth/login"}
PUBLIC_PREFIXES = ("/static/",)

def get_current_user(request: Request):
    token = request.cookies.get("spanel_token")
    if not token:
        return None
    conn = _db()
    row = conn.execute(
        "SELECT u.id, u.username FROM sessions s JOIN users u ON s.user_id = u.id WHERE s.token = ?",
        (token,)
    ).fetchone()
    conn.close()
    if row:
        return {"id": row["id"], "username": row["username"]}
    return None

@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    path = request.url.path
    # Allow public paths
    if path in PUBLIC_PATHS or any(path.startswith(p) for p in PUBLIC_PREFIXES):
        return await call_next(request)
    # Allow WebSocket upgrades (they have their own auth check possibility)
    if request.headers.get("upgrade", "").lower() == "websocket":
        return await call_next(request)
    # Check auth
    user = get_current_user(request)
    if not user:
        if path.startswith("/api/"):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return RedirectResponse("/login")
    request.state.user = user
    return await call_next(request)

# ──────────────────────────────────────────────
# Auth API
# ──────────────────────────────────────────────
class LoginRequest(BaseModel):
    username: str
    password: str

class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str

@app.post("/api/auth/login")
async def api_login(req: LoginRequest, response: Response):
    conn = _db()
    row = conn.execute("SELECT id, password_hash FROM users WHERE username = ?", (req.username,)).fetchone()
    if not row or not _check_pw(req.password, row["password_hash"]):
        conn.close()
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token = secrets.token_hex(32)
    conn.execute("INSERT INTO sessions (token, user_id) VALUES (?, ?)", (token, row["id"]))
    conn.commit()
    conn.close()
    response.set_cookie("spanel_token", token, httponly=True, samesite="lax", max_age=86400 * 30)
    return {"ok": True, "username": req.username}

@app.post("/api/auth/logout")
async def api_logout(request: Request, response: Response):
    token = request.cookies.get("spanel_token")
    if token:
        conn = _db()
        conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
        conn.commit()
        conn.close()
    response.delete_cookie("spanel_token")
    return {"ok": True}

@app.get("/api/auth/me")
async def api_me(request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401)
    return user

@app.post("/api/auth/password")
async def api_change_password(req: ChangePasswordRequest, request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401)
    conn = _db()
    row = conn.execute("SELECT password_hash FROM users WHERE id = ?", (user["id"],)).fetchone()
    if not _check_pw(req.current_password, row["password_hash"]):
        conn.close()
        raise HTTPException(status_code=400, detail="Current password is wrong")
    conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (_hash_pw(req.new_password), user["id"]))
    conn.commit()
    conn.close()
    return {"ok": True}

# ──────────────────────────────────────────────
# Settings API
# ──────────────────────────────────────────────
class SettingsUpdate(BaseModel):
    panel_name: str | None = None
    theme: str | None = None
    terminal_theme: str | None = None
    floating_enabled: bool | None = None
    osk_floating: bool | None = None

@app.get("/api/settings")
async def api_get_settings():
    return {
        "panel_name": config.get("panel_name", "Spanel"),
        "theme": config.get("theme", "dark"),
        "terminal_theme": config.get("terminal_theme", "tokyo_night"),
        "floating_enabled": config.get("floating_enabled", False),
        "osk_floating": config.get("osk_floating", True),
    }

@app.post("/api/settings")
async def api_update_settings(req: SettingsUpdate):
    if req.panel_name is not None:
        config["panel_name"] = req.panel_name
    if req.theme is not None:
        config["theme"] = req.theme
    if req.terminal_theme is not None:
        config["terminal_theme"] = req.terminal_theme
    if req.floating_enabled is not None:
        config["floating_enabled"] = req.floating_enabled
    if req.osk_floating is not None:
        config["osk_floating"] = req.osk_floating
    save_config(config)
    return {"ok": True}

# ──────────────────────────────────────────────
# System Metrics
# ──────────────────────────────────────────────
history_data = deque(maxlen=120)

class RenameRequest(BaseModel):
    new_name: str

class ServerAddRequest(BaseModel):
    name: str
    url: str

class ServerRemoveRequest(BaseModel):
    server_id: str

def get_memory_usage():
    try:
        with open("/sys/fs/cgroup/memory.current") as f:
            used = int(f.read().strip())
        with open("/sys/fs/cgroup/memory.max") as f:
            raw = f.read().strip()
        total = int(raw) if raw != "max" else psutil.virtual_memory().total
        return {"used": used, "total": total, "percent": round(used / total * 100, 2)}
    except Exception:
        m = psutil.virtual_memory()
        return {"used": m.used, "total": m.total, "percent": m.percent}

def get_system_stats():
    freq = psutil.cpu_freq()
    return {
        "timestamp": datetime.now().isoformat(),
        "cpu": {
            "percent": psutil.cpu_percent(interval=None),
            "cores": psutil.cpu_count(logical=True),
            "freq": f"{int(freq.current)} MHz" if freq else "N/A",
        },
        "memory": get_memory_usage(),
        "platform": "Linux",
        "physical_hostname": platform.node(),
        "display_hostname": config.get("display_hostname", platform.node()),
    }

# ──────────────────────────────────────────────
# Runtime Detection
# ──────────────────────────────────────────────
def _which(name: str) -> str | None:
    return shutil.which(name)

RUNTIMES: dict = {}

def _detect_runtimes():
    global RUNTIMES
    RUNTIMES = {
        "proot":  _which("proot")  is not None,
        "docker": _which("docker") is not None,
        "podman": _which("podman") is not None,
    }
    available = [k for k, v in RUNTIMES.items() if v]
    print(f"[spanel] Detected runtimes: {available or ['none']}")

_detect_runtimes()

@app.get("/api/runtimes")
async def api_runtimes():
    return RUNTIMES

# ──────────────────────────────────────────────
# Container isolation helpers
# ──────────────────────────────────────────────
# Clean, fully-isolated environment for proot processes.
# Nothing from the host leaks in.
ISOLATED_ENV = {
    "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
    "TERM": "xterm-256color",
    "HOME": "/root",
    "USER": "root",
    "SHELL": "/bin/sh",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
}

def _build_proot_cmd(
    rootfs_path: str,
    exe: str = "/bin/sh",
    args: list[str] | None = None,
    mem_mb: int = 0,
    cpu_pct: int = 0,
) -> list[str]:
    """
    Build a proot command that:
      - Uses -S rootfs (sets up /dev /proc /sys automatically)
      - Does NOT bind the host BASE_DIR (panel data stays hidden)
      - Sets workdir to /root
      - Passes through /tmp as a fresh tmpfs-like bind
    """
    cmd = [
        "proot",
        "-S", rootfs_path,     # emulates setuid (sets up /dev /proc /sys binds)
        "-w", "/root",          # initial workdir inside container
        # do NOT add --bind for BASE_DIR  → host panel files invisible
    ]
    cmd += [exe] + (args or [])
    return cmd

def _apply_resource_limits(mem_mb: int, cpu_pct: int):
    """
    Called in the child process after fork().
    Applies soft resource limits using the `resource` module.
    """
    try:
        import resource as _res
        if mem_mb > 0:
            limit_bytes = mem_mb * 1024 * 1024
            _res.setrlimit(_res.RLIMIT_AS, (limit_bytes, limit_bytes))
        # RLIMIT_CPU is a hard cap in seconds; translate pct to a rough cap
        # (100% = unlimited, 10% ≈ 10 sec per 100 real-sec)
        if cpu_pct > 0 and cpu_pct < 100:
            cpu_hard = max(1, int(300 * cpu_pct / 100))  # up to 5-min window
            _res.setrlimit(_res.RLIMIT_CPU, (cpu_hard, cpu_hard))
    except Exception:
        pass  # resource limits are best-effort on non-Linux

async def proot_supervisor(cid: str, name: str, rootfs_path: str, port: int, mem_mb: int = 0, cpu_pct: int = 0):
    while True:
        try:
            cmd = _build_proot_cmd(rootfs_path, "/bin/manager", ["--port", str(port)])
            print(f"[spanel/{name}] Starting proot manager on port {port}")
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                env=ISOLATED_ENV,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            _active_containers[cid]["pid"] = proc.pid
            _active_containers[cid]["status"] = "running"
            await proc.wait()
            print(f"[spanel/{name}] Manager exited ({proc.returncode}). Restarting in 5s")
        except FileNotFoundError:
            print(f"[spanel/{name}] proot not found")
            _active_containers[cid]["status"] = "error: proot missing"
        except Exception as e:
            _active_containers[cid]["status"] = f"error: {e}"

        _active_containers[cid]["pid"] = None
        if _active_containers[cid].get("stopped_by_user"):
            break
        await asyncio.sleep(5)

@app.on_event("startup")
async def _startup():
    psutil.cpu_percent(interval=None)

    # Reload existing saved containers
    for cid, cinfo in config.get("saved_containers", {}).items():
        _active_containers[cid] = cinfo.copy()
        _active_containers[cid]["status"] = "starting"
        _active_containers[cid]["stopped_by_user"] = False
        asyncio.create_task(proot_supervisor(
            cid, cinfo["name"], cinfo["path"], cinfo["port"],
            mem_mb=cinfo.get("mem_mb", 0), cpu_pct=cinfo.get("cpu_pct", 0),
        ))

    async def _record():
        while True:
            history_data.append(get_system_stats())
            await asyncio.sleep(2)
    asyncio.create_task(_record())

@app.get("/api/realtime")
async def api_realtime():
    return get_system_stats()

@app.get("/api/historical")
async def api_historical():
    return list(history_data)

@app.post("/api/host/rename")
async def api_rename(req: RenameRequest):
    config["display_hostname"] = req.new_name
    for s in config.get("servers", []):
        if s.get("is_self"):
            s["name"] = req.new_name
    save_config(config)
    return {"ok": True}

@app.get("/api/servers")
async def api_servers():
    return config.get("servers", [])

@app.post("/api/servers/add")
async def api_add_server(req: ServerAddRequest):
    sid = "srv_" + os.urandom(4).hex()
    entry = {"id": sid, "name": req.name, "url": req.url.rstrip("/"), "is_self": False}
    config.setdefault("servers", []).append(entry)
    save_config(config)
    return entry

@app.post("/api/servers/remove")
async def api_remove_server(req: ServerRemoveRequest):
    config["servers"] = [s for s in config.get("servers", []) if s["id"] != req.server_id]
    save_config(config)
    return {"ok": True}

# ──────────────────────────────────────────────
# Processes API
# ──────────────────────────────────────────────
_managed_procs = {}

@app.get("/api/processes")
async def api_processes():
    procs = []
    # Fetch top 30 system processes by CPU
    for p in sorted(psutil.process_iter(['pid', 'name', 'username', 'cpu_percent', 'memory_percent', 'cmdline']), 
                    key=lambda x: x.info.get('cpu_percent', 0) or 0, reverse=True)[:30]:
        try:
            procs.append({
                "pid": p.info['pid'],
                "user": p.info['username'] or '--',
                "name": p.info['name'],
                "cpu": round(p.info['cpu_percent'] or 0, 1),
                "mem": round(p.info['memory_percent'] or 0, 1),
                "cmd": " ".join(p.info['cmdline'] or [])[:100]
            })
        except: pass
    return procs

@app.get("/api/processes/managed")
async def api_managed_processes():
    res = []
    for k, v in list(_managed_procs.items()):
        alive = v['proc'].poll() is None
        res.append({
            "id": k,
            "name": v['name'],
            "command": v['cmd'],
            "pid": v['proc'].pid if alive else None,
            "alive": alive,
            "rc": v['proc'].returncode
        })
    return res

class ProcessStartReq(BaseModel):
    name: str
    command: str

@app.post("/api/processes/start")
async def api_start_process(req: ProcessStartReq):
    import shlex
    try:
        args = shlex.split(req.command)
        proc = _sp.Popen(args, stdout=_sp.DEVNULL, stderr=_sp.DEVNULL, cwd=BASE_DIR)
        pid_id = secrets.token_hex(4)
        _managed_procs[pid_id] = {"name": req.name, "cmd": req.command, "proc": proc}
        return {"ok": True, "id": pid_id}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

class ProcessSignalReq(BaseModel):
    proc_id: str
    sig: str

@app.post("/api/processes/signal")
async def api_signal_process(req: ProcessSignalReq):
    if req.proc_id not in _managed_procs:
        raise HTTPException(status_code=404, detail="Not found")
    proc = _managed_procs[req.proc_id]["proc"]
    try:
        if req.sig == "TERM":
            proc.terminate()
        elif req.sig == "KILL":
            proc.kill()
        elif req.sig == "HUP":
            proc.send_signal(signal.SIGHUP)
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/processes/kill/{pid}")
async def api_kill_sys_process(pid: int):
    try:
        os.kill(pid, signal.SIGKILL)
        return {"ok": True}
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to kill")

# ──────────────────────────────────────────────
# Files API
# ──────────────────────────────────────────────
import posixpath
SHARED_DIR = os.path.join(BASE_DIR, "shared")
os.makedirs(SHARED_DIR, exist_ok=True)

@app.get("/api/files")
async def api_list_files():
    def _scan():
        res = []
        for f in os.listdir(SHARED_DIR):
            p = os.path.join(SHARED_DIR, f)
            if os.path.isfile(p):
                st = os.stat(p)
                res.append({"name": f, "size": st.st_size, "mtime": st.st_mtime * 1000})
        return sorted(res, key=lambda x: x["name"])
    return await asyncio.to_thread(_scan)

@app.post("/api/files/upload")
async def api_upload_file(file: UploadFile = File(...)):
    fname = safe_filename(file.filename)
    if not fname:
        raise HTTPException(status_code=400, detail="Invalid filename")
    p = os.path.join(SHARED_DIR, fname)
    data = await file.read()
    await asyncio.to_thread(open(p, "wb").write, data)
    return {"ok": True}

class FileDeleteReq(BaseModel):
    filename: str

@app.post("/api/files/delete")
async def api_delete_file(req: FileDeleteReq):
    fname = safe_filename(req.filename)
    p = os.path.join(SHARED_DIR, fname)
    if os.path.exists(p) and os.path.isfile(p):
        os.remove(p)
    return {"ok": True}

class FileRenameReq(BaseModel):
    old_name: str
    new_name: str

@app.post("/api/files/rename")
async def api_rename_file(req: FileRenameReq):
    from pathlib import Path
    old = safe_filename(req.old_name)
    new = safe_filename(req.new_name)
    op = Path(SHARED_DIR) / old
    np_ = Path(SHARED_DIR) / new
    if op.exists() and op.is_file():
        op.rename(np_)
    return {"ok": True}

@app.get("/api/files/download/{filename}")
async def api_download_file(filename: str):
    fname = safe_filename(filename)
    p = os.path.join(SHARED_DIR, fname)
    if os.path.exists(p) and os.path.isfile(p):
        return FileResponse(p, filename=fname)
    raise HTTPException(status_code=404)

# ──────────────────────────────────────────────
# Containers API
# ──────────────────────────────────────────────
CONTAINERS_DIR = os.path.join(BASE_DIR, "containers")
ROOTFS_DIR = os.path.join(BASE_DIR, "rootfs")
os.makedirs(CONTAINERS_DIR, exist_ok=True)
os.makedirs(ROOTFS_DIR, exist_ok=True)

_active_containers = {}

def get_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]

@app.get("/api/rootfs")
async def api_rootfs_list():
    res = []
    for f in os.listdir(ROOTFS_DIR):
        p = os.path.join(ROOTFS_DIR, f)
        if os.path.isfile(p) and (f.endswith('.tar.gz') or f.endswith('.tar.xz')):
            res.append({"name": f})
    return res

@app.get("/api/containers")
async def api_containers():
    res = []
    for cid, cinfo in _active_containers.items():
        res.append({
            "id": cid,
            "name": cinfo["name"],
            "distro": cinfo.get("distro", "Custom"),
            "status": cinfo.get("status", "unknown"),
            "port": cinfo.get("port"),
            "runtime": cinfo.get("runtime", "proot"),
            "mem_mb": cinfo.get("mem_mb", 0),
            "cpu_pct": cinfo.get("cpu_pct", 0),
        })
    return res

class ContainerCreateReq(BaseModel):
    name: str
    tarball: str
    entrypoint: str = "/bin/sh"
    cmd: str = ""
    mem_mb: int = 0    # 0 = unlimited
    cpu_pct: int = 0   # 0 = unlimited

@app.post("/api/containers/create")
async def api_create_container(req: ContainerCreateReq):
    # Choose runtime: prefer docker > podman > proot
    if RUNTIMES.get("docker"):
        runtime = "docker"
    elif RUNTIMES.get("podman"):
        runtime = "podman"
    elif RUNTIMES.get("proot"):
        runtime = "proot"
    else:
        raise HTTPException(status_code=500, detail="No supported container runtime found (install proot, docker or podman)")

    tarball_name = safe_filename(req.tarball)
    container_name = re.sub(r'[^a-zA-Z0-9_\-]', '', req.name)
    if not tarball_name or not container_name:
        raise HTTPException(status_code=400, detail="Invalid name or tarball")
    tar_path = os.path.join(ROOTFS_DIR, tarball_name)
    if not os.path.isfile(tar_path):
        raise HTTPException(status_code=404, detail="Tarball not found in rootfs/")

    cid = "ct_" + secrets.token_hex(4)
    rootfs_path = os.path.join(CONTAINERS_DIR, container_name)

    if os.path.exists(rootfs_path):
        raise HTTPException(status_code=400, detail="Container path already exists")

    await asyncio.to_thread(os.makedirs, rootfs_path)

    try:
        def _extract():
            with tarfile.open(tar_path, "r:*") as tar:
                safe_extract(tar, rootfs_path)
        await asyncio.to_thread(_extract)
    except Exception as e:
        await asyncio.to_thread(shutil.rmtree, rootfs_path, True)
        raise HTTPException(status_code=500, detail=f"Extraction failed: {e}")

    port = get_free_port()
    cinfo = {
        "id": cid,
        "name": req.name,
        "path": rootfs_path,
        "port": port,
        "distro": req.tarball,
        "entrypoint": req.entrypoint,
        "cmd": req.cmd,
        "runtime": runtime,
        "mem_mb": req.mem_mb,
        "cpu_pct": req.cpu_pct,
        "status": "starting",
    }

    _active_containers[cid] = cinfo

    config.setdefault("saved_containers", {})[cid] = {k: v for k, v in cinfo.items() if k != "task"}
    await asyncio.to_thread(save_config, config)

    asyncio.create_task(proot_supervisor(cid, req.name, rootfs_path, port, req.mem_mb, req.cpu_pct))

    return {"ok": True, "id": cid, "port": port, "runtime": runtime}

@app.websocket("/ws/container/{cid}")
async def ws_container_shell(websocket: WebSocket, cid: str):
    await websocket.accept()
    if cid not in _active_containers:
        await websocket.close()
        return

    ct = _active_containers[cid]
    rootfs_path = ct["path"]
    entrypoint = ct.get("entrypoint", "/bin/sh")
    mem_mb = ct.get("mem_mb", 0)
    cpu_pct = ct.get("cpu_pct", 0)
    cmd = _build_proot_cmd(rootfs_path, entrypoint)

    master_fd, slave_fd = pty.openpty()
    _set_pty_size(master_fd, 24, 80)

    pid = os.fork()
    if pid == 0:
        # Child: apply resource limits, then exec proot with clean-room env
        os.close(master_fd)
        os.setsid()
        fcntl.ioctl(slave_fd, termios.TIOCSCTTY, 0)
        os.dup2(slave_fd, 0); os.dup2(slave_fd, 1); os.dup2(slave_fd, 2)
        if slave_fd > 2:
            os.close(slave_fd)
        _apply_resource_limits(mem_mb, cpu_pct)
        os.execvpe(cmd[0], cmd, ISOLATED_ENV)
        sys.exit(1)

    os.close(slave_fd)
    flags = fcntl.fcntl(master_fd, fcntl.F_GETFL)
    fcntl.fcntl(master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

    loop = asyncio.get_event_loop()
    read_event = asyncio.Event()
    def _rdr(): read_event.set()
    loop.add_reader(master_fd, _rdr)

    async def _read_pty():
        try:
            while True:
                await read_event.wait(); read_event.clear()
                while True:
                    try:
                        d = os.read(master_fd, 4096)
                        if not d: break
                        await websocket.send_text(d.decode("utf-8", "replace"))
                    except BlockingIOError: break
        except Exception: pass
        finally: loop.remove_reader(master_fd)

    async def _write_pty():
        try:
            while True:
                d = await websocket.receive_text()
                if d.startswith("\x1b[8;") and d.endswith("t"):
                    try:
                        p = d[4:-1].split(";")
                        _set_pty_size(master_fd, int(p[0]), int(p[1]))
                    except Exception: pass
                else:
                    os.write(master_fd, d.encode())
        except Exception: pass

    rt, wt = asyncio.create_task(_read_pty()), asyncio.create_task(_write_pty())
    await asyncio.wait([rt, wt], return_when=asyncio.FIRST_COMPLETED)
    try: os.kill(pid, signal.SIGTERM)
    except: pass
    try: os.close(master_fd)
    except: pass

# ──────────────────────────────────────────────
# Sandbox API
# ──────────────────────────────────────────────
@app.post("/api/sandbox/launch")
async def api_sandbox_launch():
    tarball = "alpine.tar.gz"
    tar_path = os.path.join(ROOTFS_DIR, tarball)
    if not await asyncio.to_thread(os.path.isfile, tar_path):
        raise HTTPException(status_code=404, detail="alpine.tar.gz not found in rootfs/")

    cid = "sandbox"
    rootfs_path = os.path.join(CONTAINERS_DIR, cid)

    if cid not in _active_containers or not os.path.isdir(rootfs_path):
        await asyncio.to_thread(os.makedirs, rootfs_path, 0o755, True)
        def _extract():
            with tarfile.open(tar_path, "r:*") as tar:
                safe_extract(tar, rootfs_path)
        await asyncio.to_thread(_extract)
        _active_containers[cid] = {
            "name": "Sandbox", "distro": tarball, "path": rootfs_path,
            "port": 0, "status": "running", "pid": None,
            "runtime": "proot", "mem_mb": 0, "cpu_pct": 0,
        }

    sid = "sbx_" + secrets.token_hex(4)
    return {"status": "ok", "session_id": sid, "container": cid}

@app.websocket("/ws/sandbox/{session_id}")
async def ws_sandbox(websocket: WebSocket, session_id: str):
    await websocket.accept()
    if "sandbox" not in _active_containers:
        await websocket.close()
        return

    rootfs_path = os.path.join(CONTAINERS_DIR, "sandbox")
    cmd = _build_proot_cmd(rootfs_path, "/bin/sh")

    master_fd, slave_fd = pty.openpty()
    _set_pty_size(master_fd, 24, 80)

    pid = os.fork()
    if pid == 0:
        os.close(master_fd)
        os.setsid()
        fcntl.ioctl(slave_fd, termios.TIOCSCTTY, 0)
        os.dup2(slave_fd, 0); os.dup2(slave_fd, 1); os.dup2(slave_fd, 2)
        if slave_fd > 2:
            os.close(slave_fd)
        os.execvpe(cmd[0], cmd, ISOLATED_ENV)
        sys.exit(1)

    os.close(slave_fd)
    flags = fcntl.fcntl(master_fd, fcntl.F_GETFL)
    fcntl.fcntl(master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

    loop = asyncio.get_event_loop()
    read_event = asyncio.Event()
    def _rdr(): read_event.set()
    loop.add_reader(master_fd, _rdr)

    async def _read_pty():
        try:
            while True:
                await read_event.wait(); read_event.clear()
                while True:
                    try:
                        d = os.read(master_fd, 4096)
                        if not d: break
                        await websocket.send_text(d.decode("utf-8", "replace"))
                    except BlockingIOError: break
        except Exception: pass
        finally: loop.remove_reader(master_fd)

    async def _write_pty():
        try:
            while True:
                d = await websocket.receive_text()
                if d.startswith("\x1b[8;") and d.endswith("t"):
                    try:
                        p = d[4:-1].split(";")
                        _set_pty_size(master_fd, int(p[0]), int(p[1]))
                    except Exception: pass
                else:
                    os.write(master_fd, d.encode())
        except Exception: pass

    rt, wt = asyncio.create_task(_read_pty()), asyncio.create_task(_write_pty())
    await asyncio.wait([rt, wt], return_when=asyncio.FIRST_COMPLETED)
    try: os.kill(pid, signal.SIGTERM)
    except: pass
    try: os.close(master_fd)
    except: pass

# ──────────────────────────────────────────────
# PTY Terminal (Linux only)
# ──────────────────────────────────────────────
_active_sessions: dict = {}

def _set_pty_size(fd, rows, cols):
    s = struct.pack("HHHH", rows, cols, 0, 0)
    fcntl.ioctl(fd, termios.TIOCSWINSZ, s)

@app.websocket("/ws/terminal/{session_id}")
async def ws_terminal(websocket: WebSocket, session_id: str):
    await websocket.accept()
    loop = asyncio.get_event_loop()

    master_fd, slave_fd = pty.openpty()
    _set_pty_size(master_fd, 24, 80)

    pid = os.fork()
    if pid == 0:
        os.close(master_fd)
        os.setsid()
        fcntl.ioctl(slave_fd, termios.TIOCSCTTY, 0)
        os.dup2(slave_fd, 0)
        os.dup2(slave_fd, 1)
        os.dup2(slave_fd, 2)
        if slave_fd > 2:
            os.close(slave_fd)
        env = os.environ.copy()
        env["TERM"] = "xterm-256color"
        os.execvpe("/bin/bash", ["/bin/bash", "-l"], env)
        sys.exit(1)

    os.close(slave_fd)
    _active_sessions[session_id] = {"pid": pid, "fd": master_fd}

    flags = fcntl.fcntl(master_fd, fcntl.F_GETFL)
    fcntl.fcntl(master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

    read_event = asyncio.Event()
    def _on_readable():
        read_event.set()
    loop.add_reader(master_fd, _on_readable)

    async def _reader():
        try:
            while True:
                await read_event.wait()
                read_event.clear()
                try:
                    while True:
                        data = os.read(master_fd, 4096)
                        if not data:
                            return
                        await websocket.send_text(data.decode(errors="replace"))
                except BlockingIOError:
                    pass
                except OSError:
                    return
        except Exception:
            pass

    reader_task = asyncio.create_task(_reader())

    try:
        while True:
            msg = await websocket.receive_text()
            if msg.startswith("\x1b[8;") and msg.endswith("t"):
                try:
                    parts = msg[4:-1].split(";")
                    rows, cols = int(parts[0]), int(parts[1])
                    _set_pty_size(master_fd, rows, cols)
                except Exception:
                    pass
                continue
            os.write(master_fd, msg.encode())
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        reader_task.cancel()
        try:
            loop.remove_reader(master_fd)
        except Exception:
            pass
        try:
            os.close(master_fd)
        except OSError:
            pass
        try:
            os.kill(pid, signal.SIGKILL)
            await asyncio.sleep(0.05)
            os.waitpid(pid, os.WNOHANG)
        except OSError:
            pass
        _active_sessions.pop(session_id, None)
        try:
            await websocket.close()
        except RuntimeError:
            pass

# ──────────────────────────────────────────────
# Static / Pages
# ──────────────────────────────────────────────
static_dir = os.path.join(BASE_DIR, "static")
app.mount("/static", StaticFiles(directory=static_dir), name="static")

@app.get("/")
async def root():
    return FileResponse(os.path.join(static_dir, "index.html"))

@app.get("/login")
async def login_page():
    return FileResponse(os.path.join(static_dir, "login.html"))
