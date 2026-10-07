"""Run the local Task 2 project, seed an empty graph, and export live Fuseki data."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from model_source import STORAGE_VERSION, model_info, model_path
from source_sync import SourceSync, atomic_write, source_lock
from local_services import free_port, port_available, owned_process, owns_listener

ROOT = Path(__file__).resolve().parent
CONFIG = json.loads((ROOT / "config.json").read_text("utf-8"))
NAMESPACE = "http://uow.edu.au/isit906/thestar/digitalcare#"
DATASET = CONFIG["dataset_url"].rstrip("/")
APP_URL = f"http://{CONFIG['app_host']}:{CONFIG['app_port']}"


def request(url, data=None, content_type=None, accept="application/sparql-results+json"):
    headers = {"Accept": accept}
    if content_type:
        headers["Content-Type"] = content_type
    with urllib.request.urlopen(urllib.request.Request(url, data=data, headers=headers), timeout=20) as response:
        return response.read()


def query(text):
    return json.loads(request(DATASET + "/query", text.encode(), "application/sparql-query"))


def ready(url):
    try:
        request(url)
        return True
    except (OSError, ValueError):
        return False


def wait_ready(url, process, name, command, host, port, seconds=45):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("Server exited during startup. Read logs/ for the cause.")
        if owns_listener(ROOT, name, command, host, port) and ready(url):
            return
        time.sleep(0.4)
    raise RuntimeError("Server startup timed out. Read logs/ for the cause.")


def spawn(name, command, env=None):
    (ROOT / "logs").mkdir(exist_ok=True)
    (ROOT / "data").mkdir(exist_ok=True)
    log = (ROOT / "logs" / (name + ".log")).open("ab")
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    process = subprocess.Popen(command, cwd=ROOT, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, creationflags=flags)
    log.close()
    created_filetime = None
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        created, ended, kernel, user = (wintypes.FILETIME() for _ in range(4))
        if ctypes.windll.kernel32.GetProcessTimes(wintypes.HANDLE(int(process._handle)), ctypes.byref(created), ctypes.byref(ended), ctypes.byref(kernel), ctypes.byref(user)):
            created_filetime = str((created.dwHighDateTime << 32) | created.dwLowDateTime)
    metadata = ROOT / "data" / "processes.json"
    saved = json.loads(metadata.read_text()) if metadata.exists() else {}
    saved[name] = {"pid": process.pid, "command": [str(x) for x in command], "project": str(ROOT), "createdFileTime": created_filetime}
    metadata.write_text(json.dumps(saved, indent=2), encoding="utf-8")
    return process


def fuseki_command():
    parsed = urllib.parse.urlsplit(DATASET)
    lock_path = ROOT / "runtime" / "runtime-lock.json"
    if not lock_path.exists():
        raise RuntimeError("Bundled runtime is missing. Extract the complete submission ZIP again.")
    lock = json.loads(lock_path.read_text())
    return [str(ROOT / lock["java"]["executable"]), "-Xmx512m", "-jar",
            str(ROOT / lock["fuseki"]["jar"]), "--localhost", f"--port={parsed.port or 3030}",
            "--update", "--tdb2", f"--loc={ROOT / 'data' / STORAGE_VERSION / 'tdb2'}", parsed.path]


def app_command():
    return [sys.executable, "-B", str(ROOT / "server.py"), "--host", CONFIG["app_host"],
            "--port", str(CONFIG["app_port"]), "--dataset", DATASET]


def configure_local_ports():
    """Fresh extractions get their own endpoints, never another copy's data."""
    global CONFIG, DATASET, APP_URL
    CONFIG = json.loads((ROOT / "config.json").read_text("utf-8-sig"))
    DATASET = CONFIG["dataset_url"].rstrip("/")
    original = dict(CONFIG)
    parsed = urllib.parse.urlsplit(DATASET)
    if parsed.hostname in {"127.0.0.1", "localhost"}:
        host, port = parsed.hostname, parsed.port or 3030
        command = fuseki_command()
        if not owns_listener(ROOT, "fuseki", command, host, port):
            if owned_process(ROOT, "fuseki", command):
                raise RuntimeError("This copy's Fuseki is still starting. Wait briefly and run start.cmd again.")
            if not port_available(host, port):
                storage = ROOT / "data" / STORAGE_VERSION
                database = storage / "tdb2"
                # Existing histories bind to their dataset URL. Do not silently
                # relabel them or abandon an existing database to change ports.
                if ((storage / "source-sync/state.json").exists()
                        or (database.exists() and any(database.iterdir()))):
                    raise RuntimeError(f"Port {port} belongs to another project. This copy already has saved data; stop the other project's server, then restart this copy. Its database has not been changed.")
                chosen = free_port(host, port + 1)
                DATASET = urllib.parse.urlunsplit(parsed._replace(netloc=f"{host}:{chosen}"))
                CONFIG["dataset_url"] = DATASET
                print(f"Port {port} is occupied by another service. This copy will use its own Fuseki at {DATASET}.", flush=True)
    host, port = CONFIG["app_host"], int(CONFIG["app_port"])
    if host not in {"127.0.0.1", "localhost"}:
        raise RuntimeError("The offline launcher requires app_host to be 127.0.0.1 or localhost.")
    command = app_command()
    if not owns_listener(ROOT, "app", command, host, port):
        if owned_process(ROOT, "app", command):
            raise RuntimeError("This copy's website is still starting. Wait briefly and run start.cmd again.")
        if not port_available(host, port) or port == urllib.parse.urlsplit(DATASET).port:
            CONFIG["app_port"] = free_port(host, port + 1, (urllib.parse.urlsplit(DATASET).port,))
            print(f"Port {port} is occupied. This copy's website will use port {CONFIG['app_port']}.", flush=True)
    APP_URL = f"http://{host}:{CONFIG['app_port']}"
    if CONFIG != original:
        atomic_write(ROOT / "config.json", (json.dumps(CONFIG, indent=2) + "\n").encode("utf-8"))


