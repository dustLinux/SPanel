"""
Spanel Headless Server — API-only, no web UI.
Provides system stats, PTY terminal, processes, and files.

Usage:
    python3 -m uvicorn server:app --host 0.0.0.0 --port 8001
"""
import os
import sys
import pty
import json
import signal
import struct
import fcntl
import termios
import asyncio
import platform
import shutil
import socket
import secrets
import tarfile
import subprocess as _sp
from datetime import datetime
from collections import deque

import psutil
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, HTTPException
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI(title="Spanel Headless Node")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[],  # Same-origin only
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

import re

def safe_filename(name: str) -> str:
    """Strip path traversal from filenames."""
    name = os.path.basename(name)
    name = re.sub(r'[\.]{2,}', '.', name)
    return name.lstrip('.')

def safe_extract(tar, path):
    """Extract tarball with tar-slip protection."""
    abs_path = os.path.abspath(path)
    for member in tar.getmembers():
        member_path = os.path.abspath(os.path.join(path, member.name))
        if not member_path.startswith(abs_path + os.sep) and member_path != abs_path:
            raise Exception(f"Blocked tar slip: {member.name}")
    tar.extractall(path)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")

def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r") as f:
            return json.load(f)
    return {"display_hostname": platform.node()}

config = load_config()
history_data = deque(maxlen=120)

# ──────────────────────────────────────────────
# System Metrics
# ──────────────────────────────────────────────
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

@app.on_event("startup")
async def _startup():
    psutil.cpu_percent(interval=None)
    
    # Reload existing saved containers
    for cid, cinfo in config.get("saved_containers", {}).items():
        _active_containers[cid] = cinfo.copy()
        _active_containers[cid]["status"] = "starting"
        _active_containers[cid]["stopped_by_user"] = False
        loop = asyncio.get_event_loop()
        task = loop.create_task(proot_supervisor(cid, cinfo["name"], cinfo["path"], cinfo["port"]))
        _active_containers[cid]["task"] = task

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

# ──────────────────────────────────────────────
# Container Supervisor (server.py)
# ──────────────────────────────────────────────
_active_containers = {}
CONTAINERS_DIR = os.path.join(BASE_DIR, "containers")
os.makedirs(CONTAINERS_DIR, exist_ok=True)

async def proot_supervisor(cid: str, name: str, rootfs_path: str, port: int):
    while True:
        try:
            cmd = ["proot", "-S", rootfs_path, "/bin/manager", "--port", str(port)]
            print(f"[{name}] Starting proot manager on port {port}...")
            proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
            _active_containers[cid]["pid"] = proc.pid
            _active_containers[cid]["status"] = "running"
            await proc.wait()
            print(f"[{name}] Manager exited with {proc.returncode}. Restarting in 5s...")
        except FileNotFoundError:
            print(f"[{name}] proot not found, please apt-get install proot")
            _active_containers[cid]["status"] = "error (proot missing)"
        except Exception as e:
            print(f"[{name}] Supervisor error: {e}")
            _active_containers[cid]["status"] = f"error ({e})"
        
        _active_containers[cid]["pid"] = None
        if _active_containers[cid].get("stopped_by_user"):
            break
        await asyncio.sleep(5)

def save_config(cfg):
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)

def get_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]

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
# Process Management
# ──────────────────────────────────────────────
_managed_procs: dict = {}

class ProcessStartRequest(BaseModel):
    name: str
    command: str
    cwd: str | None = None

class ProcessSignalRequest(BaseModel):
    proc_id: str
    sig: str = "TERM"

@app.get("/api/processes")
async def api_processes():
    procs = []
    for p in psutil.process_iter(['pid','name','username','cpu_percent','memory_percent','status','cmdline']):
        try:
            info = p.info
            procs.append({
                "pid": info['pid'],
                "name": info['name'],
                "user": info['username'] or '—',
                "cpu": round(info['cpu_percent'] or 0, 1),
                "mem": round(info['memory_percent'] or 0, 1),
                "status": info['status'],
                "cmd": ' '.join(info['cmdline'][:5]) if info['cmdline'] else info['name'],
            })
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    procs.sort(key=lambda x: x['cpu'], reverse=True)
    return procs

@app.get("/api/processes/managed")
async def api_managed():
    result = []
    for pid, info in list(_managed_procs.items()):
        alive = info["proc"].poll() is None
        result.append({
            "id": pid, "name": info["name"], "command": info["cmd"], "alive": alive,
            "pid": info["proc"].pid if alive else None, "returncode": info["proc"].returncode,
        })
    return result

