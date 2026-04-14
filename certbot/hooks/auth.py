#!/usr/bin/env python3

import os
import re
import sys
import json
import argparse
from urllib import request
from datetime import datetime, UTC
from lib import *

def main():
    parser = argparse.ArgumentParser(description="Certbot auth hook")

    parser.add_argument("--domain", help="FQDN (overrides CERTBOT_DOMAIN)")
    parser.add_argument("--token", help="Validation token (overrides CERTBOT_VALIDATION)")

    args = parser.parse_args()

    #print("[CERTBOT] AUTH HOOK", flush=True)

    # ---------------------------------------
    # INPUT (CLI overrides ENV)
    # ---------------------------------------
    fqdn = args.domain or os.environ.get("CERTBOT_DOMAIN")
    validation = args.token or os.environ.get("CERTBOT_VALIDATION")
    log_dir = get_log_dir(args)
    #print(f"[INFO] Using log dir: {log_dir}", flush=True)

    # ---------------------------------------
    # VALIDATION (clean failure, no traceback)
    # ---------------------------------------
    if not fqdn or not validation:
        error("Missing CERTBOT_DOMAIN or CERTBOT_VALIDATION")

        print("\n[USAGE]", flush=True)
        print("  Certbot mode:", flush=True)
        print("    CERTBOT_DOMAIN=... CERTBOT_VALIDATION=... auth.py", flush=True)

        print("\n  Manual mode:", flush=True)
        print("    auth.py --domain example.com --token abc123", flush=True)

        print("\n[CURRENT ENV]", flush=True)
        print(f"  CERTBOT_DOMAIN={os.environ.get('CERTBOT_DOMAIN')}", flush=True)
        print(f"  CERTBOT_VALIDATION={os.environ.get('CERTBOT_VALIDATION')}", flush=True)

        sys.exit(1)

    # ---------------------------------------
    # ZONE + RECORD NAME
    # ---------------------------------------
    zone = fqdn

    if fqdn.startswith("xcap."):
        zone = fqdn[len("xcap."):]

    if fqdn == zone:
        name = "_acme-challenge"
    else:
        sub = re.sub(rf"\.{re.escape(zone)}$", "", fqdn)
        name = f"_acme-challenge.{sub}"

    info(f"ZONE={zone}")
    info(f"NAME={name}")

    # ---------------------------------------
    # LOAD CACHE (auth_token + customer_id)
    # ---------------------------------------
    base_dir = os.path.dirname(os.path.abspath(__file__))

    os.makedirs(log_dir, exist_ok=True)

    cache = load_cache(log_dir, zone)

    if not cache:
        exit_fail(f"No cache found for zone: {zone}")
    
    auth_token = cache.get("auth_token")
    customer_id = cache.get("customer_id")

    if not auth_token or not customer_id:
        exit_fail("Missing auth_token or customer_id in cache")

    # ---------------------------------------
    # BUILD REQUEST
    # ---------------------------------------
    payload = {
        "zone": zone,
        "name": name,
        "value": validation,
        "type": "TXT",
        "action": "add_record",
        "auth_token": auth_token,
        "customer_id": customer_id
    }

    #print(payload)
    try:
        response = post_json(payload, ENROLLMENT_URL)
    except Exception as e:
        exit_fail(f"Request failed: {e}")

    # ---------------------------------------
    # SAVE LOG
    # ---------------------------------------
    try:
        filename = name + "." + zone
        save_log(log_dir, filename, response['result'])
        
    except Exception as e:
         exit_fail(f"Request response saving failed: {e}")
        
    sys.exit(0)


if __name__ == "__main__":
    main()
    
    