def require_own_fuseki():
    parsed = urllib.parse.urlsplit(DATASET)
    if parsed.hostname in {"127.0.0.1", "localhost"} and not owns_listener(
            ROOT, "fuseki", fuseki_command(), parsed.hostname, parsed.port or 3030):
        raise RuntimeError("The configured local Fuseki is not owned by this project. Run this folder's start.cmd first; another copy's database will not be used.")


def start_fuseki():
    test_url = DATASET + "/query?query=" + urllib.parse.quote("ASK {}")
    parsed = urllib.parse.urlsplit(DATASET)
    if parsed.hostname not in {"127.0.0.1", "localhost"}:
        if ready(test_url):
            return  # An explicitly configured remote endpoint is not auto-managed.
        raise RuntimeError("Configured remote Fuseki is unavailable; this launcher only creates a local service.")
    command = fuseki_command()
    host, port = parsed.hostname, parsed.port or 3030
    if owns_listener(ROOT, "fuseki", command, host, port):
        if not ready(test_url):
            raise RuntimeError("This copy's Fuseki is running but its dataset is unavailable. Read logs/fuseki.log.")
        print(f"This project's Fuseki already available: {DATASET}", flush=True)
        return
    if not port_available(host, port):
        raise RuntimeError("The selected Fuseki port became occupied. Run start.cmd again to select a free port.")
    jar = Path(command[3])
    database = ROOT / "data" / STORAGE_VERSION / "tdb2"
    base = ROOT / "data" / STORAGE_VERSION / "fuseki-base"
    database.mkdir(parents=True, exist_ok=True)
    base.mkdir(exist_ok=True)
    env = dict(os.environ, FUSEKI_BASE=str(base), FUSEKI_HOME=str(jar.parent))
    print("Starting persistent local Fuseki...", flush=True)
    process = spawn("fuseki", command, env)
    wait_ready(test_url, process, "fuseki", command, host, port)
    print(f"Fuseki ready: {DATASET}", flush=True)


