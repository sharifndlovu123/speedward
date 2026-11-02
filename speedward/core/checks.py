"""The opt-in "More checks": names, what each costs and what it asks. Constants only, no imports.

Every check runs from Globalping's probes (reusing the main scan's probes by measurement id), never from
this server. Field facts are in docs/globalping-api-notes.md ("Reachability checks", "Speed checks").
"""
from __future__ import annotations

# The reachability group (Task 20). The default scan stays at 3 tests per place; each ticked check adds
# PER_PLACE tests per place.
IPV6, PLAIN_HTTP, RESOLVERS_CHECK, PORT = REACH_CHECKS = ("ipv6", "http", "dns", "port")

# Well-known public resolvers asked by the "dns" check, one test each per place.
RESOLVERS = (("1.1.1.1", "Cloudflare"), ("8.8.8.8", "Google"))
RESOLVER_IPS = tuple(ip for ip, _ in RESOLVERS)

# The speed group (Task 21): "Is it fast?". Each feeds a block of the "where the time goes" diagram.
CONSISTENCY, HTTP2, COMPRESSION, DNS_TRACE, MTR = SPEED_CHECKS = (
    "consistency", "http2", "compression", "dnstrace", "mtr")
ALL_CHECKS = REACH_CHECKS + SPEED_CHECKS

CONSISTENCY_EXTRA = 4  # extra http runs per place; with the main test's repeat that makes 5
MTR_PACKETS = 5  # packets per hop for the path test (the API allows 1 to 16)
PATH_PACKETS = 3  # the "Network path" option sends no count, so Globalping's default (3) applies

PER_PLACE = {IPV6: 1, PLAIN_HTTP: 1, RESOLVERS_CHECK: len(RESOLVERS), PORT: 1,
             CONSISTENCY: CONSISTENCY_EXTRA, HTTP2: 1, COMPRESSION: 1, DNS_TRACE: 1, MTR: 1}
PORT_PACKETS = 3  # TCP connection attempts per place (the API allows 1 to 16)

# Checks that cannot run on some addresses: skipped (shown as "did not run") and never charged.
SKIPPED_ON_IP = (IPV6, RESOLVERS_CHECK, DNS_TRACE)  # no name to look up or to ask for over IPv6
SKIPPED_ON_PLAIN_HTTP = (HTTP2,)  # HTTP/2 is tried over HTTPS only

LABEL = {
    IPV6: "Over IPv4 and IPv6",
    PLAIN_HTTP: "Over HTTP and HTTPS",
    RESOLVERS_CHECK: "Through different DNS resolvers",
    PORT: "Port reachable (TCP 443)",
    CONSISTENCY: "Repeat 5 times (consistency)",
    HTTP2: "HTTP/2 against HTTP/1.1",
    COMPRESSION: "Compression (gzip, brotli)",
    DNS_TRACE: "DNS delegation trace",
    MTR: "Path loss and jitter (mtr)",
}
QUESTION = {
    IPV6: "does it fail over IPv6 only in some places?",
    PLAIN_HTTP: "is the plain address redirected or dead?",
    RESOLVERS_CHECK: "does the name resolve everywhere, not just on one resolver?",
    PORT: "is the door open, separately from the web server?",
    # The approved mockup gives these two a note; the others show only their cost.
    COMPRESSION: "size on the wire is not available",
    DNS_TRACE: "where a slow lookup spends its time",
    MTR: "shares one test with Network path below when both are ticked",
}


def parse_checks(text: str | None) -> tuple[str, ...]:
    """A comma-separated list of check names, in ALL_CHECKS order, each once. ValueError (fixed
    wording, nothing posted is repeated) on anything unknown."""
    names = [n.strip() for n in (text or "").split(",") if n.strip()]
    if any(n not in ALL_CHECKS for n in names):
        raise ValueError("Unknown check. Untick the extra checks and try again.")
    return tuple(c for c in ALL_CHECKS if c in names)


def cost_per_place(checks) -> int:
    return sum(PER_PLACE[c] for c in checks)
