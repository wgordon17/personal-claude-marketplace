import ipaddress
import re
from urllib.parse import urlparse

_EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$")
_USERNAME_RE = re.compile(r"^[a-zA-Z0-9_]{3,32}$")

_PRIVATE_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
]


def validate_email(email: str) -> bool:
    return bool(email) and len(email) <= 254 and bool(_EMAIL_RE.match(email))


def validate_username(username: str) -> bool:
    return bool(username) and bool(_USERNAME_RE.match(username))


def validate_password(password: str) -> bool:
    return bool(password) and len(password) >= 12


def validate_project_name(name: str) -> bool:
    return bool(name) and 1 <= len(name.strip()) <= 100


def validate_description(description: str, max_length: int = 1000) -> bool:
    return len(description) <= max_length


def validate_ticket_title(title: str) -> bool:
    return bool(title) and 1 <= len(title.strip()) <= 200


def _is_private_ip(host: str) -> bool:
    try:
        addr = ipaddress.ip_address(host)
        return addr.is_private or addr.is_loopback or addr.is_link_local
    except ValueError:
        return False


def validate_webhook_url(url: str) -> bool:
    if not url or len(url) > 2048:
        return False

    try:
        parsed = urlparse(url)
    except Exception:
        return False

    if parsed.scheme not in ("https", "http"):
        return False

    hostname = parsed.hostname
    if not hostname:
        return False

    # SSRF guard: reject URLs resolving to private/loopback addresses
    if _is_private_ip(hostname):
        return False

    # Reject reserved hostnames
    return hostname not in ("localhost", "metadata.google.internal", "169.254.169.254")
