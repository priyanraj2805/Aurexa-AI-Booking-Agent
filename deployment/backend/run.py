import argparse
import os
import sys
import threading
import webbrowser

import uvicorn
from dotenv import load_dotenv

from scripts import preflight

load_dotenv()

# Add backend directory to sys.path explicitly just in case
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


def open_browser():
    webbrowser.open("http://localhost:8005/docs")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run backend dev server")
    parser.add_argument(
        "--skip-preflight",
        action="store_true",
        help="Skip startup prerequisite checks",
    )
    parser.add_argument(
        "--preflight-only",
        action="store_true",
        help="Run preflight checks and exit",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Do not auto-open API docs in browser",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()

    if not args.skip_preflight or args.preflight_only:
        preflight_result = preflight.run_preflight()
        print(preflight.render_report(preflight_result))
        if preflight_result["exit_code"] != 0 or args.preflight_only:
            return int(preflight_result["exit_code"])

    if not args.no_browser:
        threading.Timer(1, open_browser).start()

    uvicorn.run("app.main:app", host="0.0.0.0", port=8005, reload=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
