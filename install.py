#!/usr/bin/env python3

import argparse
import configparser
import ipaddress
import json
import os
import random
import re
import socket
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from time import sleep
from urllib import error as urlerror
from urllib import request

# Practical email syntactic check. Not a full RFC 5322 parser — it enforces
# the things users actually get wrong:
#   - exactly one @ separator
#   - no whitespace anywhere
#   - non-empty local part, only RFC-5322-common chars, no leading/trailing
#     dot and no consecutive dots
#   - domain made of at least two DNS labels, each 1-63 chars, alphanumerics
#     and hyphens only (no leading/trailing hyphen)
#   - TLD of at least 2 letters (alphabetic)
_EMAIL_LOCAL_RE  = re.compile(r"^[A-Za-z0-9._%+\-]+$")
_EMAIL_LABEL_RE  = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9\-]{0,61}[A-Za-z0-9])?$")
_EMAIL_TLD_RE    = re.compile(r"^[A-Za-z]{2,}$")

REPO_URL = "https://github.com/AGProjects/sylk-suite.git"
DEST_DIR = Path("/opt/sylk-suite")
DOMAIN = "sylk.link"
ENROLLMENT_URL = "https://enrollment.sipthor.net/enrollment-sylk-domain.phtml"
PUSH_URL = "http://ca-sip-01.sipthor.net:8400/push"

RESULT_PREFIX = "dns-data"

ALLOWED_COMPONENTS = {"opensips", "sylkserver", "mediaproxy", "openxcap", "msrprelay", "dns", "enrollment", "docker", "mysql"}

# Debian packages this script `apt-get install`s fall into two groups:
#
# PROJECT_DEB_PACKAGES — project-specific packages that are safe to
#   purge on --purge-files. These are either our own (opensips*,
#   mediaproxy*, msrprelay, openxcap) or the Docker stack / small
#   utilities we pull in explicitly.
#
# SYSTEM_DEB_DEPENDENCIES — foundational system packages the installer
#   needs but that are commonly pre-installed on Debian/Ubuntu and are
#   depended on by many other things on the host. Removing these would
#   break other software (and in the case of ca-certificates would even
#   break Python HTTPS on this very installer). --purge-files intentionally
#   leaves these alone; --show-installed lists them so the user can see
#   exactly what the installer touched. Use --reinstall-deps to repair
#   these if they have been removed by an older version of this script.
PROJECT_DEB_PACKAGES = [
    # Baseline utilities (main)
    "python3-psutil",
    "qrencode",
    # Docker stack (install_docker)
    "docker.io",
    "docker-compose",
    # OpenSIPS stack (install_opensips). The meta-package pulls in the rest;
    # we still list them explicitly so --show-installed reports them and
    # --purge-files removes them by name.
    "opensips-config-sylkserver",
    "opensips",
    "mediaproxy-dispatcher",
    "mediaproxy-relay",
    "msrprelay",
    # Note: certbot is NOT a host Debian package in this setup — it runs
    # inside the `certbot/certbot` Docker container built from
    # certbot/Dockerfile, so it is tracked in DOCKER_IMAGES below rather
    # than here. The project's scripts/uninstall.sh lists `certbot` as a
    # defensive apt-remove in case someone also installed the host
    # package manually, but the installer itself never does.
    # OpenXCAP (install_openxcap)
    "openxcap",
]

SYSTEM_DEB_DEPENDENCIES = [
    "ca-certificates",  # system CA bundle — also used by Python's urllib
    "apt-utils",
    "curl",
    "gnupg",
    "git",
    "nftables",         # `nft` CLI — needed by ensure_conntrack_hooks_enabled()
                        # to flush the nftables ruleset before reloading
                        # nf_conntrack (mediaproxy install step).
]

# Back-compat alias: kept so anything that still references the old name
# (tests, external tooling) keeps working. New code should reference the
# split lists directly.
INSTALLED_DEB_PACKAGES = PROJECT_DEB_PACKAGES + SYSTEM_DEB_DEPENDENCIES

# Third-party APT sources and GPG keyrings added by this script.
INSTALLED_APT_SOURCES = [
    "/etc/apt/sources.list.d/ag-projects.list",
    "/etc/apt/sources.list.d/opensips.list",
    "/etc/apt/sources.list.d/opensips-cli.list",
    "/usr/share/keyrings/agp-debian-key.gpg",
    "/usr/share/keyrings/opensips-org.gpg",
]

# Docker resources declared in the bundled docker-compose.yml.
DOCKER_CONTAINERS = ["sylkserver", "janus", "sylk-webrtc"]
DOCKER_IMAGES = ["sylkserver:bookworm", "sylk-webrtc-nginx", "certbot/certbot"]
DOCKER_VOLUMES = ["sylkserver_tls"]
DOCKER_NETWORKS = ["sylk-net"]

starts = ["bl", "sn", "fl", "zo", "qu", "pl", "gr", "dr", "tr", "wh", "kr", "gl", "sp", "tw"]
middles = ["a", "e", "i", "o", "u", "ai", "oo", "ee"]
ends = ["b", "p", "d", "t", "g", "nk", "sh", "mp", "zz"]

WORDS = [
    "apple", "moon", "star", "leaf", "stone", "fire", "sky", "wave",
    "sun", "rock", "tree", "river", "bird", "cat", "dog", "hill", "lake",
    "cloud", "spark", "branch", "flower", "seed", "grass", "wind", "rain",
    "snow", "sand", "shell", "beach", "pebble", "root", "fruit", "fish",
    "frog", "owl", "fox", "lion", "bear", "ant", "bee", "wolf", "hawk",
    "nest", "mud", "ice", "leaflet", "twig", "pine", "oak", "maple",
    "fern", "cliff", "dune", "cave", "glade", "brook", "pond", "reef"
]

CYAN = "\033[36m"
YELLOW = "\033[33m"
GREEN = "\033[32m"
RESET = "\033[0m"
RED = "\033[31m"
BLUE = "\033[94m"

dns_template = """conference.DOMAINNAME     600     IN      NAPTR   100 100 "s" "SIP+D2T" "" _sip._tcp.DOMAINNAME.
DOMAINNAME                3600    IN      A       IPADDR
xcap.DOMAINNAME           600     IN      A       IPADDR
DOMAINNAME                600     IN      NAPTR   10 100 "s" "SIPS+D2T" "" _sips._tcp.DOMAINNAME.
DOMAINNAME                600     IN      NAPTR   30 100 "s" "SIP+D2U" "" _sip._udp.DOMAINNAME.
DOMAINNAME                600     IN      NAPTR   20 100 "s" "SIP+D2T" "" _sip._tcp.DOMAINNAME.
DOMAINNAME                3600    IN      NS      ns3.dns-hosting.info.
DOMAINNAME                3600    IN      NS      ns2.dns-hosting.info.
DOMAINNAME                3600    IN      NS      ns1.dns-hosting.info.
DOMAINNAME                3600    IN      SOA     ns1.dns-hosting.info. support@ag-projects.com. 13 300 600 604800 3600
localhost.DOMAINNAME      3600    IN      A       127.0.0.1
xcap.DOMAINNAME           600     IN      TXT     https://xcap.WEBURL/xcap-root
_msrps._tcp.DOMAINNAME    600     IN      SRV     10 0 MSRPPORT DOMAINNAME.
_sip._tcp.DOMAINNAME      600     IN      SRV     100 100 SIPTCPPORT DOMAINNAME.
_sip._udp.DOMAINNAME      600     IN      SRV     100 100 SIPTCPPORT DOMAINNAME.
_sips._tcp.DOMAINNAME     600     IN      SRV     100 100 SIPTLSPORT DOMAINNAME.
_stun._udp.DOMAINNAME     600     IN      SRV     0 10 3478 stun2.sipthor.net.
_stun._udp.DOMAINNAME     600     IN      SRV     0 10 3478 stun1.sipthor.net.
_sylkserver.DOMAINNAME    600     IN      TXT     https://WEBURL/sylk-config.json"""

step = 0
# Set by --verbose on the command line. When True, run() ignores the
# per-call `silent=True` flag and streams every command's output live.
# This is a global so we don't have to thread the flag through every
# call site (there are ~70 of them).
VERBOSE = False
env = os.environ.copy()
env['DEBIAN_FRONTEND'] = "noninteractive"