def initialise():
    from server import Fuseki
    require_own_fuseki()
    info = SourceSync(ROOT, Fuseki(DATASET)).sync()
    action = "Synchronized" if info['changed'] else "Already synchronized"
    print(f"{action}: {info['filename']} ({info['version']}). Source SHA-256: {info['sha256']}. Website records on unchanged source fields are preserved.", flush=True)


def repair_types():
    raise RuntimeError("The previous A1 type patch is retired. Use manage.py init with the current source RDF and this project's dataset.")


def verify_question_api():
    try:
        catalog = json.loads(request(APP_URL + "/api/cqs"))
        questions = catalog.get("questions")
        if (not isinstance(questions, list) or len(questions) != 9
                or not all(isinstance(item, dict) for item in questions)
                or {item.get("id") for item in questions} != {f"CQ{i}" for i in range(1, 10)}):
            raise ValueError("Incomplete question catalog")
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        raise RuntimeError(f"The backend at {APP_URL} does not provide CQ1-CQ9. Close that old application and run start.cmd again.") from exc


def verify_app_identity():
    source = model_info(ROOT)
    if not owns_listener(ROOT, "app", app_command(), CONFIG['app_host'], int(CONFIG['app_port'])):
        raise RuntimeError("The website at this address is not owned by this project. Run this folder's start.cmd.")
    info = json.loads(request(APP_URL + "/api/health"))
    if (info.get("namespace") != NAMESPACE or info.get("fusekiUrl") != DATASET
            or info.get("modelVersion") != source['version']
            or info.get("modelSourceSha256") != source['sha256']
            or Path(info.get("modelSourcePath", "")).resolve() != model_path(ROOT).resolve()
            or info.get("sourceSyncVersion") != 1 or info.get("partBVersion") != 1):
        raise RuntimeError("This project's website is running an older backend. Run this folder's stop.cmd, then start.cmd.")
    verify_question_api()


def start_app():
    global APP_URL
    command = app_command()
    host, port = CONFIG['app_host'], int(CONFIG['app_port'])
    if owns_listener(ROOT, "app", command, host, port):
        verify_app_identity()
        print(f"Part B with CQ1-CQ9 already available: {APP_URL}", flush=True)
        return
    if not port_available(host, port):
        # Fuseki startup/import can take several seconds. If another program
        # claimed the planned web port meanwhile, choose again before opening
        # the browser; app ports are not part of the database recovery state.
        port = free_port(host, port + 1, (urllib.parse.urlsplit(DATASET).port,))
        CONFIG['app_port'] = port
        APP_URL = f"http://{host}:{port}"
        atomic_write(ROOT / "config.json", (json.dumps(CONFIG, indent=2) + "\n").encode('utf-8'))
        command = app_command()
        print(f"Website port changed during startup. This copy will use {APP_URL}.", flush=True)
    process = spawn("app", command)
    wait_ready(APP_URL + "/api/health", process, "app", command, host, port)
    verify_app_identity()
    print(f"Part B ready: {APP_URL}", flush=True)


def export(path):
    require_own_fuseki()
    destination = Path(path) if path else ROOT / "submission" / "DigitalCare_export.ttl"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(request(DATASET + "/data?default", accept="text/turtle"))
    print(f"Exported current Fuseki graph to {destination.resolve()}", flush=True)


def run(args):
    if args.command in {"start", "fuseki"}:
        configure_local_ports()
        start_fuseki()
    if args.command in {"start", "init"}:
        initialise()
    if args.command == "start":
        start_app()
        if args.open:
            webbrowser.open(APP_URL)
    if args.command == "status":
        verify_app_identity()
        print(request(APP_URL + "/api/health").decode())
    if args.command == "repair-types":
        repair_types()
    if args.command == "export":
        export(args.output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["start", "fuseki", "init", "repair-types", "status", "export"])
    parser.add_argument("--open", action="store_true")
    parser.add_argument("--output")
    args = parser.parse_args()
    if args.command in {"start", "fuseki", "init"}:
        with source_lock(ROOT / "data" / "launcher"):
            run(args)
    else:
        run(args)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