@app.post("/api/processes/start")
async def api_start_process(req: ProcessStartRequest):
    try:
        proc = _sp.Popen(req.command, shell=True, cwd=req.cwd or BASE_DIR, stdout=_sp.DEVNULL, stderr=_sp.DEVNULL, start_new_session=True)
        pid = f"mp_{os.urandom(4).hex()}"
        _managed_procs[pid] = {"proc": proc, "cmd": req.command, "name": req.name}
        return {"ok": True, "id": pid, "pid": proc.pid}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/processes/signal")
async def api_signal_process(req: ProcessSignalRequest):
    info = _managed_procs.get(req.proc_id)
    if not info:
        raise HTTPException(status_code=404, detail="Process not found")
    sig_map = {"TERM": signal.SIGTERM, "KILL": signal.SIGKILL, "HUP": signal.SIGHUP}
    sig = sig_map.get(req.sig.upper(), signal.SIGTERM)
    try:
        os.kill(info["proc"].pid, sig)
        return {"ok": True}
    except ProcessLookupError:
        return {"ok": True, "note": "already dead"}

@app.post("/api/processes/kill/{pid}")
async def api_kill_system_process(pid: int):
    try:
        os.kill(pid, signal.SIGKILL)
        return {"ok": True}
    except ProcessLookupError:
        return {"ok": True, "note": "already dead"}
    except PermissionError:
        raise HTTPException(status_code=403, detail="Permission denied")

# ──────────────────────────────────────────────
# Files API
# ──────────────────────────────────────────────
SHARED_DIR = os.path.join(BASE_DIR, "shared")
os.makedirs(SHARED_DIR, exist_ok=True)

class FileDeleteRequest(BaseModel):
    filename: str

class FileRenameRequest(BaseModel):
    old_name: str
    new_name: str

@app.get("/api/files")
async def api_list_files():
    files = []
    for f in os.listdir(SHARED_DIR):
        p = os.path.join(SHARED_DIR, f)
        if os.path.isfile(p):
            stat = os.stat(p)
            files.append({"name": f, "size": stat.st_size, "mtime": datetime.fromtimestamp(stat.st_mtime).isoformat()})
    return sorted(files, key=lambda x: x["name"])

@app.get("/api/files/download/{filename}")
async def api_download_file(filename: str):
    p = os.path.join(SHARED_DIR, os.path.basename(filename))
    if not os.path.exists(p): raise HTTPException(404, "Not found")
    return FileResponse(p, filename=filename)

@app.post("/api/files/upload")
async def api_upload_file(request: Request):
    form = await request.form()
    file = form.get("file")
    if not file: raise HTTPException(400, "No file")
    fname = safe_filename(file.filename)
    if not fname: raise HTTPException(400, "Invalid filename")
    p = os.path.join(SHARED_DIR, fname)
    with open(p, "wb") as f: shutil.copyfileobj(file.file, f)
    return {"ok": True}

@app.post("/api/files/delete")
async def api_delete_file(req: FileDeleteRequest):
    fname = safe_filename(req.filename)
    p = os.path.join(SHARED_DIR, fname)
    if os.path.exists(p): os.remove(p)
    return {"ok": True}

@app.post("/api/files/rename")
async def api_rename_file(req: FileRenameRequest):
    old = safe_filename(req.old_name)
    new = safe_filename(req.new_name)
    p_old = os.path.join(SHARED_DIR, old)
    p_new = os.path.join(SHARED_DIR, new)
    if not os.path.exists(p_old): raise HTTPException(404, "Not found")
    os.rename(p_old, p_new)
    return {"ok": True}

# ──────────────────────────────────────────────
# Containers API
# ──────────────────────────────────────────────
CONTAINERS_DIR = os.path.join(BASE_DIR, "containers")
ROOTFS_DIR = os.path.join(BASE_DIR, "rootfs")
os.makedirs(CONTAINERS_DIR, exist_ok=True)
os.makedirs(ROOTFS_DIR, exist_ok=True)

_active_containers = {}

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
            "port": cinfo.get("port")
        })
    return res

class ContainerCreateReq(BaseModel):
    name: str
    tarball: str

