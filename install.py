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

EMAIL_REGEX = re.compile(r"^[^@]+@[^@]+\.[^@]+$")

REPO_URL = "https://github.com/AGProjects/sylk-suite.git"
DEST_DIR = Path("/opt/sylk-suite")
DOMAIN = "sylk.link"
ENROLLMENT_URL = "https://enrollment.sipthor.net/enrollment-sylk-domain.phtml"
PUSH_URL = "http://ca-sip-01.sipthor.net:8400/push"

RESULT_PREFIX = "dns-data"

ALLOWED_COMPONENTS = {"opensips", "sylkserver", "mediaproxy", "openxcap", "msrprelay", "dns", "enrollment", "docker", "mysql"}

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
env = os.environ.copy()
env['DEBIAN_FRONTEND'] = "noninteractive"


class Info():
    DEFAULT_FILE='.env'

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
        with open(filename, "w", encoding="utf-8") as f:
            f.write(f"IP={self.ip}\n")
            f.write(f"FULL_DOMAIN={self.zone}.{DOMAIN}\n")
            f.write(f"EMAIL={self.email}\n")
            f.write(f"NAT={str(self.nat)}\n")
            f.write(f"LOCAL_IP={self.local_ip}\n")
            f.write(f"WEB_PORT={self.web_port}\n")
            f.write(f"SIP_PORT={self.sip_port}\n")
            f.write(f"RTP_PORT={self.rtp_port}\n")
            f.write(f"MSRP_PORT={self.msrp_port}\n")

    @classmethod
    def load(cls, filename=None):
        filename = filename or cls.DEFAULT_FILE

        if not os.path.exists(filename):
            return None

        env_data = {}
        with open(filename, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    key, value = line.split("=", 1)
                    env_data[key] = value

        if env_data.get("FULL_DOMAIN") == 'sylk.link' and env_data.get("IP") == '0.0.0.0':
            return None

        if env_data.get("FULL_DOMAIN", None) is None:
            return None

        ip = env_data.get("IP")
        full_domain = env_data.get("FULL_DOMAIN")
        email = env_data.get("EMAIL")

        if full_domain == '':
            return None

        zone = full_domain.split(".")[0] if full_domain else ""
        nat = env_data.get("NAT")
        local_ip = env_data.get("LOCAL_IP")
        web_port = env_data.get("WEB_PORT")
        sip_port = env_data.get("SIP_PORT")
        rtp_port = env_data.get("RTP_PORT")
        msrp_port = env_data.get("MSRP_PORT")

        return cls(email=email, zone=zone, ip_addr=None, local_ip=local_ip, nat=nat, web_port=web_port, sip_port=sip_port, rtp_port=rtp_port, msrp_port=msrp_port)

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


def run(cmd, cwd=None, silent=False, echo=True):
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

    if process.returncode != 0:
        if silent or not echo:
            print(f"{BLUE}>>> {cmd}{RESET}")
        print(f"{RED}Command failed!{RESET}")
        sys.exit(process.returncode)
    return ''.join(output_lines)


def check_command(cmd):
    result = subprocess.run(
        ["which", cmd],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )
    return result.returncode == 0


def is_valid_email(email):
    return bool(EMAIL_REGEX.match(email))


def is_valid_subdomain(name):
    if len(name) < 3 or len(name) > 63:
        return False
    pattern = r"^(?!-)[a-z0-9-]{1,63}(?<!-)$"
    return bool(re.match(pattern, name))


def get_ips():
    results = []
    import psutil

    for interface, addrs in psutil.net_if_addrs().items():
        for addr in addrs:
            if addr.family == socket.AF_INET:
                ip = addr.address

                ip_obj = ipaddress.ip_address(ip)

                if ip_obj.is_loopback:
                    continue
                results.append({
                    "interface": interface,
                    "ip": ip,
                    "type": "private" if ip_obj.is_private else "public"
                })

    return results

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
    if not check_command("git"):
        run("apt install -y -qq git", silent=True)
    if DEST_DIR.exists():
        output(f"Repo already exists at {DEST_DIR}, pulling latest changes...")
        run("git pull", cwd=DEST_DIR, silent=True)
    else:
        output(f"Cloning repo into {DEST_DIR}...")
        run(f"git clone {REPO_URL} {DEST_DIR}", silent=True)


def start_sylk_suite(data):
    run("cp -r ./sylkserver/config-templates ./sylkserver/config", cwd=DEST_DIR, silent=True)
    run("cp -r ./janus/config-templates ./janus/config", cwd=DEST_DIR, silent=True)
    run("docker-compose up -d", cwd=DEST_DIR, silent=True)
    sleep(1)
    run("chmod +x certbot/hooks/auth.py certbot/hooks/cleanup.py", cwd=DEST_DIR, silent=True)
    # Remove stale certbot lock file left behind by interrupted runs
    run('docker-compose run -T --rm --entrypoint "" certbot rm -f /etc/letsencrypt/.certbot.lock', cwd=DEST_DIR, silent=True)
    run(
        f"""
        docker-compose run -T --rm --entrypoint "" certbot certbot certonly \
        --manual \
        --preferred-challenges dns \
        --manual-auth-hook /hooks/auth.py \
        --manual-cleanup-hook /hooks/cleanup.py \
        -d {data.full_domain} \
        -d xcap.{data.full_domain} \
        --email {data.email} \
        --agree-tos \
        --no-eff-email
        """,
        cwd=DEST_DIR,
        echo=False
    )
    run("cp ./webrtc-nginx/domain.conf ./webrtc-nginx/conf/", cwd=DEST_DIR, silent=True)
    run(f"sed -i 's/FULLDOMAIN/{data.full_domain}/g' ./webrtc-nginx/conf/domain.conf", cwd=DEST_DIR, silent=True)
    run("docker exec sylk-webrtc nginx -s reload", silent=True)
    output("NGINX Web server installed")
    run("mkdir -p ./webrtc-nginx/html", cwd=DEST_DIR, silent=True)
    regenerate = False
    try:
        with open(f"{DEST_DIR}/webrtc-nginx/html/sylk-config.json") as f:
            cfg = f.read()
            cfg_parsed = json.loads(cfg)
            if cfg_parsed['publicUrl'] != f'https://{data.full_domain}:{data.web_port}':
                regenerate = True
    except (FileNotFoundError, json.JSONDecodeError):
        pass

    if regenerate:
        run("docker-compose build --no-cache webrtc", cwd=dest_dir, silent=true)

    run("docker cp sylk-webrtc:/usr/share/nginx/html/. ./webrtc-nginx/html/", cwd=dest_dir, silent=true)
    # update_sylk_config(data)
    output("Sylk Server abd Janus server installed")


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


def install_mediaproxy(data):
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


def setup_data(data=None):
    # print("Installing Sylk Suite...")
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

    with request.urlopen("https://api.ipify.org?format=text") as response:
        public_ip = response.read().decode()

    ip_addr = question(1, "", "Public server IP address", default=data.ip if data and data.ip else public_ip).lower()

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
        ips = get_ips()
        try:
            private_ips = list(reversed([i for i in ips if i["type"] == "private"]))
            private_ip = private_ips[0]['ip']
        except KeyError:
            private_ip = "127.0.0.1"
        local_ip = question(1, "", "Private server IP address", default=data.local_ip if data else private_ip).lower()

    data = Info(email, silly_subdomain, ip_addr, local_ip, nat, web_port, sip_port, rtp_port, msrp_port)

    correct = question(
        2,
        "Confirm Sylk Suite installation",
        f"\n{str(data)}\n\nContinue with the following settings?",
        default="Y"
    ).lower()

    if correct not in ("", "y", "yes"):
        setup_data(data)
    data.save()

    return data


def main(components, exclude_components, force_mysql=False, skip_git=False):
    if os.geteuid() != 0:
        error("Please run this script with sudo or as root")
        sys.exit(1)

    os.system('apt-get install -qq -y python3-psutil apt-utils joe ngrep tcpdump> /dev/null')
    os.system('echo "options nf_conntrack enable_hooks=1" | sudo tee /etc/modprobe.d/nf_conntrack.conf > /dev/null')

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

    if install_components['dns']:
        make_step("Create DNS zone")
        create_domain(data)

    if install_components['docker']:
        make_step("Install Docker")
        install_docker()

    if install_components['sylkserver']:
        make_step("Get certificate")
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

    try:
        args = parser.parse_args()
        main(args.include, args.exclude, force_mysql=args.force_mysql, skip_git=args.skip_git)

    except KeyboardInterrupt:
        print()
        sys.exit(0)

