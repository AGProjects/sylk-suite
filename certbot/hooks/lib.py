import os
import sys
import json
from urllib import request
from datetime import datetime, UTC
from pathlib import Path

def get_log_dir(args):
    # Docker mode (Certbot)
    if os.environ.get("CERTBOT_DOMAIN"):
        return "/logs"

    # Manual mode (CLI)
    if args.domain:
        base_dir = Path(__file__).resolve().parent
        return str((base_dir / "../logs").resolve())

    # fallback safety
    return str((Path(__file__).resolve().parent / "../logs").resolve())
    
ENROLLMENT_URL  = "https://enrollment.sipthor.net/enrollment-sylk-domain.phtml"

DEBUG = os.environ.get("HOOK_DEBUG") == "1"

def info(msg):
    if DEBUG:
        print(f"[INFO] {msg}", flush=True)

def error(msg):
    print(f"[ERROR] {msg}", file=sys.stderr, flush=True)

def exit_fail(msg):
    error(msg)
    sys.exit(1)

def load_cache(log_dir, domain):
    path = os.path.join(log_dir, f"{domain}.json")
    if not os.path.exists(path):
        return None

    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def save_log(log_dir, name, data):
    os.makedirs(log_dir, exist_ok=True)

    path = os.path.join(log_dir, f"{name}.json")

    with open(path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "timestamp": datetime.now(UTC).isoformat(),
                "data": data
            },
            f,
            indent=2
        )

    info(f"Saved log: {path}")


def post_json(payload, url):
    data = json.dumps(payload).encode("utf-8")

    req = request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST"
    )

    with request.urlopen(req) as response:
        return json.loads(response.read().decode())
        