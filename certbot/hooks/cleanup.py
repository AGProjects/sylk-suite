#!/usr/bin/env python3

import os
import json
import argparse
from lib import *

def main():
    parser = argparse.ArgumentParser(description="Certbot cleanup hook")
    parser.add_argument("--domain", help="FQDN (overrides CERTBOT_DOMAIN)")
    parser.add_argument("--token", help="optional to be simetric with auth.py")
    args = parser.parse_args()

    fqdn = args.domain or os.environ.get("CERTBOT_DOMAIN")

    if not fqdn:
        exit_fail("Missing --domain or CERTBOT_DOMAIN")

    zone = fqdn
    if fqdn.startswith("xcap."):
        zone = fqdn[len("xcap."):]

    record_name = "_acme-challenge"

    base_dir = os.path.dirname(os.path.abspath(__file__))
    log_dir = get_log_dir(args)
    #print(f"[INFO] Using log dir: {log_dir}", flush=True)
    
    # ---------------------------------------
    # 1. LOAD AUTH CACHE
    # ---------------------------------------
    auth_cache = load_cache(log_dir, zone)

    if not auth_cache:
        exit_fail(f"Missing auth cache for {zone}")

    auth_token = auth_cache.get("auth_token")
    customer_id = auth_cache.get("customer_id")

    if not auth_token or not customer_id:
        exit_fail("Missing auth_token or customer_id")

    # ---------------------------------------
    # 2. LOAD ACME FILE FOR THIS DOMAIN
    # ---------------------------------------
    acme_file = os.path.join(log_dir, f"_acme-challenge.{fqdn}.json")

    if not os.path.exists(acme_file):
        exit_fail(f"Missing ACME file: {acme_file}")

    with open(acme_file, "r", encoding="utf-8") as f:
        acme_data = json.load(f)
    
    try:
        record_id = acme_data['data']['result']['id']
    except KeyError:
        exit_fail("Missing record_id in ACME file %s" % acme_file)

    info(f"ZONE={zone}")
    info(f"RECORD_ID={record_id}")

    # ---------------------------------------
    # 3. SEND CLEANUP REQUEST
    # ---------------------------------------
    payload = {
        "zone": zone,
        "name": record_name,
        "record_id": record_id,
        "action": "delete_record",
        "auth_token": auth_token,
        "customer_id": customer_id
    }

    #print(payload)    
    try:
        response = post_json(payload, ENROLLMENT_URL)
    except Exception as e:
        exit_fail(f"Request for cleanup failed: {e}")


if __name__ == "__main__":
    main()
    
    