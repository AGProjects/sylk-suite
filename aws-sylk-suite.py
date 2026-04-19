#!/usr/bin/env python3
"""
aws-sylk-suite.py - Manage the Sylk Suite EC2 instance.

Commands:
    start              Start the instance (if stopped) and print its public IP.
    stop               Stop the instance.
    status             Show current state and public IP.
    copy <file>        Copy a local file to the instance (into ~/).
    ssh                Open an interactive SSH session.

Requires:
    - boto3            (pip3 install boto3)
    - AWS credentials  configured via `aws configure`
    - SSH key          ./sylk-suite-aws.pem next to this script
    - Instance ID      in ./sylk-suite-aws-instance.txt next to this script
"""

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

try:
    import boto3
    from botocore.exceptions import ClientError
except ImportError:
    sys.exit("boto3 is not installed. Run: pip3 install boto3")

# ---------- CONFIG (edit these) ----------
SSH_USER    = "admin"               # "ubuntu" for Ubuntu, "ec2-user" for Amazon Linux
REGION      = "eu-central-1"           # None => use region from `aws configure`
# -----------------------------------------

SCRIPT_DIR    = Path(__file__).resolve().parent
KEY_PATH      = SCRIPT_DIR / "sylk-suite-aws.pem"
INSTANCE_FILE = SCRIPT_DIR / "sylk-suite-aws-instance.txt"


def load_instance_id():
    """Read the instance ID from the instance file, or exit."""
    if not INSTANCE_FILE.exists():
        sys.exit(
            f"Instance file not found: {INSTANCE_FILE}\n"
            f"Create it and put your EC2 instance ID (e.g. i-0abc123...) on the first line."
        )
    content = INSTANCE_FILE.read_text().strip()
    iid = content.splitlines()[0].strip() if content else ""
    if not iid.startswith("i-"):
        sys.exit(f"{INSTANCE_FILE} does not contain a valid EC2 instance ID (expected 'i-...').")
    return iid


INSTANCE_ID = None   # populated in main()


def require_key():
    """Exit if the SSH key is missing; fix permissions if too open."""
    if not KEY_PATH.exists():
        sys.exit(f"SSH key not found: {KEY_PATH}\nAborting.")
    mode = KEY_PATH.stat().st_mode & 0o777
    if mode & 0o077:
        print(f"Fixing permissions on {KEY_PATH.name} (chmod 400)")
        KEY_PATH.chmod(0o400)


def ec2_client():
    return boto3.client("ec2", region_name=REGION) if REGION else boto3.client("ec2")


def describe():
    resp = ec2_client().describe_instances(InstanceIds=[INSTANCE_ID])
    return resp["Reservations"][0]["Instances"][0]


def get_state_and_ip():
    i = describe()
    return i["State"]["Name"], i.get("PublicIpAddress"), i.get("PublicDnsName")


def cmd_status(_):
    state, ip, dns = get_state_and_ip()
    print(f"Instance:   {INSTANCE_ID}")
    print(f"State:      {state}")
    print(f"Public IP:  {ip or '(none)'}")
    print(f"Public DNS: {dns or '(none)'}")


def cmd_start(_):
    ec2 = ec2_client()
    state, ip, _ = get_state_and_ip()
    if state == "running":
        print(f"Instance already running at {ip}")
        return
    if state == "stopping":
        print("Instance is stopping, waiting for it to fully stop first...")
        ec2.get_waiter("instance_stopped").wait(InstanceIds=[INSTANCE_ID])
        state = "stopped"
    if state != "stopped":
        sys.exit(f"Cannot start from state '{state}'.")

    print("Starting instance...")
    ec2.start_instances(InstanceIds=[INSTANCE_ID])
    ec2.get_waiter("instance_running").wait(InstanceIds=[INSTANCE_ID])
    _, ip, _ = get_state_and_ip()
    print(f"Instance running. Public IP: {ip}")
    print("Giving SSH ~10s to come up...")
    time.sleep(10)
    print("Ready. Connect with:  ./aws-sylk-suite.py ssh")


def cmd_stop(_):
    ec2 = ec2_client()
    state, _, _ = get_state_and_ip()
    if state == "stopped":
        print("Instance already stopped.")
        return
    print("Stopping instance...")
    ec2.stop_instances(InstanceIds=[INSTANCE_ID])
    ec2.get_waiter("instance_stopped").wait(InstanceIds=[INSTANCE_ID])
    print("Instance stopped.")


def require_running_ip():
    state, ip, _ = get_state_and_ip()
    if state != "running":
        sys.exit(f"Instance state is '{state}'. Start it first:  ./aws-sylk-suite.py start")
    if not ip:
        sys.exit("Instance has no public IP address.")
    return ip


def cmd_copy(args):
    require_key()
    ip = require_running_ip()
    local = Path(args.file).expanduser()
    if not local.exists():
        sys.exit(f"File not found: {local}")
    dest = f"{SSH_USER}@{ip}:~/"
    print(f"Copying {local} -> {dest}")
    subprocess.run(
        [
            "scp",
            "-i", str(KEY_PATH),
            "-o", "StrictHostKeyChecking=accept-new",
            str(local), dest,
        ],
        check=True,
    )
    print("Done.")


def cmd_ssh(_):
    require_key()
    ip = require_running_ip()
    print(f"Connecting to {SSH_USER}@{ip} ...")
    os.execvp(
        "ssh",
        [
            "ssh",
            "-i", str(KEY_PATH),
            "-o", "StrictHostKeyChecking=accept-new",
            f"{SSH_USER}@{ip}",
        ],
    )


def main():
    p = argparse.ArgumentParser(description="Manage the Sylk Suite EC2 instance.")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("start",  help="Start the instance").set_defaults(func=cmd_start)
    sub.add_parser("stop",   help="Stop the instance").set_defaults(func=cmd_stop)
    sub.add_parser("status", help="Show instance state and IP").set_defaults(func=cmd_status)

    pc = sub.add_parser("copy", help="Copy a local file to the instance (~/)")
    pc.add_argument("file", help="Path to local file to copy")
    pc.set_defaults(func=cmd_copy)

    sub.add_parser("ssh", help="Open an interactive SSH session").set_defaults(func=cmd_ssh)

    args = p.parse_args()

    global INSTANCE_ID
    INSTANCE_ID = load_instance_id()

    # Key is required for copy/ssh; harmless to check up front.
    require_key()

    try:
        args.func(args)
    except ClientError as e:
        sys.exit(f"AWS error: {e}")


if __name__ == "__main__":
    main()