@app.post("/api/containers/create")
async def api_create_container(req: ContainerCreateReq):
    import tarfile
    tar_path = os.path.join(ROOTFS_DIR, req.tarball)
    if not os.path.isfile(tar_path):
        raise HTTPException(status_code=404, detail="Tarball not found in rootfs/")
    
    cid = "ct_" + secrets.token_hex(4)
    rootfs_path = os.path.join(CONTAINERS_DIR, req.name)
    
    if os.path.exists(rootfs_path):
        raise HTTPException(status_code=400, detail="Container path already exists")
    
    os.makedirs(rootfs_path)
    
    try:
        with tarfile.open(tar_path, "r:*") as tar:
            tar.extractall(path=rootfs_path)
    except Exception as e:
        shutil.rmtree(rootfs_path, ignore_errors=True)
        raise HTTPException(status_code=500, detail=f"Extraction failed: {str(e)}")

    port = get_free_port()
    cinfo = {
        "id": cid,
        "name": req.name,
        "path": rootfs_path,
        "port": port,
        "distro": req.tarball,
        "status": "starting"
    }
    
    _active_containers[cid] = cinfo

    if "saved_containers" not in config:
        config["saved_containers"] = {}
    config["saved_containers"][cid] = cinfo.copy()
    save_config(config)

    loop = asyncio.get_event_loop()
    task = loop.create_task(proot_supervisor(cid, req.name, rootfs_path, port))
    _active_containers[cid]["task"] = task

    return {"ok": True, "id": cid, "port": port}

# ──────────────────────────────────────────────
# Sandbox API
# ──────────────────────────────────────────────
@app.post("/api/sandbox/launch")
async def api_sandbox_launch():
    tarball = "alpine.tar.gz"
    tar_path = os.path.join(ROOTFS_DIR, tarball)
    if not os.path.isfile(tar_path):
        raise HTTPException(status_code=404, detail="alpine.tar.gz not found in rootfs/")
    
    cid = "sandbox"
    rootfs_path = os.path.join(CONTAINERS_DIR, cid)
    
    if cid not in _active_containers or not os.path.isdir(rootfs_path):
        os.makedirs(rootfs_path, exist_ok=True)
        import tarfile
        with tarfile.open(tar_path, "r:*") as tar:
            tar.extractall(path=rootfs_path)
            
        _active_containers[cid] = {
            "name": "Sandbox", "distro": tarball, "path": rootfs_path, "port": 0, "status": "running", "pid": None
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
    
    import pty, struct, fcntl, termios
    master_fd, slave_fd = pty.openpty()
    
    try:
        winsize = struct.pack("HHHH", 24, 80, 0, 0)
        fcntl.ioctl(slave_fd, termios.TIOCSWINSZ, winsize)
    except Exception:
        pass

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
        os.execvpe("proot", ["proot", "-S", rootfs_path, "/bin/sh"], env)
        sys.exit(1)

    os.close(slave_fd)
    
    flags = fcntl.fcntl(master_fd, fcntl.F_GETFL)
    fcntl.fcntl(master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
    
    loop = asyncio.get_event_loop()
    read_event = asyncio.Event()

    def _reader(): read_event.set()
    loop.add_reader(master_fd, _reader)

    async def _read_pty():
        try:
            while True:
                await read_event.wait()
                read_event.clear()
                while True:
                    try:
                        data = os.read(master_fd, 4096)
                        if not data: break
                        await websocket.send_text(data.decode("utf-8", "replace"))
                    except BlockingIOError: break
        except Exception: pass
        finally: loop.remove_reader(master_fd)

    async def _write_pty():
        try:
            while True:
                data = await websocket.receive_text()
                if data.startswith("\x1b[8;") and data.endswith("t"):
                    try:
                        pts = data[4:-1].split(";")
                        _set_pty_size(master_fd, int(pts[0]), int(pts[1]))
                    except Exception: pass
                else: os.write(master_fd, data.encode("utf-8"))
        except Exception: pass

    rt, wt = asyncio.create_task(_read_pty()), asyncio.create_task(_write_pty())
    await asyncio.wait([rt, wt], return_when=asyncio.FIRST_COMPLETED)
    
    import signal
    try: os.kill(pid, signal.SIGTERM)
    except: pass
    os.close(master_fd)

@app.get("/")
async def root():
    return {
        "service": "Spanel Headless Node",
        "hostname": config.get("display_hostname", platform.node()),
        "status": "running"
    }
