import argparse
import asyncio
import sys
from typing import Tuple

import uvicorn
from fastapi.routing import APIRoute

try:
    import tomli
except ImportError:
    try:
        import tomllib as tomli
    except ImportError:
        tomli = None

from app.config import CONFIG
from app.main import app
from app.services.gemini_client import _resolve_cookies


class Colors:
    YELLOW = "\033[93m"
    CYAN = "\033[96m"
    RESET = "\033[0m"
    BOLD = "\033[1m"


def get_app_info() -> Tuple[str, str]:
    if not tomli:
        return "Gemini Bridge", "N/A (tomli not installed)"
    try:
        with open("pyproject.toml", "rb") as f:
            toml_data = tomli.load(f)
        project_data = toml_data.get("project", {})
        name = project_data.get("name", "gemini-bridge").replace("-", " ").title()
        version = project_data.get("version", "N/A")
        return name, version
    except (FileNotFoundError, KeyError) as e:
        print(f"[warn] Failed to read pyproject.toml: {e}", file=sys.stderr)
        return "Gemini Bridge", "N/A"


def print_server_info(host: str, port: int):
    base_url = f"http://{host}:{port}"
    app_name, app_version = get_app_info()
    print("\n" + "=" * 80)
    print(f"{Colors.BOLD}{Colors.YELLOW}{f'{app_name} v{app_version}'.center(80)}{Colors.RESET}")
    print(f"{app_name} — OpenAI-compatible API on top of gemini.google.com".center(80))
    print("=" * 80)
    print("\nServices:")
    print(f"  - Docs:   {base_url}/docs")
    print(f"  - Status: {base_url}/admin/status")
    print(f"\nConfig: browser={CONFIG['Browser']['name']} model={CONFIG['Gemini']['default_model']}")
    print("\nEndpoints:")
    paths = sorted({route.path for route in app.routes if isinstance(route, APIRoute)})
    for path in paths:
        if path not in ("/docs", "/redoc", "/openapi.json"):
            print(f"  - {base_url}{path}")
    print("\n" + "=" * 80)


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    parser = argparse.ArgumentParser(description="Run the Gemini Bridge FastAPI server.")
    parser.add_argument("--host", type=str, default="localhost", help="Host IP address")
    parser.add_argument("--port", type=int, default=6969, help="Port number")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reloading")
    args = parser.parse_args()

    # Don't init the Gemini client here — uvicorn's lifespan does it inside the
    # worker, on the right event loop. We only peek at cookie availability so
    # the boot banner can hint whether the user needs the extension running.
    psid, psidts = _resolve_cookies()
    if psid and psidts:
        print(f"INFO:     ✅ {Colors.CYAN}Gemini cookies found — initializing on startup{Colors.RESET}")
    else:
        print(f"INFO:     ⏳ {Colors.CYAN}Waiting for extension to push cookies{Colors.RESET}")

    print_server_info(args.host, args.port)

    config = uvicorn.Config(app, host=args.host, port=args.port, reload=args.reload, log_config=None)
    try:
        uvicorn.Server(config).run()
    except KeyboardInterrupt:
        # Py 3.11+ re-raises KeyboardInterrupt from asyncio.run; swallow to keep
        # Ctrl+C clean after shutdown logs.
        print("\n[Bridge] Stopped.")
