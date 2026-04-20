import os
import sys
import asyncio
import traceback
import json
import shutil
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# Add the current directory so myscoop can be imported
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from myscoop.cli import get_buckets_dir, _resolve_install_target, install_single_app, _remove_shortcut
from myscoop.cli import APPS_DIR, SHIMS_DIR
from myscoop.dependency import DependencyResolver
from myscoop.shim import ShimManager
from myscoop.manifest import Manifest, ManifestNotFoundError
from myscoop.uninstaller import uninstall_app as perform_uninstall, UninstallError

app = FastAPI(title="MakingScoop Manager API")

# Ensure static directory exists
os.makedirs("static", exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")

import re
import queue
import time
import threading

# Active client queues for broadcasting
active_clients_lock = threading.Lock()
active_clients = set()

# Ensure standard output also goes to our status_queue so the user sees it
class StreamLogger:
    def __init__(self, original):
        self.original = original
        self.ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')

    def write(self, message):
        self.original.write(message)
        if message.strip():
            clean_msg = self.ansi_escape.sub('', message.strip()).replace('\n', ' ').replace('\r', '')
            if clean_msg:
                # Thread-safe broadcast to all connected clients
                with active_clients_lock:
                    for q in active_clients:
                        try:
                            # non-blocking put to prevent a hung client from holding up the logger
                            q.put_nowait(clean_msg)
                        except queue.Full:
                            pass

    def flush(self):
        self.original.flush()

    def isatty(self):
        return getattr(self.original, "isatty", lambda: False)()

    def fileno(self):
        return self.original.fileno()

    @property
    def encoding(self):
        return getattr(self.original, "encoding", "utf-8")

    def __getattr__(self, name):
        return getattr(self.original, name)

# Replace stdout to capture click.echo prints used by myscoop/cli.py
sys.stdout = StreamLogger(sys.stdout)

def log_status(msg: str):
    print(msg)

class InstallRequest(BaseModel):
    path: str

class UninstallRequest(BaseModel):
    app: str

@app.get("/")
def serve_index():
    from fastapi.responses import FileResponse
    return FileResponse("static/index.html")

@app.get("/api/apps")
def list_apps():
    """List all installed applications."""
    if not os.path.exists(APPS_DIR):
        return {"apps": []}
    
    apps = []
    # Traverse APPS_DIR to find installed applications and their versions
    for app_name in sorted(os.listdir(APPS_DIR)):
        app_path = os.path.join(APPS_DIR, app_name)
        if not os.path.isdir(app_path):
            continue
        versions = [
            d for d in os.listdir(app_path)
            if os.path.isdir(os.path.join(app_path, d))
        ]
        if versions:
            version = sorted(versions)[-1]
            apps.append({"name": app_name, "version": version})

    return {"apps": apps}

def process_installation_queue_sync(target_path: str):
    """Background task to install one or many apps without failing the whole process."""
    try:
        path_obj = Path(target_path)
        if not path_obj.exists():
            log_status(f"Error: Path does not exist -> {target_path}")
            return
            
        exes = []
        if path_obj.is_dir():
            exes = [str(p) for p in path_obj.glob("*.exe")]
            log_status(f"Found {len(exes)} executable(s) in folder.")
        elif str(path_obj).lower().endswith(".exe"):
            exes = [str(path_obj)]
        else:
            log_status(f"Target '{target_path}' is not a directory or an .exe file.")
            return

        buckets_dir = get_buckets_dir()
        
        for exe_path in exes:
            log_status(f"\\n--- Starting install for: {exe_path} ---")
            try:
                import subprocess

                # Pass the exe path directly to the CLI.
                # The CLI's _resolve_install_target will find the best
                # matching manifest (e.g. 'abb' with gui installer type)
                # and handle the full install flow including GUI automation.
                cmd = [sys.executable, "myscoop.py", "install", str(exe_path)]
                log_status(f"Running cmd: {' '.join(cmd)}")
                
                # Execute CLI command as a subprocess
                process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    cwd=os.path.dirname(os.path.abspath(__file__))
                )
                
                # Stream logs live to connected clients
                for line in process.stdout:
                    if line:
                        log_status(line.strip())
                        
                process.wait()
                
                if process.returncode == 0:
                    log_status(f"Successfully installed: {exe_path}")
                else:
                    log_status(f"Failed to install '{exe_path}': process returned {process.returncode}")
                    
            except Exception as e:
                # Catch error so it continues to next app
                log_status(f"Failed to install '{exe_path}': {str(e)}")
                # traceback.print_exc()

        log_status("\\n--- All installations complete! ---")
        
    except Exception as e:
        log_status(f"Critical error during bulk install: {e}")

from fastapi.concurrency import run_in_threadpool

@app.post("/api/install")
async def start_install(req: InstallRequest):
    """Enqueue installation of a file or folder of executables."""
    target_path = req.path
    # Run the installation wrapper in background so we don't block the API event loop
    asyncio.create_task(run_in_threadpool(process_installation_queue_sync, target_path))
    return {"message": "Installation started in background.", "path": target_path}

@app.post("/api/uninstall")
def uninstall_app(req: UninstallRequest):
    """Uninstall a given application name."""
    buckets_dir = get_buckets_dir()
    try:
        result = perform_uninstall(
            req.app,
            buckets_dir,
            APPS_DIR,
            SHIMS_DIR,
            _remove_shortcut,
            log=log_status,
        )
        return {"message": result["message"]}
    except UninstallError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.get("/api/install/status")
async def stream_status(request: Request):
    """Server-sent events for installation logs."""
    client_queue = queue.Queue(maxsize=1000)
    with active_clients_lock:
        active_clients.add(client_queue)

    async def event_generator():
        last_ping = time.time()
        try:
            while True:
                # Check if client disconnected
                if await request.is_disconnected():
                    break
                try:
                    # Polling approach
                    msg = client_queue.get_nowait()
                    # Ensure no control chars break SSE format
                    msg_clean = msg.replace('\\x1b', '').replace('\\n', ' ')
                    yield f"data: {msg_clean}\\n\\n"
                    last_ping = time.time()
                except queue.Empty:
                    # Periodic keep-alive ping
                    if time.time() - last_ping > 1.0:
                        yield "data: ping\\n\\n"
                        last_ping = time.time()
                    await asyncio.sleep(0.1)
        finally:
            with active_clients_lock:
                active_clients.discard(client_queue)

    return StreamingResponse(event_generator(), media_type="text/event-stream")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="127.0.0.1", port=8000, reload=True)
