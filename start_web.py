from __future__ import annotations

import argparse
import os
import socket

import uvicorn
from src.config.env import load_project_env

def local_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def main() -> None:
    load_project_env()

    parser = argparse.ArgumentParser(
        description="Единый запуск Boiler Elec AI Web"
    )
    
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--mode", choices=("auto", "real", "demo"), default=os.getenv("BOILER_ELEC_PIPELINE_MODE", "auto"))
    parser.add_argument("--runs-root", default=os.getenv("BOILER_ELEC_RUNS_ROOT", "data/output/runs"))
    parser.add_argument("--projects-root", default=os.getenv("BOILER_ELEC_PROJECTS_ROOT", "data/projects"))
    parser.add_argument("--demo-runs-root", default=os.getenv("BOILER_ELEC_DEMO_RUNS_ROOT", "demo_data/runs"))
    parser.add_argument("--norms-dir", default=os.getenv("BOILER_ELEC_NORMS_DIR", "data/norms"))
    parser.add_argument("--public-base-url", default=os.getenv("BOILER_ELEC_PUBLIC_BASE_URL", ""))
    parser.add_argument("--max-concurrent-runs", type=int, default=int(os.getenv("BOILER_ELEC_MAX_CONCURRENT_RUNS", "1")))
    parser.add_argument("--share-admin-token", default=os.getenv("BOILER_ELEC_SHARE_ADMIN_TOKEN", ""))
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()

    os.environ["BOILER_ELEC_PIPELINE_MODE"] = args.mode
    os.environ["BOILER_ELEC_RUNS_ROOT"] = args.runs_root
    os.environ["BOILER_ELEC_PROJECTS_ROOT"] = args.projects_root
    os.environ["BOILER_ELEC_DEMO_RUNS_ROOT"] = args.demo_runs_root
    os.environ["BOILER_ELEC_NORMS_DIR"] = args.norms_dir
    os.environ["BOILER_ELEC_MAX_CONCURRENT_RUNS"] = str(max(1, args.max_concurrent_runs))
    if args.public_base_url:
        os.environ["BOILER_ELEC_PUBLIC_BASE_URL"] = args.public_base_url.rstrip("/")
    if args.share_admin_token:
        os.environ["BOILER_ELEC_SHARE_ADMIN_TOKEN"] = args.share_admin_token

    print("\nBoiler Elec AI Web")
    print(f"  Локально: http://127.0.0.1:{args.port}")
    print(f"  В локальной сети: http://{local_ip()}:{args.port}")
    if args.public_base_url:
        print(f"  Публичный адрес: {args.public_base_url.rstrip('/')}")
    else:
        print("  Интернет-доступ: не настроен; используйте VPN/туннель/reverse proxy и --public-base-url")
    print(f"  Одновременных тяжёлых расчётов: {max(1, args.max_concurrent_runs)}\n")

    uvicorn.run("src.web.app:app", host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()