class Info():
    # Persistent settings live next to the other installer artefacts in the
    # repository's logs directory. The old `.env` files (either the one the
    # installer used to drop inside DEST_DIR, or the one produced when the
    # script was run from a different working directory) are kept as a
    # one-shot migration source in load() below. Order matters: the copy
    # inside DEST_DIR is considered authoritative, since it's the one that
    # corresponds to the currently-deployed installation.
    DEFAULT_FILE = str(DEST_DIR / "logs" / "setup.json")

    # Docker-compose still needs a KEY=VALUE file for ${VAR} substitution
    # inside docker-compose.yml. We generate it from setup.json and keep
    # it next to setup.json (well away from the git checkout) so the user
    # cannot accidentally commit it or have it shadowed by a stale .env
    # in DEST_DIR. Every docker-compose invocation in this script is
    # expected to pass `--env-file <ENV_FILE>`.
    ENV_FILE = str(DEST_DIR / "logs" / "docker.env")

    LEGACY_ENV_FILES = (
        str(DEST_DIR / ".env"),   # authoritative: matches the running deploy
        ".env",                    # fallback: installer's current directory
    )
    # Kept for backward compatibility with external callers / --purge-files
    LEGACY_ENV_FILE = ".env"

    def __init__(self, email, zone, ip_addr, local_ip, nat, web_port, sip_port, rtp_port, msrp_port):
        self.email = email
        self.zone = zone
        self.ip = ip_addr
        self.local_ip = local_ip
        self.nat = nat
        self.web_port = web_port or "443"
        self.sip_port = sip_port or "15060"
        self.rtp_port = rtp_port or "60000"
        self.msrp_port = msrp_port or "2855"

        env['FULL_DOMAIN'] = f"{self.zone}.{DOMAIN}"
        env['IP'] = self.ip
        env['EMAIL'] = self.email
        env['LOCAL_IP'] = self.local_ip
        env['NAT'] = str(self.nat)

        env['WEB_PORT'] = self.web_port
        env['SIP_PORT'] = self.sip_port
        env['RTP_PORT'] = self.rtp_port
        env['MSRP_PORT'] = self.msrp_port

    @property
    def full_domain(self):
        return f"{self.zone}.{DOMAIN}"

    @property
    def json(self):
        return json.dumps(self.__dict__).encode('utf-8')

    def save(self, filename=None):
        filename = filename or self.DEFAULT_FILE
        parent = os.path.dirname(filename)
        if parent:
            os.makedirs(parent, exist_ok=True)

        payload = {
            "email":       self.email,
            "full_domain": f"{self.zone}.{DOMAIN}",
            "zone":        self.zone,
            "ip":          self.ip,
            "local_ip":    self.local_ip,
            "nat":         self.nat,
            "web_port":    self.web_port,
            "sip_port":    self.sip_port,
            "rtp_port":    self.rtp_port,
            "msrp_port":   self.msrp_port,
        }
        # Write atomically so an interrupted write cannot leave a half-file.
        tmp = filename + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
            f.write("\n")
        os.replace(tmp, filename)

        # Keep the docker-compose .env in sync with setup.json. When save()
        # is called with a custom filename (e.g. in tests), ENV_FILE still
        # points at its default location — callers that really want a
        # different location can call save_env() directly.
        try:
            self.save_env()
        except OSError:
            # Best-effort: if the logs directory isn't writable yet (e.g.
            # save() called before clone_repo has created DEST_DIR) we
            # silently skip. The next successful save() will redo it.
            pass

    # Environment variables referenced by docker-compose.yml. Keep this in
    # sync with the ${...} substitutions in that file.
    _ENV_KEYS = (
        "FULL_DOMAIN", "IP", "EMAIL", "LOCAL_IP", "NAT",
        "WEB_PORT", "SIP_PORT", "RTP_PORT", "MSRP_PORT",
    )

    def _as_env_dict(self):
        """Return the dict that docker-compose expects for ${VAR} expansion."""
        return {
            "FULL_DOMAIN": f"{self.zone}.{DOMAIN}",
            "IP":          self.ip or "",
            "EMAIL":       self.email or "",
            "LOCAL_IP":    self.local_ip or "",
            "NAT":         str(self.nat),
            "WEB_PORT":    str(self.web_port),
            "SIP_PORT":    str(self.sip_port),
            "RTP_PORT":    str(self.rtp_port),
            "MSRP_PORT":   str(self.msrp_port),
        }

    def save_env(self, filename=None):
        """Write a KEY=VALUE .env file docker-compose can read via --env-file.

        The file lives under logs/ (outside the git checkout itself) so it
        is never confused with, or shadowed by, a stale DEST_DIR/.env from
        an older installer run.
        """
        filename = filename or self.ENV_FILE
        parent = os.path.dirname(filename)
        if parent:
            os.makedirs(parent, exist_ok=True)

        lines = [
            "# Generated by install.py from setup.json — do not edit by hand.",
            "# docker-compose reads this via --env-file to substitute ${VAR}",
            f"# references in {DEST_DIR}/docker-compose.yml.",
        ]
        env_dict = self._as_env_dict()
        # Preserve a deterministic key order so diffs are stable.
        for key in self._ENV_KEYS:
            lines.append(f"{key}={env_dict[key]}")

        tmp = filename + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        os.replace(tmp, filename)

    @classmethod
    def _from_dict(cls, data):
        full_domain = data.get("full_domain") or ""
        ip = data.get("ip")

        # Placeholder/sentinel values mean "not yet configured".
        if full_domain == "sylk.link" and ip == "0.0.0.0":
            return None
        if not full_domain:
            return None

        zone = data.get("zone") or full_domain.split(".")[0]
        return cls(
            email=data.get("email"),
            zone=zone,
            # Previously this was hard-coded to None on the assumption that
            # setup_data() would always re-prompt and rebuild the Info with
            # a real public IP. That's no longer true — with a valid
            # setup.json the confirmation shortcut returns this Info
            # directly, so we must populate ip_addr from the saved value or
            # env['IP'] ends up as None and subprocess.Popen crashes.
            ip_addr=ip,
            local_ip=data.get("local_ip"),
            nat=data.get("nat"),
            web_port=data.get("web_port"),
            sip_port=data.get("sip_port"),
            rtp_port=data.get("rtp_port"),
            msrp_port=data.get("msrp_port"),
        )

    @classmethod
    def _load_legacy_env(cls, filename):
        """Parse a legacy KEY=VALUE .env file into a dict compatible with _from_dict."""
        env_data = {}
        with open(filename, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    env_data[key] = value
        return {
            "email":       env_data.get("EMAIL"),
            "full_domain": env_data.get("FULL_DOMAIN"),
            "ip":          env_data.get("IP"),
            "local_ip":    env_data.get("LOCAL_IP"),
            "nat":         env_data.get("NAT"),
            "web_port":    env_data.get("WEB_PORT"),
            "sip_port":    env_data.get("SIP_PORT"),
            "rtp_port":    env_data.get("RTP_PORT"),
            "msrp_port":   env_data.get("MSRP_PORT"),
        }

    @classmethod
    def load(cls, filename=None):
        filename = filename or cls.DEFAULT_FILE

        # Preferred: JSON settings file.
        if os.path.exists(filename):
            try:
                with open(filename, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except (OSError, json.JSONDecodeError):
                return None
            return cls._from_dict(data)

        # One-shot migration from a legacy .env file. We look first in
        # DEST_DIR (the authoritative copy the previous installer runs
        # dropped alongside docker-compose.yml) and then fall back to a .env
        # in the current working directory — which is what the user ends up
        # with when they download install.py into some other folder and run
        # it from there. After we successfully read it, persist to the new
        # JSON location and delete the legacy file so subsequent runs use
        # the new path.
        for legacy in cls.LEGACY_ENV_FILES:
            if not os.path.exists(legacy):
                continue
            try:
                data = cls._load_legacy_env(legacy)
            except OSError:
                continue
            info = cls._from_dict(data)
            if info is None:
                # Corrupt / placeholder legacy file; try the next candidate.
                continue
            try:
                info.save(filename)
                os.remove(legacy)
                output(f"Migrated legacy settings from {legacy} to {filename}")
            except OSError:
                # Migration is best-effort; if the target dir does not
                # exist yet (first run before clone_repo) we'll retry on
                # the next save call.
                pass
            return info

        return None

    def __str__(self):
        fields = [
            ("Email address", self.email),
            ("Sylk domain", self.full_domain),
            ("Public server IP address", self.ip),
            ("Behind NAT", self.nat),
            ("Private server IP address", self.local_ip),
            ("Public Web Port", self.web_port),
            ("Public MSRP Port", self.msrp_port),
            ("Public SIP Port", self.sip_port),
            ("Public RTP Port", self.rtp_port),
        ]

        return "\n".join(f"    {label:<25}: {value}" for label, value in fields)


# Base docker-compose command with an explicit --env-file. Every
# docker-compose invocation in this script MUST go through this constant
# (or `compose(args)`) so the values the containers see match exactly
# what the installer saved to setup.json.
COMPOSE_CMD = f"docker-compose --env-file {Info.ENV_FILE}"


def compose(args):
    """Return a 'docker-compose --env-file <ENV_FILE> <args>' command string.

    Falls back to a bare 'docker-compose' if the env file does not exist
    yet. That keeps --purge-docker-files usable on a half-installed
    machine where setup.json was never written, and also lets a user run
    the teardown after having manually deleted /opt/sylk-suite/logs/
    without the cleanup itself blowing up on a missing file.
    """
    if os.path.exists(Info.ENV_FILE):
        return f"docker-compose --env-file {Info.ENV_FILE} {args}"
    return f"docker-compose {args}"


def question(step_number, title, question, default=None, step_color=CYAN, prompt_color=YELLOW):
    global step
    if step != step_number:
        print("\n" + "-" * 80)
        print(f"{step_color}[STEP {step_number}] {title}{RESET}")

    step = step_number

    if default is not None:
        if default.upper() in ("Y", "N"):
            prompt = f"{prompt_color}{question} ({default}/n): {RESET}"
        else:
            prompt = f"{prompt_color}{question} ({default}): {RESET}"
    else:
        prompt = f"{prompt_color}{question}: {RESET}"

    answer = input(prompt).strip()
    if answer == "" and default is not None:
        return default
    return answer


def output(text):
    print(f"{GREEN}> {text}{RESET}")


def error(text):
    print(f"{RED}{text}{RESET}")


def make_step(msg, step_color=CYAN):
    global step
    step = step + 1
    #print("\n" + "-" * 80)
    print("")
    print(f"{step_color}[STEP {step}] {msg}{RESET}")


def run(cmd, cwd=None, silent=False, echo=True, check=True):
    """
    Run `cmd` (shell command) with captured stdout/stderr (merged).

    - `silent=True` suppresses live streaming while the command runs BUT
      will print the captured output if the command fails, so that failure
      diagnostics are not lost. Previously a silent failure only printed
      "Command failed!" with zero context, which made problems like a
      failed `git pull` impossible to diagnose from the terminal.
      NOTE: the module-level `VERBOSE` flag (set by --verbose on the CLI)
      forces silent=False for every run() call, so the user can see every
      command's output live.
    - `echo=False` suppresses the `>>> cmd` banner.
    - `check=True` (the default, preserving old behaviour) makes this
      sys.exit() on non-zero status. Pass `check=False` to get the captured
      output back instead of exiting.
    """
    # --verbose overrides any per-call silent=True request
    if VERBOSE:
        silent = False
    if not silent and echo:
        print(f"{BLUE}>>> {cmd}{RESET}")
    process = subprocess.Popen(
        cmd,
        shell=True,
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True
    )

    output_lines = []
    for line in process.stdout:
        line_clean = line.replace('\r', '').strip()
        if not silent and line_clean and "Reading database" not in line_clean:
            print(line_clean)
        output_lines.append(line)

    process.wait()
    captured = ''.join(output_lines)

    if process.returncode != 0:
        if silent or not echo:
            print(f"{BLUE}>>> {cmd}{RESET}")
        # Surface the captured output so the user can see WHY the command
        # failed (previously this was swallowed when silent=True).
        if silent and captured.strip():
            for line in captured.splitlines():
                if line.strip() and "Reading database" not in line:
                    print(line)
        print(f"{RED}Command failed (exit {process.returncode}){RESET}")
        if not check:
            return captured
        sys.exit(process.returncode)
    return captured


def check_command(cmd):
    result = subprocess.run(
        ["which", cmd],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )
    return result.returncode == 0


def is_valid_email(email):
    if not email or len(email) > 254 or any(c.isspace() for c in email):
        return False
    parts = email.split("@")
    if len(parts) != 2:
        return False
    local, domain = parts
    if not local or len(local) > 64:
        return False
    if local.startswith(".") or local.endswith(".") or ".." in local:
        return False
    if not _EMAIL_LOCAL_RE.match(local):
        return False
    labels = domain.split(".")
    if len(labels) < 2:
        return False
    for label in labels:
        if not _EMAIL_LABEL_RE.match(label):
            return False
    if not _EMAIL_TLD_RE.match(labels[-1]):
        return False
    return True


def is_valid_subdomain(name):
    if len(name) < 3 or len(name) > 63:
        return False
    pattern = r"^(?!-)[a-z0-9-]{1,63}(?<!-)$"
    return bool(re.match(pattern, name))


# Interface-name prefixes we don't want to offer as "private server IP"
# candidates: docker0 and per-network bridges (br-*), veth pairs inside
# containers, VPN/tun-tap and libvirt/kubernetes bridges.
_VIRTUAL_IFACE_PREFIXES = (
    "docker", "br-", "veth", "tun", "tap", "virbr", "cni", "kube", "flannel",
)


def _is_virtual_iface(name):
    return any(name.startswith(p) for p in _VIRTUAL_IFACE_PREFIXES)


def get_ips(skip_virtual=True):
    results = []
    import psutil

    for interface, addrs in psutil.net_if_addrs().items():
        if skip_virtual and _is_virtual_iface(interface):
            continue
        for addr in addrs:
            if addr.family == socket.AF_INET:
                ip = addr.address

                ip_obj = ipaddress.ip_address(ip)

                if ip_obj.is_loopback or ip_obj.is_link_local:
                    continue
                results.append({
                    "interface": interface,
                    "ip": ip,
                    "type": "private" if ip_obj.is_private else "public"
                })

    return results


_PUBLIC_IP_SERVICES = (
    "https://api.ipify.org?format=text",
    "https://ifconfig.me/ip",
    "https://ipv4.icanhazip.com",
)


def _detect_public_ip(timeout=5):
    """
    Try to detect the public IPv4 of this host.

    Order of attempts:
      1. A locally-configured public IPv4 address (as reported by get_ips()),
         which avoids any outbound HTTP call when the host actually owns its
         public IP.
      2. A sequence of public lookup services (ipify, ifconfig.me,
         icanhazip). The first one that returns a syntactically valid IPv4
         wins.

    Returns a tuple (ip, source, errors):
      - ip: the detected IPv4 as a string, or None on total failure.
      - source: a human-readable description of where the address came from,
                or None when nothing worked.
      - errors: list of "service: reason" strings for the methods that
                failed (may be empty even on success).
    """
    errors = []

    # Step 1: try locally-assigned public IPs first.
    try:
        for entry in get_ips():
            if entry["type"] == "public":
                return entry["ip"], f"local interface {entry['interface']}", errors
    except Exception as e:
        errors.append(f"local interface scan: {e}")

    # Step 2: fall back to HTTP lookup services.
    for url in _PUBLIC_IP_SERVICES:
        try:
            with request.urlopen(url, timeout=timeout) as response:
                body = response.read().decode().strip()
            ipaddress.ip_address(body)  # validates IPv4/IPv6
            # We only want IPv4 here; reject anything that isn't.
            if ":" in body:
                errors.append(f"{url}: returned non-IPv4 address {body}")
                continue
            return body, url, errors
        except (urlerror.URLError, socket.timeout, ValueError, OSError) as e:
            errors.append(f"{url}: {e}")
        except Exception as e:
            errors.append(f"{url}: {e}")

    return None, None, errors

def _pick_private_ip(preferred=None):
    """
    Interactive picker for the private server IP. Lists every private IPv4
    address psutil reports (skipping loopback, link-local and the Docker /
    virtual bridges) and lets the user select one by number, or type a
    custom IP address. Returns the chosen IP as a string.
    """
    private_ips = [i for i in get_ips() if i["type"] == "private"]

    if not private_ips:
        # No sensible candidates detected (e.g. host-only container). Fall
        # back to a free-form prompt, using the previously-saved value or
        # 127.0.0.1 as the default.
        default = preferred or "127.0.0.1"
        while True:
            entered = question(1, "", "Private server IP address", default=default).strip()
            try:
                ipaddress.ip_address(entered)
                return entered
            except ValueError:
                error("Invalid IP address, please enter a valid IPv4 address.")

    # Show the detected candidates as a numbered list. If the previously
    # saved value matches one of them, default to that row; otherwise
    # default to the first entry.
    default_idx = 1
    if preferred:
        for idx, entry in enumerate(private_ips, 1):
            if entry["ip"] == preferred:
                default_idx = idx
                break

    print("")
    print(f"{YELLOW}Detected private IP addresses on this server:{RESET}")
    for idx, entry in enumerate(private_ips, 1):
        marker = " <-- saved" if preferred and entry["ip"] == preferred else ""
        print(f"    [{idx}] {entry['interface']:<12} {entry['ip']}{marker}")

    while True:
        choice = question(
            1, "",
            f"Select private IP (1-{len(private_ips)} or type a custom IP)",
            default=str(default_idx),
        ).strip()

        if not choice:
            choice = str(default_idx)

        # Numeric selection into the list?
        if choice.isdigit():
            idx = int(choice)
            if 1 <= idx <= len(private_ips):
                return private_ips[idx - 1]["ip"]
            error(f"Number out of range; choose 1-{len(private_ips)} or type an IP address.")
            continue

        # Otherwise treat the input as a literal IP address.
        try:
            ipaddress.ip_address(choice)
            return choice
        except ValueError:
            error("Not a valid IP address; choose a number from the list or type an IPv4 address.")


def generate_silly_name(n=2):
    #return "".join(random.choice(starts) + random.choice(middles) + random.choice(ends) for _ in range(n)).lower()
    return random.choice(WORDS).lower()


def dns_exists(name, domain=DOMAIN):
    fqdn = f"{name}.{domain}"
    try:
        socket.gethostbyname(fqdn)
        return True
    except socket.gaierror:
        return False


def get_unique_short_subdomain(max_attempts=1000, domain="sylk.link"):
    for _ in range(max_attempts):
        base_name = generate_silly_name()
        candidate = base_name

        suffix = 1
        while dns_exists(candidate, domain):
            candidate = f"{base_name}{suffix}"
            suffix += 1

        if not dns_exists(candidate, domain):
            return candidate

    raise RuntimeError("Kon geen unieke subdomein naam vinden!")


def install_docker():
    run("apt-get update -qq && apt-get install -y ca-certificates curl gnupg > /dev/null", silent=True)
    if not check_command("docker"):
        run("apt-get update -qq", silent=True)
        run("apt-get install -y -qq docker.io docker-compose > /dev/null", silent=True)
        run("systemctl enable docker", silent=True)
        run("systemctl start docker", silent=True)
    else:
        output("Docker already installed")


def clone_repo():
    """
    Clone the repository once. If the destination already exists, leave it
    completely alone — no `git pull`, no status check, no touching tracked
    files. This lets the user apply local edits (for debugging, tweaking
    configs, etc.) without having them blown away or blocking the next
    install. To pull updates, the user can run git manually; to start
    fresh they can delete DEST_DIR and re-run the installer.
    """
    if DEST_DIR.exists():
        output(f"Repo already exists at {DEST_DIR}, leaving it as-is "
               f"(delete it manually if you want a fresh clone).")
        return

    if not check_command("git"):
        run("apt install -y -qq git", silent=True)
    output(f"Cloning repo into {DEST_DIR}...")
    run(f"git clone {REPO_URL} {DEST_DIR}", silent=True)


CONNTRACK_CONF_FILE = "/etc/modprobe.d/nf_conntrack.conf"
CONNTRACK_CONF_DESIRED = "options nf_conntrack enable_hooks=1\n"
# Touched after a successful in-place reload so subsequent install.py
# runs in the same boot session skip the (disruptive) docker restart.
# /run is tmpfs on Debian, so the marker disappears automatically on
# reboot, which is exactly what we want.
CONNTRACK_RELOAD_MARKER = "/run/sylk-suite-conntrack-hooks-reloaded"


def _get_boot_time():
    """Return the POSIX timestamp of the system boot, or None if unknown."""
    try:
        with open("/proc/stat") as f:
            for line in f:
                if line.startswith("btime "):
                    return int(line.split()[1])
    except OSError:
        pass
    return None


def _conntrack_hooks_effective():
    """Best-effort: are we confident nf_conntrack was loaded with
    `enable_hooks=1` in the currently-running kernel?

    Returns a tuple ``(effective, reason)`` so the caller can log *why*
    the state was (or was not) considered good without having to
    re-derive the evidence.

    The kernel declares `enable_hooks` with `module_param(..., 0000)`, so
    `/sys/module/nf_conntrack/parameters/enable_hooks` is NEVER created
    and we cannot ask the running kernel directly. We have to infer from
    surrounding filesystem evidence:

      A. We dropped a /run marker after hot-reloading in the current boot
         session. If it's there, the reload already happened.
      B. /etc/modprobe.d/nf_conntrack.conf exists with the right content
         AND its mtime predates the current boot. In that case the kernel
         would have read it when modprobe first brought nf_conntrack up
         during early boot.

    Anything else (conf missing, conf newer than boot, module not
    loaded) means we have to take action to get into the right state.
    """
    # (A) marker from a prior run this boot
    if os.path.exists(CONNTRACK_RELOAD_MARKER):
        return True, f"marker {CONNTRACK_RELOAD_MARKER} present (reloaded earlier this boot)"

    # (B) conf on disk before this boot AND module already loaded
    if not os.path.exists(CONNTRACK_CONF_FILE):
        return False, f"{CONNTRACK_CONF_FILE} does not exist"
    try:
        with open(CONNTRACK_CONF_FILE) as f:
            if f.read() != CONNTRACK_CONF_DESIRED:
                return False, f"{CONNTRACK_CONF_FILE} has unexpected content"
    except OSError as e:
        return False, f"could not read {CONNTRACK_CONF_FILE}: {e}"
    if not os.path.isdir("/sys/module/nf_conntrack"):
        return False, "nf_conntrack module is not currently loaded"

    boot_time = _get_boot_time()
    if boot_time is None:
        return False, "could not determine boot time from /proc/stat"
    try:
        conf_mtime = os.path.getmtime(CONNTRACK_CONF_FILE)
    except OSError as e:
        return False, f"could not stat {CONNTRACK_CONF_FILE}: {e}"
    # The conf must have been on disk *before* the system booted, so the
    # kernel's module-load would have picked up its options.
    if conf_mtime < boot_time:
        return True, (
            f"conf mtime {int(conf_mtime)} predates boot {boot_time} "
            "-> kernel would have honored enable_hooks at module load"
        )
    return False, (
        f"conf mtime {int(conf_mtime)} is newer than boot {boot_time} "
        "-> module was loaded before our option file was in place"
    )


def _loaded_modules():
    """Return the set of currently-loaded kernel module names (from /proc/modules)."""
    try:
        with open("/proc/modules") as f:
            return {line.split()[0] for line in f if line.strip()}
    except OSError:
        return set()


def _modules_using(target):
    """Return the set of currently-loaded modules that use `target`.

    /proc/modules line format:
        <name> <size> <refcount> <used_by_comma_list_or_minus>
    The "used_by" list on the line for `target` is exactly what we want
    -- it's the names of modules the kernel says depend on it (i.e. hold
    a reference to it), which is the same info `lsmod`'s "Used by"
    column displays.
    """
    try:
        with open("/proc/modules") as f:
            for line in f:
                parts = line.split()
                if len(parts) < 4 or parts[0] != target:
                    continue
                used_by = parts[3]
                if used_by == "-":
                    return set()
                return {u for u in used_by.split(",") if u}
    except OSError:
        pass
    return set()


def _mark_conntrack_reloaded():
    try:
        with open(CONNTRACK_RELOAD_MARKER, "w") as f:
            f.write("")
    except OSError:
        # Not being able to write the marker only costs us a redundant
        # reload on the next run — never fail the installer for it.
        pass


def ensure_conntrack_hooks_enabled():
    """Guarantee nf_conntrack is loaded with `enable_hooks=1` so MediaProxy
    can install NAT entries for RTP flows via NFNETLINK_CONNTRACK.

    Behaviour: this function ALWAYS performs the live reload when the
    module is currently loaded, on every call. The previous heuristic
    that skipped the reload when filesystem evidence suggested hooks
    were already effective has been removed by user request -- every
    install_mediaproxy() run now leaves nf_conntrack in a known-good
    state, at the cost of a docker bounce each time.

    Background: MediaProxy's relay inserts conntrack expectations from
    userspace for every RTP stream it bridges. The kernel's nf_conntrack
    only registers its hooks in a network namespace when something asks
    for tracking — normally that's a netfilter rule that uses
    `ct state`, NAT, etc. For the UDP flows MediaProxy cares about there
    is no such rule, so the kernel silently tears down the entries
    MediaProxy inserts, and RTP reverse-path breaks. Loading nf_conntrack
    with the `enable_hooks=1` module parameter forces the hooks to
    register unconditionally, which is exactly what we need.

    `enable_hooks` is a module parameter, so it can ONLY be set at module
    load time. Dropping `options nf_conntrack enable_hooks=1` into
    /etc/modprobe.d/ handles future boots, but if nf_conntrack was
    already loaded (docker implicitly loads it the moment its iptables
    rules materialise), the option is ignored until reboot. This
    function does the hot-reload in place so the user doesn't have to
    reboot.

    NOTE on detection: the kernel declares enable_hooks with perm=0, so
    /sys/module/nf_conntrack/parameters/enable_hooks is NEVER created and
    we cannot read the current value. See _conntrack_hooks_effective()
    for the fallback heuristic we use (boot-time vs conf mtime + a /run
    marker for re-runs in the same boot session).

    The reload sequence is:
      1. Stop docker (it's the main holder of conntrack via iptables).
      2. Flush every iptables table so no rule references conntrack.
      3. modprobe -r the dependent modules, then nf_conntrack itself.
      4. modprobe nf_conntrack enable_hooks=1.
      5. Restart docker, which rebuilds its iptables/conntrack state.

    If step 3 or 4 fails (e.g. some other service holds the module open),
    we surface a clear message asking the user to reboot; nothing gets
    left in a broken state because docker is always started again at the
    end.
    """
    # Chatty diagnostic lines -- only surfaced when --verbose is on.
    # The core action/progress lines further down stay unconditional
    # because when a reload is actually happening the user should see it.
    def vlog(msg):
        if VERBOSE:
            output(msg)

    vlog("Checking nf_conntrack hook state (required for MediaProxy RTP relay)...")

    # 1. Persist the option so future boots pick it up automatically.
    try:
        existing = ""
        if os.path.exists(CONNTRACK_CONF_FILE):
            with open(CONNTRACK_CONF_FILE) as f:
                existing = f.read()
        if existing != CONNTRACK_CONF_DESIRED:
            with open(CONNTRACK_CONF_FILE, "w") as f:
                f.write(CONNTRACK_CONF_DESIRED)
            vlog(f"  wrote {CONNTRACK_CONF_FILE}")
        else:
            vlog(f"  {CONNTRACK_CONF_FILE} already has enable_hooks=1")
    except OSError as e:
        if os.path.exists("/etc/modprobe.d"):
            error(f"Could not write {CONNTRACK_CONF_FILE}: {e}")
        return

    # 2. Diagnostic only -- we always proceed to the live reload so that
    # every install_mediaproxy() run leaves the kernel in a known-good
    # state. The previous "skip if already effective" fast-path was
    # removed by user request; the reasoning the heuristic produced is
    # still useful in --verbose logs for debugging.
    effective, reason = _conntrack_hooks_effective()
    if effective:
        vlog(f"  nf_conntrack hooks look effective ({reason}),"
             " but reloading anyway -- forced live reload at every mediaproxy run")
    else:
        vlog(f"  reload needed: {reason}")

    # 3. Module not loaded at all -> just load it with the option. Cheap.
    if not os.path.isdir("/sys/module/nf_conntrack"):
        vlog("  nf_conntrack not loaded; loading with enable_hooks=1...")
        run("modprobe nf_conntrack enable_hooks=1", silent=True, echo=False, check=False)
        _mark_conntrack_reloaded()
        vlog("  [ok]   nf_conntrack loaded with enable_hooks=1")
        return

    # 4. Module loaded -> always live-reload (disruptive).
    vlog("Reloading nf_conntrack with enable_hooks=1 (needed for MediaProxy RTP relay)...")

    docker_was_active = subprocess.run(
        ["systemctl", "is-active", "--quiet", "docker"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    ).returncode == 0

    if docker_was_active:
        # Stop docker.socket too so systemd doesn't immediately revive it
        # the moment a client reconnects.
        vlog("  stopping docker...")
        run("systemctl stop docker docker.socket", silent=True, echo=False, check=False)

    # Flush every iptables table so no rule references conntrack. Without
    # this, `modprobe -r nf_conntrack` will fail with "Module nf_conntrack
    # is in use".
    vlog("  flushing iptables rules (filter/nat/mangle/raw)...")
    for table in ("filter", "nat", "mangle", "raw"):
        run(f"iptables -t {table} -F", silent=True, echo=False, check=False)
        run(f"iptables -t {table} -X", silent=True, echo=False, check=False)

    # On Debian 12 `iptables` is actually iptables-nft, and Docker also
    # creates native nftables tables of its own. `iptables -F` only
    # touches rules through the compatibility backend -- rules and
    # tables created via the `nft` command (or via Docker's nft
    # integration) survive, and they keep xt_nat / nft_chain_nat pinned,
    # which in turn keeps nf_nat pinned, which keeps nf_conntrack
    # pinned. Flush the entire nftables ruleset too. Harmless on hosts
    # without nft installed; destructive only to rules we're about to
    # recreate via `systemctl start docker` at the end anyway.
    vlog("  flushing nftables ruleset...")
    run("nft flush ruleset", silent=True, echo=False, check=False)

    # Remove the modules that keep nf_conntrack held. Order matters: leaf
    # modules first, then nf_conntrack itself. Missing/already-unloaded
    # modules are ignored (check=False).
    # Every module that can hold an nf_conntrack reference. These fall
    # into four groups:
    #   * legacy xt_* iptables matches/targets (xt_CT is the `-j CT`
    #     target, distinct from xt_conntrack which is `-m conntrack`)
    #   * legacy iptable_* table modules
    #   * nft_* chain/target modules — these are the ones Debian 12 uses
    #     because `iptables` is shimmed onto nftables there, and
    #     `iptables -F` does NOT unload them; nft_chain_nat in particular
    #     will keep nf_nat (and thus nf_conntrack) pinned until we
    #     explicitly remove it
    #   * the nf_* netfilter helpers
    # Missing modules are tolerated silently — the subprocess call runs
    # without `check`, and we iterate one module at a time so an early
    # failure doesn't abort the rest.
    leaf_modules = [
        # Legacy xt_*
        "xt_CT", "xt_MASQUERADE", "xt_conntrack", "xt_connmark", "xt_nat",
        "xt_REDIRECT", "xt_NETMAP",
        # Legacy iptable_*
        "iptable_nat", "iptable_mangle", "iptable_raw",
        # nftables backend chains/targets (Debian 12 default)
        "nft_chain_nat", "nft_masq", "nft_nat",
        # nf_* helpers
        "nf_nat_ftp", "nf_nat_redirect", "nf_nat_masquerade", "nf_nat",
        "nf_conntrack_netlink", "nf_conntrack_ftp",
        "nf_defrag_ipv4", "nf_defrag_ipv6",
    ]
    # `modprobe -r` with multiple modules stops at the first failure, so
    # remove them one by one to maximise how much we can unload. Two
    # passes: the first does the bulk of the work; the second catches
    # modules that became unloadable after their users were removed in
    # pass 1 (e.g. nf_nat frees up once xt_nat is gone).
    vlog("  unloading dependent modules:")
    def _try_rmmod(mod):
        rc = subprocess.run(
            ["modprobe", "-r", mod],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        )
        return rc.returncode, (rc.stderr or b"").decode("utf-8", "replace").strip()

    # Pass 1 -- everyone gets a shot, in order.
    still_loaded = []
    for mod in leaf_modules:
        if mod not in _loaded_modules():
            continue  # Nothing to do; don't log noise for it.
        code, err = _try_rmmod(mod)
        if code == 0:
            vlog(f"    [ok]   rmmod {mod}")
        else:
            still_loaded.append(mod)
            holders = _modules_using(mod)
            holder_info = f"held by {', '.join(sorted(holders))}" if holders else (err or "failed")
            vlog(f"    [skip] rmmod {mod}: {holder_info}")

    # Pass 2 -- retry only the ones that failed, since pass 1 may have
    # removed their users. Stop once a full pass makes no progress.
    while still_loaded:
        progress = []
        for mod in still_loaded:
            if mod not in _loaded_modules():
                # Someone else's removal took it with them.
                vlog(f"    [ok]   {mod} auto-removed")
                continue
            code, err = _try_rmmod(mod)
            if code == 0:
                vlog(f"    [ok]   rmmod {mod} (retry)")
            else:
                progress.append(mod)
        if progress == still_loaded:
            break  # No progress; give up and let the caller report.
        still_loaded = progress

    # Show what's still holding nf_conntrack before we try to remove it.
    holders = _modules_using("nf_conntrack")
    if holders:
        vlog(f"  nf_conntrack still held by: {', '.join(sorted(holders))}")
    else:
        vlog("  nf_conntrack has no remaining holders")

    vlog("  unloading nf_conntrack...")
    unload = subprocess.run(
        ["modprobe", "-r", "nf_conntrack"],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )
    if unload.returncode != 0:
        # Something we didn't account for is still holding the module.
        err_text = (unload.stderr or b"").decode("utf-8", "replace").strip()
        still = _modules_using("nf_conntrack")
        error(f"Could not unload nf_conntrack: {err_text or 'unknown error'}")
        if still:
            error(f"  still held by: {', '.join(sorted(still))}")
            error(f"  (add these to leaf_modules in install.py and re-run)")
        error("MediaProxy will need a reboot before RTP relay works correctly.")
        # Restart docker so we don't leave the host without networking.
        if docker_was_active:
            run("systemctl start docker", silent=True, echo=False, check=False)
        return

    vlog("  [ok]   nf_conntrack unloaded")

    vlog("  loading nf_conntrack enable_hooks=1...")
    reload = subprocess.run(
        ["modprobe", "nf_conntrack", "enable_hooks=1"],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )
    if reload.returncode != 0:
        err_text = (reload.stderr or b"").decode("utf-8", "replace").strip()
        error(f"Failed to reload nf_conntrack: {err_text or 'unknown error'}")
        error("Please reboot before running MediaProxy.")
    else:
        output("nf_conntrack reloaded with enable_hooks=1 (no reboot required).")
        _mark_conntrack_reloaded()

    # Bring docker (and thereby the sylk containers + their iptables rules) back.
    if docker_was_active:
        run("systemctl start docker", silent=True, echo=False, check=False)


def cleanup_orphan_docker_bridges():
    """Remove docker-created bridge interfaces no longer owned by any
    docker network.

    Every `docker network create` (and every docker-compose run that
    materialises its declared networks) adds a Linux bridge named
    br-<network-id[:12]>. `docker network rm` is supposed to delete the
    bridge too, but if the host crashes mid-teardown — or if the user
    runs docker-compose from a different working directory and ends up
    with a second project that creates collidingly-named networks — the
    kernel keeps the orphan bridge interface forever, usually still
    claiming the same subnet docker handed out to it originally.

    Symptom when this happens: a fresh container gets IP 172.18.0.2 on
    its real bridge, but `ip route get 172.18.0.2` resolves to a DEAD
    orphan bridge that also claims 172.18.0.0/16. docker-proxy then
    accepts on the published port but its forwarded packets get
    No-route-to-host, so from outside it looks like "TCP connects, TLS
    handshake RSTs" while from inside the container everything works.

    This function is safe to call repeatedly: it only deletes interfaces
    that are (a) named br-<hex>, (b) currently DOWN/no-carrier, and (c)
    unknown to docker. Anything that's UP — i.e. has live veths attached
    — is left strictly alone.
    """
    if not check_command("docker"):
        return  # Nothing to clean if docker isn't even installed yet.

    net_dir = "/sys/class/net"
    try:
        candidates = [n for n in os.listdir(net_dir) if n.startswith("br-")]
    except OSError:
        return

    if not candidates:
        return

    # Networks docker currently knows about. We compare against the first
    # 12 characters of the network ID, which is what shows up after the
    # `br-` prefix in the kernel interface name.
    captured = run(
        "docker network ls --no-trunc --format '{{.ID}}'",
        silent=True, echo=False, check=False,
    )
    known_prefixes = {
        line.strip()[:12] for line in captured.splitlines() if line.strip()
    }

    removed = []
    for ifname in candidates:
        prefix = ifname[len("br-"):]
        if prefix in known_prefixes:
            continue  # Live bridge owned by a real docker network.

        # Only touch DOWN bridges. An UP bridge has at least one live veth
        # attached, which means a real container is using it -- almost
        # certainly we just misidentified ownership and removing it would
        # break that container.
        try:
            with open(f"{net_dir}/{ifname}/operstate") as f:
                state = f.read().strip()
        except OSError:
            continue
        if state == "up":
            continue

        # Use subprocess directly so we can swallow the (usually empty)
        # output without it cluttering the install log.
        result = subprocess.run(
            ["ip", "link", "delete", ifname],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if result.returncode == 0:
            removed.append(ifname)

    if removed:
        output(f"Removed {len(removed)} orphan docker bridge(s): {', '.join(removed)}")


def start_sylk_suite(data):
    # Sweep up any docker bridge interfaces left behind by previous
    # installer runs (especially ones launched from a different cwd).
    # If an orphan bridge claims the same /16 the new sylk-net is about
    # to get, the kernel routes container traffic to the dead bridge and
    # port 60000 stops working -- see cleanup_orphan_docker_bridges().
    cleanup_orphan_docker_bridges()

    run("cp -r ./sylkserver/config-templates ./sylkserver/config", cwd=DEST_DIR, silent=True)
    run("cp -r ./janus/config-templates ./janus/config", cwd=DEST_DIR, silent=True)
    run(compose("up -d"), cwd=DEST_DIR)
    sleep(1)
    run("chmod +x certbot/hooks/auth.py certbot/hooks/cleanup.py", cwd=DEST_DIR, silent=True)
    # Remove stale certbot lock file left behind by interrupted runs
    run(compose('run -T --rm --entrypoint "" certbot rm -f /etc/letsencrypt/.certbot.lock'),
        cwd=DEST_DIR, silent=True)
    run(
        compose(f"""run -T --rm --entrypoint "" certbot certbot certonly \
        --manual \
        --preferred-challenges dns \
        --manual-auth-hook /hooks/auth.py \
        --manual-cleanup-hook /hooks/cleanup.py \
        -d {data.full_domain} \
        -d xcap.{data.full_domain} \
        --email {data.email} \
        --agree-tos \
        --no-eff-email"""),
        cwd=DEST_DIR,
        echo=False
    )
    run("cp ./webrtc-nginx/domain.conf ./webrtc-nginx/conf/", cwd=DEST_DIR, silent=True)
    run(f"sed -i 's/FULLDOMAIN/{data.full_domain}/g' ./webrtc-nginx/conf/domain.conf", cwd=DEST_DIR, silent=True)
    run("docker exec sylk-webrtc nginx -s reload", silent=True)
    output("Nginx Web server root folder: %s/webrtc-nginx/html/" % DEST_DIR)
    run("mkdir -p ./webrtc-nginx/html", cwd=DEST_DIR, silent=True)
    regenerate = False
    try:
        with open(f"{DEST_DIR}/webrtc-nginx/html/sylk-config.json") as f:
            cfg = f.read()
            cfg_parsed = json.loads(cfg)
            expected_url = f"https://{data.full_domain}" if data.web_port == '443' else f"https://{data.full_domain}:{data.web_port}"
            regenerate = cfg_parsed['publicUrl'] != expected_url
    except (FileNotFoundError, json.JSONDecodeError):
        pass

    if regenerate:
        output("Domain or port changed, rebuilding web app and config")
        run(compose("build --no-cache webrtc"), cwd=DEST_DIR, silent=True)
        run(compose("up -d"), cwd=DEST_DIR, silent=True)
    run("docker cp sylk-webrtc:/usr/share/nginx/html/. ./webrtc-nginx/html/", cwd=DEST_DIR, silent=True)
    # update_sylk_config(data)
    output("SylkServer configuration folder: %s/sylkserver/config/" % DEST_DIR)
    output("Janus server configuration folder: %s/janus/config/" % DEST_DIR)


def install_opensips(data, mysql=True, force_mysql=False):
    run("curl -fsSL https://download.ag-projects.com/agp-debian-key.gpg -o /usr/share/keyrings/agp-debian-key.gpg", silent=True)
    run('echo "deb [signed-by=/usr/share/keyrings/agp-debian-key.gpg] https://packages.ag-projects.com/debian bookworm main contrib" > /etc/apt/sources.list.d/ag-projects.list', silent=True)

    run("curl -fsSL https://apt.opensips.org/opensips-org.gpg -o /usr/share/keyrings/opensips-org.gpg", silent=True)
    run('echo "deb [signed-by=/usr/share/keyrings/opensips-org.gpg] https://apt.opensips.org bookworm 3.5-releases" > /etc/apt/sources.list.d/opensips.list', silent=True)
    run('echo "deb [signed-by=/usr/share/keyrings/opensips-org.gpg] https://apt.opensips.org bookworm cli-nightly" > /etc/apt/sources.list.d/opensips-cli.list', silent=True)

    run("apt-get update -qq && apt-get install -qq -y opensips-config-sylkserver > /dev/null", silent=True)

    run(f"cp -L {DEST_DIR}/certbot/conf/live/{data.full_domain}/fullchain.pem  /etc/opensips/tls/default.crt", silent=True)
    run(f"cp -L {DEST_DIR}/certbot/conf/live/{data.full_domain}/privkey.pem  /etc/opensips/tls/default.key", silent=True)

    if data.nat:
        subprocess.run(
            ["sed", "-i", rf"s#^define(`SERVER_IP',.*#define(`SERVER_IP', `{data.local_ip}')#",
             "/etc/opensips/config/settings.m4"],
            check=True
        )

        sed_expr = "/SYLK_SERVER_IP/s/\\`127.0.0.1'/\\`172.18.0.10'/g"
        run(f"sed -i {sed_expr} /etc/opensips/config/settings.m4", silent=True)

        subprocess.run(
            ["sed", "-i", rf"s#^define(`PUSH_SERVER_URL',.*#define(`PUSH_SERVER_URL', `{PUSH_URL}')#",
             "/etc/opensips/config/settings.m4"],
            check=True
        )

        sed_expr = f"/ADVERTISED_SERVER_IP/s/\\`SERVER_IP'/\\`{data.ip}'/g"
        run(f"sed -i {sed_expr} /etc/opensips/config/settings.m4", silent=True)

        sed_expr = f"s/, SERVER_IP)/, \\`{data.ip}\\')/g"
        run(f"sed -i \"{sed_expr}\" /etc/opensips/config/settings.m4", silent=True)

        subprocess.run(
            ["sed", "-i", r"s#^define(`ENABLE_LATE_FORKING',.*#define(`ENABLE_LATE_FORKING', `1')#",
             "/etc/opensips/config/settings.m4"],
            check=True
        )
        # subprocess.run(
        #     ["sed", "-i", r"s#^define(`ENABLE_TLS',.*#define(`ENABLE_TLS', `0')#",
        #      "/etc/opensips/config/settings.m4"],
        #     check=True
        # )

        sed_expr = f"/ADVERTISED_SERVER_IP/s/, \\`[^']*'/, \\`{data.ip}'/"
        run(f'sed -i "{sed_expr}" /etc/opensips/config/settings.m4', silent=True)

        sip_port = data.sip_port
        sip_tls_port = int(sip_port) + 1

        sed_expr = f"/SYLK_SERVER_IP_SOURCE/s/, \\`[^']*'/, \\`{data.ip}'/"
        run(f'sed -i "{sed_expr}" /etc/opensips/config/settings.m4', silent=True)

        sed_expr = f"/SERVER_UDP_PORT/s/, \\`[^']*'/, \\`{sip_port}'/"
        run(f'sed -i "{sed_expr}" /etc/opensips/config/settings.m4', silent=True)

        sed_expr = f"/SERVER_TCP_PORT/s/, \\`[^']*'/, \\`{sip_port}'/"
        run(f'sed -i "{sed_expr}" /etc/opensips/config/settings.m4', silent=True)

        sed_expr = f"/SERVER_TLS_PORT/s/, \\`[^']*'/, \\`{sip_tls_port}'/"
        run(f'sed -i "{sed_expr}" /etc/opensips/config/settings.m4', silent=True)

    else:
        subprocess.run(
            ["sed", "-i", rf"s#^define(`SERVER_IP',.*#define(`SERVER_IP', `{data.ip}')#",
             "/etc/opensips/config/settings.m4"],
            check=True
        )

    run("opensips-config", cwd='/etc/opensips/', silent=True)

    if mysql:
        db_exists = subprocess.run(
            ["mysql", "-e", "USE opensips"],
            capture_output=True
        ).returncode == 0

        if not db_exists or force_mysql:
            run("opensips-dbinit", cwd='/etc/opensips/', silent=True)
            run("cat /usr/share/doc/opensips-config-sylkserver/push_tokens.sql | sudo mysql opensips", silent=True)

        run(f"mysql opensips -e \"insert ignore into domain (domain) values ('{data.full_domain}')\"", silent=True)

    run("systemctl restart opensips", silent=True)
    run("opensips-cli -x mi domain_reload", silent=True)
    domain_dump = run("opensips-cli -x mi domain_dump", silent=True)
    try:
        domains = [d["name"] for d in json.loads(domain_dump).get("Domains", [])]
        output(f"OpenSIPS active domains: {', '.join(domains)}")
    except Exception:
        pass
    run("systemctl enable opensips", silent=True)

    try:
        with open("/etc/opensips/opensips.cfg") as f:
            cfg = f.read()

        advertised = re.search(r'advertised_address\s*=\s*"([^"]+)"', cfg)
        advertised_addr = advertised.group(1) if advertised else "unknown"

        udp_ports  = re.findall(r'socket\s*=\s*udp:[^:]+:(\d+)', cfg)
        tcp_ports  = re.findall(r'socket\s*=\s*tcp:[^:]+:(\d+)', cfg)
        tls_ports  = re.findall(r'socket\s*=\s*tls:[^:]+:(\d+)', cfg)

        parts = []
        for port in dict.fromkeys(udp_ports + tcp_ports):
            protos = []
            if port in udp_ports:
                protos.append("UDP")
            if port in tcp_ports:
                protos.append("TCP")
            parts.append(f"{port} {' '.join(protos)}")
        for port in dict.fromkeys(tls_ports):
            parts.append(f"{port} TLS")

        output(f"OpenSIPS listening on: {advertised_addr} ports ({', '.join(parts)})")
    except Exception:
        pass

    output("OpenSIPS installed")
    output("OpenSIPS routing logic: /etc/opensips/config/opensips.m4")
    output("OpenSIPS configuration: /etc/opensips/config/setting.m4")
    output("Run sudo /usr/sbin/opensips-config after changing the m4 files")


def install_mediaproxy(data):
    # MediaProxy's RTP relay relies on nf_conntrack being loaded with
    # enable_hooks=1 (see the function's docstring for the full story).
    # Without this, newly-created conntrack entries are destroyed by the
    # kernel and the RTP reverse path breaks — the symptom is "RTP works
    # only after rebooting the machine". Doing this here means we reload
    # conntrack at the exact point the operator is installing the
    # component that needs it, rather than at the very top of main()
    # where docker has not yet been (re)installed.
    #
    # NOTE: ensure_conntrack_hooks_enabled() now performs the live
    # reload unconditionally on every call (no boot-marker fast path),
    # so each install_mediaproxy() run will bounce docker once while
    # nf_conntrack is reloaded.
    #
    # ensure_conntrack_hooks_enabled() calls `nft flush ruleset` as part
    # of its cleanup, so make sure the nftables user-space CLI is
    # installed first (it's not present on all minimal Debian/Ubuntu/
    # Amazon Linux images).
    run("apt-get install -y -qq nftables > /dev/null", silent=True)
    ensure_conntrack_hooks_enabled()

    run("cp /usr/share/doc/mediaproxy-common/tls/* /etc/mediaproxy/tls/", silent=True)

    config_path = '/etc/mediaproxy/config.ini'

    config = configparser.ConfigParser()
    config.optionxform = str
    config.read(config_path)

    if 'Relay' not in config:
        config['Relay'] = {}

    config['Relay']['dispatchers'] = '127.0.0.1'
    if data.nat:
        config['Relay']['advertised_ip'] = f"{data.ip}"

    try:
        endport = int(data.rtp_port) + 500
        config['Relay']['port_range'] = '%s:%s' % (data.rtp_port, endport)
    except Exception:
         config['Relay']['port_range'] = "60004:60504"

    with open(config_path, 'w') as configfile:
        config.write(configfile)

    run("systemctl restart mediaproxy-dispatcher", silent=True)
    run("systemctl restart mediaproxy-relay", silent=True)
    run("systemctl enable mediaproxy-dispatcher", silent=True)
    run("systemctl enable mediaproxy-relay", silent=True)

    try:
        mp_config = configparser.ConfigParser()
        mp_config.optionxform = str
        mp_config.read('/etc/mediaproxy/config.ini')
        mp_ip = mp_config.get('Relay', 'advertised_ip', fallback=None) or mp_config.get('Relay', 'address', fallback='unknown')
        mp_ports = mp_config.get('Relay', 'port_range', fallback='unknown')
        output(f"MediaProxy listening on: {mp_ip} RTP port range {mp_ports}")
    except Exception:
        pass
    output("MediaProxy installed and configured")


def install_msrprelay(data):
    config_path = '/etc/msrprelay/config.ini'

    config = configparser.ConfigParser()
    config.optionxform = str
    config.read(config_path)
    if 'Relay' not in config:
        config['Relay'] = {}

    config['Relay']['certificate'] = f"{DEST_DIR}/certbot/conf/live/{data.full_domain}/fullchain.pem"
    config['Relay']['key'] = f"{DEST_DIR}/certbot/conf/live/{data.full_domain}/privkey.pem"
    config['Relay']['backend'] = "database"
    config['Relay']['hostname'] = f"{data.full_domain}"
    config['Relay']['address'] = f"0.0.0.0:{data.msrp_port}"

    if 'Database' not in config:
        config['Database'] = {}

    config['Database']['uri'] = "mysql://opensips:opensips@localhost/opensips"

    with open(config_path, 'w') as configfile:
        config.write(configfile)
    run("systemctl restart msrprelay", silent=True)
    run("systemctl enable msrprelay", silent=True)
    try:
        mr_config = configparser.ConfigParser()
        mr_config.optionxform = str
        mr_config.read('/etc/msrprelay/config.ini')
        hostname = mr_config.get('Relay', 'hostname', fallback='unknown')
        address  = mr_config.get('Relay', 'address', fallback='')
        port = address.split(':')[-1] if ':' in address else address
        output(f"MSRP Relay listening on: tls:{hostname}:{port}")
    except Exception:
        pass
    output("MSRP Relay installed and configured")


def install_openxcap(data):
    run("apt-get install -y -qq openxcap > /dev/null", silent=True)
    config_path = '/etc/openxcap/config.ini'

    config = configparser.ConfigParser()
    config.optionxform = str
    config.read(config_path)
    if 'Server' not in config:
        config['Server'] = {}

    docker_ip = run("ip addr show docker0 | grep 'inet ' | awk '{print $2}' | cut -d/ -f1", silent=True).strip()
    config['Server']['address'] = f"{docker_ip}"
    config['Server']['port'] = "8080"
    config['Server']['backend'] = "OpenSIPS"
    port = ":" + data.web_port if (data.web_port and data.web_port != "433") else ""
    config['Server']['root'] = f"https://xcap.{data.full_domain}{port}/xcap-root"

    if 'Authentication' not in config:
        config['Authentication'] = {}
    config['Authentication']['type'] = 'basic'
    config['Authentication']['default_realm'] = f"{data.full_domain}"

    if 'Database' not in config:
        config['Database'] = {}
    config['Database']['authentication_db_uri'] = 'mysql://opensips:opensips@localhost/opensips'
    config['Database']['storage_db_uri'] = 'mysql://opensips:opensips@localhost/opensips'

    if 'OpenSIPS' not in config:
        config['OpenSIPS'] = {}

    config['OpenSIPS']['outbound_sip_proxy'] = f'{data.full_domain}'

#    if 'TLS' not in config:
#        config['TLS'] = {}

#    config['TLS']['certificate'] = f"{DEST_DIR}/certbot/conf/live/{data.full_domain}/fullchain.pem"
#    config['TLS']['private_key']= f"{DEST_DIR}/certbot/conf/live/{data.full_domain}/privkey.pem"

    with open(config_path, 'w') as configfile:
        config.write(configfile)

    run("systemctl enable openxcap", silent=True)
    run("systemctl restart openxcap", silent=True)
    try:
        xcap_config = configparser.ConfigParser()
        xcap_config.optionxform = str
        xcap_config.read('/etc/openxcap/config.ini')
        root = xcap_config.get('Server', 'root', fallback='unknown')
        output(f"OpenXCAP XCAP root: {root}")
    except Exception:
        pass
    output("OpenXCAP installed and configured")


def update_sylk_config(data):
    port = f":{data.web_port}" if data.web_port and data.web_port != "443" else ""
    domain = data.full_domain

    config = {
        "defaultDomain":           domain,
        "enrollmentDomain":        domain,
        "nonSipDomains":           [],
        "publicUrl":               f"https://{domain}{port}",
        "enrollmentUrl":           f"https://{domain}{port}/enrollment/user",
        "defaultConferenceDomain": f"videoconference.{domain}",
        "defaultGuestDomain":      f"guest.{domain}",
        "wsServer":                f"wss://{domain}{port}/ws",
        "fileSharingUrl":          f"https://{domain}{port}/filesharing",
        "fileTransferUrl":         f"https://{domain}{port}/filetransfer",
        "iceServers":              [{"urls": "stun:stun.sipthor.net:3478"}],
        "muteGuestAudioOnJoin":    False,
        "guestUserPermissions": {
            "allowMuteAllParticipants":     False,
            "allowToggleHandsParticipants": False
        },
        "showGuestCompleteScreen": True,
        "downloadUrl":             "https://sylkserver.com",
        "testNumbers": [
            {"uri": f"echo@{domain}",     "name": "Test microphone"},
            {"uri": f"playback@{domain}", "name": "Test video"}
        ]
    }

    config_path = DEST_DIR / "webrtc-nginx" / "sylk-config.json"
    with open(config_path, 'w') as f:
        json.dump(config, f, indent=2)

    run(f"docker cp {config_path} sylk-webrtc:/usr/share/nginx/html/sylk-config.json", silent=True)
    run("mkdir -p ./webrtc-nginx/html", cwd=DEST_DIR, silent=True)
    run(f"cp {config_path} ./webrtc-nginx/html/sylk-config.json", cwd=DEST_DIR, silent=True)
    run("docker exec sylk-webrtc nginx -s reload", silent=True)
    output(f"Web frontend available at: https://{domain}{port}")
    output(f"Mobile app configuration file: {config_path}")


def restart_enrollment(data):
    config_path = '/etc/enrollment/config.ini'
    config = configparser.ConfigParser()
    config.optionxform = str
    config.read(config_path)
    if 'server' not in config:
        config['server'] = {}
    config['server']['domain'] = f"{data.full_domain}"
    with open(config_path, 'w') as configfile:
        config.write(configfile)
    run("systemctl restart enrollment", silent=True)


def install_enrollment(data):
    config_path = '/etc/enrollment/config.ini'
    try:
        os.mkdir('/etc/enrollment')
    except FileExistsError:
        pass

    run("cp ./enrollment/config.ini /etc/enrollment/", cwd=DEST_DIR, silent=True)
    run("cp ./enrollment/enrollment.service /etc/systemd/system/", cwd=DEST_DIR, silent=True)
    run("cp ./enrollment/enrollment.py /usr/bin/enrollment", cwd=DEST_DIR, silent=True)
    run("chmod +x /usr/bin/enrollment", silent=True)

    docker_ip = run("ip addr show docker0 | grep 'inet ' | awk '{print $2}' | cut -d/ -f1", silent=True).strip()
    config = configparser.ConfigParser()
    config.optionxform = str
    config.read(config_path)
    if 'server' not in config:
        config['server'] = {}

    config['server']['domain'] = f"{data.full_domain}"
    config['server']['host'] = f"{docker_ip}"
    config['server']['port'] = "8081"

    with open(config_path, 'w') as configfile:
        config.write(configfile)

    run("systemctl daemon-reload", silent=True)
    run("systemctl enable enrollment", silent=True)
    restart_enrollment(data)

    sylk_config_path = DEST_DIR / "webrtc-nginx" / "sylk-config.json"
    try:
        with open(sylk_config_path) as f:
            enrollment_url = json.load(f).get("enrollmentUrl", "")
            # strip trailing /user path segment
            enrollment_base = enrollment_url.rsplit("/", 1)[0] if enrollment_url.endswith("/user") else enrollment_url
        output(f"Enrollment started at: {enrollment_base}")
    except Exception:
        output("Enrollment installed and configured")


def create_domain(data):
    try:
        LOG_DIR = os.path.join(DEST_DIR, "certbot/logs")
        os.makedirs(LOG_DIR, exist_ok=True)
        MAINLOG_DIR = os.path.join(DEST_DIR, "logs")
        os.makedirs(MAINLOG_DIR, exist_ok=True)
        dns_mananagement_filename = os.path.join(MAINLOG_DIR, f"dns_management.json")

        payload = json.loads(data.json.decode("utf-8"))

        mdns_exists = False
        mdns = {}
        if os.path.exists(dns_mananagement_filename):
            with open(dns_mananagement_filename, "r", encoding="utf-8") as f:
                mdns = json.load(f)
                mdns_exists = True

        payload.update(mdns)
        for h in ('url', 'username', 'password', 'timestamp'):
            try:
                del payload[h]
            except KeyError:
                pass

        payload['action'] = "add_domain"
        json_data = json.dumps(payload).encode("utf-8")

        req = request.Request(ENROLLMENT_URL, data=json_data, headers={'Content-Type': 'application/json'}, method='POST')

        with request.urlopen(req) as response:
            # print(response.status, response.reason)
            result = json.loads(response.read().decode())
            if result['success']:
                payload = result.get('result', {})
                if not payload:
                    print("No data returned from enrollment server")
                    sys.exit(1)

                domain = payload.get('zone', 'unknown')
                dns_zone = domain + ".sylk.link"

                output("Sylk domain created %s at %s" % (dns_zone, ENROLLMENT_URL))

                ts = datetime.now(UTC).isoformat()

                for k in ("url", "username", "password", "auth_token", "customer_id"):
                    try:
                        mdns[k] = payload[k]
                    except KeyError:
                        pass

                mdns['timestamp'] = ts

                # Add timestamp
                log_data = {
                    "dns_zone": dns_zone,
                    **payload
                }

                for k in ("url", "username", "password", "zone", "ip", "local_ip", "nat", "email"):
                    try:
                        del log_data[k]
                    except KeyError:
                        pass

                if mdns_exists:
                    log_data["auth_token"] = mdns["auth_token"]
                    log_data["customer_id"] = mdns["customer_id"]

                else:
                    with open(dns_mananagement_filename, "w", encoding="utf-8") as f:
                        json.dump(mdns, f, indent=2)

                output("Managed DNS login credentials: %s" % dns_mananagement_filename)

                log_data['timestamp'] = ts
                filename = os.path.join(LOG_DIR, f"{domain}.sylk.link.json")

                # Use by certbot
                with open(filename, "w", encoding="utf-8") as f:
                    json.dump(log_data, f, indent=2)

                output("Managed DNS zone metadata: %s" % filename)

            else:
                sys.exit(1)
    except urlerror.HTTPError as e:
        error(f"HTTP Error {e.code}: {e.reason}")
        try:
            error_body = e.read().decode()
            result = json.loads(error_body)
            error(f"Server response: {result['error']}  - {result['error_message']}")
        except Exception:
            pass
        sys.exit(1)
    except urlerror.URLError as e:
        error("URL Error:", e.reason)
        sys.exit(1)
    except Exception as e:
        error("Unexpected error:", str(e))
        sys.exit(1)


def _ask_all_settings(data=None):
    """
    STEP 1 questions. Returns a freshly-constructed Info instance.
    If `data` is provided its fields are used as the defaults for each prompt.
    """
    global step
    # Reset so question() prints the STEP 1 header when we re-enter this
    # block after a "no" at the confirmation step.
    step = 0

    while True:
        email = question(1, "Sylk Suite installation data", "Enter your email address", default=data.email if data else 'support@ag-projects.com')
        if is_valid_email(email):
            break
        error("Invalid email, please enter a valid email address.")

    while True:
        silly_subdomain = question(1, "", f'Choose your Sylk domain under {DOMAIN}', default=data.zone if data else get_unique_short_subdomain()).lower()
        if dns_exists(silly_subdomain):
            error("Invalid Sylk domain, it already exists.")
            cont = question(1, "", 'Continue with existing domain?', default="Y")
            if cont in ("", "y", "yes"):
                break

            if cont in ("N", "n"):
                sys.exit(1)

        if is_valid_subdomain(silly_subdomain):
            break

        error("Invalid Sylk domain, please enter a valid one.")

    # Always re-detect the public IP rather than defaulting to the cached
    # value from setup.json: the server's public IP may have changed since
    # the last run, and the user just pressing Enter should always apply
    # whatever we just detected. We still show the cached value as a hint
    # if it differs from what we just detected, and we surface any detection
    # failures so the user knows why no default appeared.
    public_ip, source, detect_errors = _detect_public_ip()

    if public_ip:
        output(f"Detected public IP: {public_ip} (via {source})")
    else:
        error("Could not auto-detect public IP. Tried:")
        for line in detect_errors:
            error(f"  - {line}")
        if data and data.ip:
            output(f"Falling back to previously-saved public IP: {data.ip}")
            public_ip = data.ip

    if data and data.ip and public_ip and data.ip != public_ip:
        # Make the IP-change notification impossible to miss. When the
        # public IP changes (e.g. the cloud instance was stopped and
        # restarted without an Elastic/static IP), several components
        # baked the previous value into their config: MediaProxy's
        # advertised_ip, Janus's nat_1_1_mapping, OpenSIPS's SERVER_IP,
        # and the DNS records suggested at the end of the installer.
        # Continuing with this installer run will rewrite all of them
        # to the new IP, but the operator also has to update the DNS
        # A record for the domain — otherwise mobile and web clients
        # will still resolve to the old address and calls will break.
        print("")
        print(f"Previous IP : {data.ip}{RESET}")
        print("")

    ip_addr = question(1, "", "Public server IP address", default=public_ip or None).lower()

    web_port  = question(1, "", "Public Web Port", default=data.web_port if data else "60000")
    msrp_port = question(1, "", "Public MSRP Port", default=data.msrp_port if data else "60001")
    sip_port  = question(1, "", "Public SIP Port (+1 port for TLS)", default=data.sip_port if data else "60002")
    rtp_port  = question(1, "", "Public RTP range start (+1000 ports)", default=data.rtp_port if data else "60004")

    if data and isinstance(data.nat, bool):
        default_nat = "Y" if data.nat else "N"
    elif data:
        default_nat = data.nat
    else:
        default_nat = "Y"

    nat = question(1, "", "Behind 1-TO-1 NAT", default=default_nat).lower()

    local_ip = ''
    if nat in ("", "y", "yes", "Y") or nat:
        nat = True
        local_ip = _pick_private_ip(data.local_ip if data else None)

    return Info(email, silly_subdomain, ip_addr, local_ip, nat, web_port, sip_port, rtp_port, msrp_port)


def setup_data(data=None):
    # If a previous run left us a setup.json, jump straight to STEP 2 so the
    # user can confirm the saved settings without walking through every
    # question again. They can opt into the full STEP 1 flow by answering
    # "n" to the confirmation.
    #
    # Before offering the shortcut, probe the public IP. If it has drifted
    # since last run (very common on cloud instances that don't have an
    # Elastic/static IP) the shortcut is NOT offered: we fall through into
    # the full STEP 1 flow so that the warning in _ask_all_settings() fires
    # and every component is reconfigured with the new IP. Skipping this
    # check on the shortcut path would let the user unwittingly keep an
    # outdated IP baked into MediaProxy, Janus, OpenSIPS, etc.
    skip_shortcut = False
    if data is not None and data.ip:
        current_ip, _source, _errs = _detect_public_ip()
        if current_ip and current_ip != data.ip:
            output(
                f"Public IP changed since last install "
                f"(saved={data.ip}, detected={current_ip}). "
                "Walking through full setup to reconfigure."
            )
            # Skip the shortcut but KEEP `data` so every other saved value
            # (domain, email, ports, nat, etc.) is still used as the default
            # in the full STEP 1 flow. Only the IP prompt will show the new
            # detected value; the rest of the prompts pre-fill with the
            # values the user confirmed last time. The prominent IP-changed
            # warning is printed inside _ask_all_settings().
            skip_shortcut = True
        # else: IP unchanged (or detection failed) — safe to offer the shortcut.

    if data is not None and not skip_shortcut:
        shortcut = question(
            2,
            "Confirm Sylk Suite installation",
            f"\n{str(data)}\n\nContinue with these saved settings (no changes)?",
            default="Y",
        ).lower()
        if shortcut in ("", "y", "yes"):
            # Note: the settings file (logs/setup.json) lives inside DEST_DIR,
            # so save() is deferred until main() has run clone_repo(). No
            # write happens here.
            return data
        # Otherwise: fall through into the full STEP 1 flow, using the saved
        # values as defaults for each prompt.

    # STEP 1 + STEP 2 confirmation loop. Re-prompt on "no" by reusing the
    # just-entered values as defaults.
    while True:
        data = _ask_all_settings(data)

        correct = question(
            2,
            "Confirm Sylk Suite installation",
            f"\n{str(data)}\n\nContinue with the following settings?",
            default="Y",
        ).lower()

        if correct in ("", "y", "yes"):
            return data
        # Loop: re-ask STEP 1 with data as defaults


def _pkg_installed(pkg):
    """Return True if a Debian package is currently installed."""
    result = subprocess.run(
        ["dpkg-query", "-W", "-f=${Status}", pkg],
        capture_output=True, text=True
    )
    return result.returncode == 0 and "install ok installed" in result.stdout


def _docker_available():
    return check_command("docker")


def _docker_inspect(kind, name, fmt):
    """Run `docker <kind> inspect --format <fmt> <name>`; return stdout or None."""
    if kind == "container":
        cmd = ["docker", "inspect", "--format", fmt, name]
    elif kind == "image":
        cmd = ["docker", "image", "inspect", "--format", fmt, name]
    elif kind == "volume":
        cmd = ["docker", "volume", "inspect", "--format", fmt, name]
    elif kind == "network":
        cmd = ["docker", "network", "inspect", "--format", fmt, name]
    else:
        return None
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode == 0 and r.stdout.strip():
        return r.stdout.strip()
    return None


def show_installed():
    """Report Debian packages installed by this script and Docker resource locations."""
    def _print_pkg_row(pkg):
        if _pkg_installed(pkg):
            ver = subprocess.run(
                ["dpkg-query", "-W", "-f=${Version}", pkg],
                capture_output=True, text=True
            ).stdout.strip()
            print(f"    {pkg:<35} {GREEN}installed{RESET}  {ver}")
        else:
            print(f"    {pkg:<35} {YELLOW}not installed{RESET}")

    print("")
    print(f"{CYAN}Project Debian packages (purged by --purge-files){RESET}")
    print("-" * 80)
    for pkg in PROJECT_DEB_PACKAGES:
        _print_pkg_row(pkg)

    print("")
    print(f"{CYAN}System dependencies (NOT purged; repair with --reinstall-deps){RESET}")
    print("-" * 80)
    for pkg in SYSTEM_DEB_DEPENDENCIES:
        _print_pkg_row(pkg)

    print("")
    print(f"{CYAN}APT sources and keyrings added by this script{RESET}")
    print("-" * 80)
    for path in INSTALLED_APT_SOURCES:
        if os.path.exists(path):
            print(f"    {path:<55} {GREEN}present{RESET}")
        else:
            print(f"    {path:<55} {YELLOW}absent{RESET}")

    print("")
    print(f"{CYAN}Docker containers (from {DEST_DIR}/docker-compose.yml){RESET}")
    print("-" * 80)
    if not _docker_available():
        print(f"    {YELLOW}docker is not installed{RESET}")
    else:
        for c in DOCKER_CONTAINERS:
            state = _docker_inspect("container", c, "{{.State.Status}}")
            root = _docker_inspect("container", c, "{{.GraphDriver.Data.MergedDir}}") or ""
            if state is None:
                print(f"    {c:<20} {YELLOW}not present{RESET}")
            else:
                print(f"    {c:<20} state={state}")
                if root:
                    print(f"    {'':<20}   fs={root}")

        print("")
        print(f"{CYAN}Docker images{RESET}")
        print("-" * 80)
        for img in DOCKER_IMAGES:
            iid = _docker_inspect("image", img, "{{.Id}}")
            upper = _docker_inspect("image", img, "{{.GraphDriver.Data.UpperDir}}") or ""
            if iid is None:
                print(f"    {img:<30} {YELLOW}not present{RESET}")
            else:
                short = iid.split(":", 1)[-1][:12]
                print(f"    {img:<30} id={short}")
                if upper:
                    print(f"    {'':<30}   upper={upper}")

        print("")
        print(f"{CYAN}Docker volumes{RESET}")
        print("-" * 80)
        for vol in DOCKER_VOLUMES:
            found = False
            # docker-compose v1 prefixes volumes with the project dir basename
            for name in (vol, f"{DEST_DIR.name}_{vol}"):
                mp = _docker_inspect("volume", name, "{{.Mountpoint}}")
                if mp:
                    print(f"    {name:<30} mountpoint={mp}")
                    found = True
                    break
            if not found:
                print(f"    {vol:<30} {YELLOW}not present{RESET}")

        print("")
        print(f"{CYAN}Docker networks{RESET}")
        print("-" * 80)
        for net in DOCKER_NETWORKS:
            found = False
            for name in (net, f"{DEST_DIR.name}_{net}"):
                scope = _docker_inspect("network", name, "{{.Scope}}/{{.Driver}}")
                if scope:
                    print(f"    {name:<30} {scope}")
                    found = True
                    break
            if not found:
                print(f"    {net:<30} {YELLOW}not present{RESET}")

        root = subprocess.run(
            ["docker", "info", "--format", "{{.DockerRootDir}}"],
            capture_output=True, text=True
        ).stdout.strip()
        if root:
            print("")
            print(f"{CYAN}Docker root directory{RESET}: {root}")

    print("")
    print(f"{CYAN}Repository location{RESET}: {DEST_DIR}  "
          f"({'present' if DEST_DIR.exists() else 'absent'})")
    print("")


def purge_files():
    """Uninstall project Debian packages and remove APT sources added by this
    script.

    Only packages in PROJECT_DEB_PACKAGES are purged. The foundational
    system dependencies in SYSTEM_DEB_DEPENDENCIES (ca-certificates, curl,
    gnupg, apt-utils, git) are intentionally left alone because they are
    usually needed by other software on the host and removing them can
    break the system — notably ca-certificates, without which Python's
    HTTPS (and therefore this installer's public-IP detection) stops
    working. Likewise `apt-get autoremove` is NOT called: it pulls out
    orphaned dependencies, which is a blunt instrument that has already
    caused collateral damage in the field.
    """
    if os.geteuid() != 0:
        error("Please run this script with sudo or as root")
        sys.exit(1)

    installed = [p for p in PROJECT_DEB_PACKAGES if _pkg_installed(p)]
    if installed:
        make_step(f"Purging {len(installed)} project Debian package(s)")
        pkg_list = " ".join(installed)
        # Stop the services first so purge can remove their units cleanly.
        for svc in ("opensips", "mediaproxy-dispatcher", "mediaproxy-relay",
                    "msrprelay", "openxcap", "enrollment", "docker"):
            subprocess.run(["systemctl", "stop", svc],
                           capture_output=True, text=True)
        # Use subprocess.run so a non-zero exit (e.g. a package that vanished
        # mid-purge) does not abort the whole cleanup.
        subprocess.run(
            f"apt-get purge -y -qq {pkg_list}",
            shell=True, env=env
        )
        output("Project packages purged")
        output("System dependencies (ca-certificates, curl, gnupg, "
               "apt-utils, git) were left installed on purpose.")
        output("Run `apt-get autoremove` manually if you want to clean up "
               "orphaned transitive dependencies.")
    else:
        output("No tracked project Debian packages are currently installed")

    make_step("Removing APT sources and keyrings added by this script")
    removed_any = False
    for path in INSTALLED_APT_SOURCES:
        if os.path.exists(path):
            try:
                os.remove(path)
                output(f"Removed {path}")
                removed_any = True
            except OSError as e:
                error(f"Could not remove {path}: {e}")
        else:
            output(f"Not present: {path}")

    # Also clean up the nf_conntrack modprobe file the installer drops.
    nf_file = "/etc/modprobe.d/nf_conntrack.conf"
    if os.path.exists(nf_file):
        try:
            os.remove(nf_file)
            output(f"Removed {nf_file}")
            removed_any = True
        except OSError as e:
            error(f"Could not remove {nf_file}: {e}")

    # Persistent settings files. We deliberately leave anything under
    # DEST_DIR alone — the user is expected to remove /opt/sylk-suite/
    # themselves (it's the cloned repo checkout, not something the
    # installer should delete on their behalf). We only clean up the
    # legacy `.env` that may be sitting in the installer's current
    # working directory from an older run.
    dest_abs = os.path.realpath(str(DEST_DIR))

    def _under_destdir(path):
        try:
            return os.path.realpath(path).startswith(dest_abs + os.sep)
        except OSError:
            return False

    for path in (Info.DEFAULT_FILE, *Info.LEGACY_ENV_FILES):
        if not path:
            continue
        if _under_destdir(path):
            # Intentionally skipped; the user will delete DEST_DIR manually.
            continue
        if os.path.exists(path):
            try:
                os.remove(path)
                output(f"Removed {path}")
                removed_any = True
            except OSError as e:
                error(f"Could not remove {path}: {e}")

    if removed_any:
        subprocess.run("apt-get update -qq", shell=True, env=env)

    output("Debian package purge complete")


def purge_docker_files():
    """Remove Docker containers, images, volumes and networks created by this script."""
    if os.geteuid() != 0:
        error("Please run this script with sudo or as root")
        sys.exit(1)

    if not _docker_available():
        error("Docker is not installed; nothing to remove")
        return

    compose_file = DEST_DIR / "docker-compose.yml"
    if compose_file.exists():
        make_step("Tearing down docker-compose stack")
        # -v removes named volumes, --rmi all removes both built and pulled
        # images referenced by the compose file, --remove-orphans cleans up
        # containers that used to be in the compose file.
        subprocess.run(
            compose("down -v --rmi all --remove-orphans"),
            shell=True, cwd=str(DEST_DIR), env=env
        )
    else:
        output(f"No docker-compose.yml at {DEST_DIR}; cleaning resources by name")

    make_step("Removing any leftover containers")
    for c in DOCKER_CONTAINERS:
        if _docker_inspect("container", c, "{{.Id}}"):
            subprocess.run(f"docker rm -f {c}", shell=True, env=env)
            output(f"Removed container {c}")

    make_step("Removing any leftover images")
    for img in DOCKER_IMAGES:
        if _docker_inspect("image", img, "{{.Id}}"):
            subprocess.run(f"docker image rm -f {img}", shell=True, env=env)
            output(f"Removed image {img}")

    make_step("Removing any leftover named volumes")
    for vol in DOCKER_VOLUMES:
        for name in (vol, f"{DEST_DIR.name}_{vol}"):
            if _docker_inspect("volume", name, "{{.Name}}"):
                subprocess.run(f"docker volume rm -f {name}", shell=True, env=env)
                output(f"Removed volume {name}")

    make_step("Removing any leftover networks")
    for net in DOCKER_NETWORKS:
        for name in (net, f"{DEST_DIR.name}_{net}"):
            if _docker_inspect("network", name, "{{.Id}}"):
                subprocess.run(f"docker network rm {name}", shell=True, env=env)
                output(f"Removed network {name}")

    output("Docker images, containers, volumes and networks removed")


def reinstall_deps():
    """Reinstall the SYSTEM_DEB_DEPENDENCIES and refresh the CA bundle.

    Older versions of --purge-files removed foundational packages like
    ca-certificates, which in turn broke Python HTTPS (public-IP detection,
    apt's own downloads via HTTPS, curl, etc.). Use this flag to recover.
    It does an `apt-get install --reinstall` of the exact system packages
    the installer originally pulls in, then runs update-ca-certificates.
    """
    if os.geteuid() != 0:
        error("Please run this script with sudo or as root")
        sys.exit(1)

    make_step(f"Reinstalling {len(SYSTEM_DEB_DEPENDENCIES)} system dependencies")
    pkg_list = " ".join(SYSTEM_DEB_DEPENDENCIES)
    # apt-get update can use HTTP mirrors so should work even if the CA
    # bundle is broken; the reinstall then restores it.
    subprocess.run("apt-get update -qq", shell=True, env=env)
    rc = subprocess.run(
        f"apt-get install -y --reinstall -qq {pkg_list}",
        shell=True, env=env
    ).returncode
    if rc != 0:
        error(f"apt-get install --reinstall exited with {rc}; "
              "you may need to fix your APT sources first.")
        sys.exit(rc)
    # Rebuild the system CA store explicitly in case ca-certificates was
    # only partially restored (the postinst normally does this, but being
    # belt-and-braces here costs nothing).
    subprocess.run("update-ca-certificates --fresh", shell=True, env=env)
    output("System dependencies reinstalled and CA bundle refreshed")


def main(components, exclude_components, force_mysql=False, skip_git=False):
    if os.geteuid() != 0:
        error("Please run this script with sudo or as root")
        sys.exit(1)

    # Install apt-utils on its own FIRST, before any other package. If
    # apt-utils is installed in the same apt-get transaction as other
    # packages, debconf prints this warning during their configuration:
    #   "debconf: delaying package configuration, since apt-utils is not installed"
    # Splitting it out (and setting DEBIAN_FRONTEND=noninteractive so
    # debconf doesn't try to ask questions either) keeps the install quiet.
    os.system('DEBIAN_FRONTEND=noninteractive apt-get install -qq -y apt-utils > /dev/null 2>&1')
    os.system('DEBIAN_FRONTEND=noninteractive apt-get install -qq -y python3-psutil > /dev/null')
    # /etc/modprobe.d/nf_conntrack.conf is written (and the running module
    # hot-reloaded if necessary) by ensure_conntrack_hooks_enabled(), which
    # runs as part of install_mediaproxy(). Doing it there — rather than
    # here at the very top — avoids disrupting docker for installer runs
    # that don't include the mediaproxy component.

    print("""
    ███████ ██    ██ ██      ██   ██     ███████ ██    ██ ██ ████████ ███████ 
    ██       ██  ██  ██      ██  ██      ██      ██    ██ ██    ██    ██      
    ███████   ████   ██      █████       ███████ ██    ██ ██    ██    █████   
         ██    ██    ██      ██  ██           ██ ██    ██ ██    ██    ██      
    ███████    ██    ███████ ██   ██     ███████  ██████  ██    ██    ███████ 
    """)
    # print(f'{"-" * 80}')

    # choice = input("> ").strip().lower()
    # choice = question(1, "Confirmation", "Do you want to continue with the installation of Sylk Suite?", default="Y").lower()
    # if choice not in ("", "y", "yes"):
    #     output("Installation cancelled.")
    #     exit(0)
    data = Info.load()
    data = setup_data(data)

    install_components = {}

    if not components:
        for comp in ALLOWED_COMPONENTS:
            install_components[comp] = True
    else:
        for comp in ALLOWED_COMPONENTS:
            install_components[comp] = False

        for comp in components:
            install_components[comp] = True

    if exclude_components:
        for comp in exclude_components:
            install_components[comp] = False

    if not skip_git:
        make_step("Clone repository")
        clone_repo()

    # Persist the gathered settings once DEST_DIR (and therefore the logs/
    # directory) can safely exist. This replaces the old `.env` file.
    try:
        data.save()
        output(f"Settings saved to {data.DEFAULT_FILE}")
    except OSError as e:
        error(f"Could not write settings file: {e}")

    if install_components['dns']:
        make_step("Create DNS zone")
        create_domain(data)

    if install_components['docker']:
        make_step("Install Docker")
        install_docker()

    if install_components['sylkserver']:
        make_step("Install SylkSuite")
        start_sylk_suite(data)

    if install_components['opensips']:
        make_step("Install OpenSIPS")
        install_opensips(data, install_components['mysql'], force_mysql=force_mysql)

    if install_components['mediaproxy']:
        make_step("Install MediaProxy")
        install_mediaproxy(data)

    if install_components['msrprelay']:
        make_step("Install MSRPRelay")
        install_msrprelay(data)

    if install_components['openxcap']:
        make_step("Install OpenXCAP")
        install_openxcap(data)

    if install_components['enrollment']:
        make_step("Install Enrollment")
        install_enrollment(data)

    run("chmod +x scripts/* > /dev/null", cwd=DEST_DIR, silent=True)

    run("apt-get install -y -qq qrencode > /dev/null", silent=True)
    try:
        result = dns_template.replace("IPADDR", data.ip).replace("DOMAINNAME", data.full_domain)
        weburl = data.full_domain
        if data.web_port != "443":
            weburl = weburl + ":" + data.web_port
        result = result.replace("WEBURL", weburl)
        result = result.replace("MSRPPORT", data.msrp_port)
        result = result.replace("SIPTCPPORT", data.sip_port)
        sip_tls_port = int(data.sip_port) + 1
        result = result.replace("SIPTLSPORT", str(sip_tls_port))
        dns_email = data.email.replace("@", ".")
        result = result.replace("support@ag-projects.com", dns_email)

        MAINLOG_DIR = os.path.join(DEST_DIR, "logs")
        zone_file = os.path.join(MAINLOG_DIR, f"{data.full_domain}.zone")

        with open(zone_file, 'w') as result_file:
            result_file.write(f"{result}\n")

        make_step("DNS zone")
        output(f"Zone content: {zone_file}")
    except Exception as e:
        print(f"Error writing DNS zones file: {e}")

    # save DNS push
    make_step("Mobile app enrollment")
    os.system(f"qrencode -t ansiutf8 {data.full_domain}")

    output("Backup /opt/sylk-suite/logs folder, it contains your setup and Managed DNS credentials")


def parse_components(value):
    components = [c.strip() for c in value.split(",") if c.strip()]

    if not components:
        raise argparse.ArgumentTypeError("At least one component must be specified.")

    invalid = [c for c in components if c not in ALLOWED_COMPONENTS]
    if invalid:
        raise argparse.ArgumentTypeError(
            f"Invalid component(s): {', '.join(invalid)}. "
            f"Allowed values are: {', '.join(ALLOWED_COMPONENTS)}"
        )

    return components


if __name__ == "__main__":

    parser = argparse.ArgumentParser(description="Sylk Suite installer")
    parser.add_argument(
        "--include",
        type=parse_components,
        help=f"Comma-separated list of included components: {', '.join(sorted(ALLOWED_COMPONENTS))}"
    )
    parser.add_argument(
        "--exclude",
        type=parse_components,
        help=f"Comma-separated list of excluded components: {', '.join(sorted(ALLOWED_COMPONENTS))}"
    )
    parser.add_argument(
        "--force-mysql",
        action="store_true",
        default=False,
        help="Recreate the OpenSIPS MySQL database even if it already exists"
    )
    parser.add_argument(
        "--skip-git",
        action="store_true",
        default=False,
        help="Skip cloning or pulling the repository (use existing local copy)"
    )
    parser.add_argument(
        "--show-installed",
        action="store_true",
        default=False,
        help="Show the Debian packages installed by this script and the "
             "locations of the Docker images, containers and volumes it created"
    )
    parser.add_argument(
        "--purge-files",
        action="store_true",
        default=False,
        help="Uninstall the project Debian packages and APT sources added "
             "by this script. Does not touch ca-certificates/curl/gnupg/"
             "apt-utils/git (they are shared system dependencies) and does "
             "not run apt-get autoremove. Does not touch Docker."
    )
    parser.add_argument(
        "--purge-docker-files",
        action="store_true",
        default=False,
        help="Remove the Docker containers, images, volumes and networks "
             "created by this script (docker-compose down -v --rmi all)."
    )
    parser.add_argument(
        "--reinstall-deps",
        action="store_true",
        default=False,
        help="Reinstall the system dependencies the installer relies on "
             "(ca-certificates, curl, gnupg, apt-utils, git) and refresh "
             "the CA bundle. Use this to recover if an older version of "
             "--purge-files removed these packages."
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        default=False,
        help="Stream every shell command's output live. Overrides the "
             "per-call silent=True that the installer uses by default, "
             "so you can see exactly what apt-get, docker, git etc. are "
             "doing. Useful for debugging installation failures."
    )

    try:
        args = parser.parse_args()

        # Promote --verbose into the module-level flag that run() reads.
        # Doing this as early as possible means every subsequent run()
        # call — including those inside the maintenance actions below —
        # will stream output when the user asked for verbosity.
        if args.verbose:
            VERBOSE = True

        # Maintenance actions run standalone and then exit; they do not
        # trigger the interactive installer.
        if args.show_installed:
            show_installed()
            sys.exit(0)

        if args.reinstall_deps:
            reinstall_deps()
            sys.exit(0)

        if args.purge_docker_files or args.purge_files:
            # Tear down Docker first so package purge does not leave orphan
            # containers bound to docker.io.
            if args.purge_docker_files:
                purge_docker_files()
            if args.purge_files:
                purge_files()
            sys.exit(0)

        main(args.include, args.exclude, force_mysql=args.force_mysql, skip_git=args.skip_git)

    except KeyboardInterrupt:
        print()
        sys.exit(0)

