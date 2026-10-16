"""
run_mesh.py
-----------
Starts all TripMesh agent microservices, the API gateway, and the Astro frontend for local testing and verification.
"""

import sys
import os
import subprocess
import time
import signal

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))

SERVICES = [
    {"name": "Budget Agent", "dir": "budget_agent", "cmd": [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8005", "--log-level", "warning"]},
    {"name": "Transport Agent", "dir": "transport_agent", "cmd": [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8002", "--log-level", "warning"]},
    {"name": "Hotel Agent", "dir": "hotel_agent_crewai", "cmd": [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8003", "--log-level", "warning"]},
    {"name": "Local Guide Agent", "dir": "local_guide_agent_rag", "cmd": [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8004", "--log-level", "warning"]},
    {"name": "Planner Agent", "dir": "planner_agent", "cmd": [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8001", "--log-level", "warning"]},
    {"name": "Gateway", "dir": "gateway", "cmd": [sys.executable, "-m", "uvicorn", "app:app", "--host", "127.0.0.1", "--port", "8000", "--log-level", "info"]},
]

def main():
    processes = []
    print("==========================================================")
    print("           TRIPMESH MULTI-AGENT MESH LAUNCHER            ")
    print("==========================================================")

    env = os.environ.copy()
    env["PYTHONPATH"] = ROOT_DIR

    try:
        for svc in SERVICES:
            svc_dir = os.path.join(ROOT_DIR, svc["dir"])
            port = svc["cmd"][7]
            print(f"[*] Starting {svc['name']} on port {port}...")
            proc = subprocess.Popen(svc["cmd"], cwd=svc_dir, env=env)
            processes.append((svc["name"], proc))
            time.sleep(0.5)

        # Start Frontend (Astro)
        frontend_dir = os.path.join(ROOT_DIR, "frontend")
        dist_dir = os.path.join(frontend_dir, "dist")
        if os.path.exists(dist_dir):
            print("[*] Starting Frontend (serving dist) on port 4321...")
            frontend_proc = subprocess.Popen([sys.executable, "-m", "http.server", "4321", "--directory", dist_dir], cwd=frontend_dir)
            processes.append(("Frontend", frontend_proc))
        elif os.path.exists(frontend_dir):
            print("[*] Starting Frontend (Astro dev server)...")
            npm_cmd = "npm.cmd" if sys.platform == "win32" else "npm"
            frontend_proc = subprocess.Popen([npm_cmd, "run", "dev", "--", "--host", "127.0.0.1", "--port", "4321"], cwd=frontend_dir)
            processes.append(("Frontend", frontend_proc))

        print("\n==========================================================")
        print("  ALL SERVICES ARE RUNNING!")
        print("  - Frontend UI:         http://localhost:4321")
        print("  - Gateway API:         http://localhost:8000")
        print("  - Planner Agent:       http://localhost:8001")
        print("  - Transport Agent:     http://localhost:8002")
        print("  - Hotel Agent:         http://localhost:8003")
        print("  - Local Guide Agent:   http://localhost:8004")
        print("  - Budget Agent:        http://localhost:8005")
        print("==========================================================")
        print("Press Ctrl+C to stop all services.\n")

        while True:
            time.sleep(1)

    except KeyboardInterrupt:
        print("\n[*] Stopping all TripMesh services...")
    finally:
        for name, proc in processes:
            try:
                proc.terminate()
                proc.kill()
            except Exception:
                pass
        print("[*] All services terminated.")

if __name__ == "__main__":
    main()
