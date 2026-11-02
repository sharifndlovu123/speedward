"""The opt-in "More checks": names, what each costs and what it asks. Constants only, no imports.

Every check runs from Globalping's probes (reusing the main scan's probes by measurement id), never from
this server. Field facts are in docs/globalping-api-notes.md ("Reachability checks").
"""
from __future__ import annotations

# The reachability group (Task 20). The default scan stays at 3 tests per place; each ticked check adds
# PER_PLACE tests per place.
IPV6, PLAIN_HTTP, RESOLVERS_CHECK, PORT = REACH_CHECKS = ("ipv6", "http", "dns", "port")

# Well-known public resolvers asked by the "dns" check, one test each per place.
RESOLVERS = (("1.1.1.1", "Cloudflare"), ("8.8.8.8", "Google"))
RESOLVER_IPS = tuple(ip for ip, _ in RESOLVERS)

PER_PLACE = {IPV6: 1, PLAIN_HTTP: 1, RESOLVERS_CHECK: len(RESOLVERS), PORT: 1}
PORT_PACKETS = 3  # TCP connection attempts per place (the API allows 1 to 16)

LABEL = {
    IPV6: "Over IPv4 and IPv6",
    PLAIN_HTTP: "Over HTTP and HTTPS",
    RESOLVERS_CHECK: "Through different DNS resolvers",
    PORT: "Port reachable (TCP 443)",
}
QUESTION = {
    IPV6: "does it fail over IPv6 only in some places?",
    PLAIN_HTTP: "is the plain address redirected or dead?",
    RESOLVERS_CHECK: "does the name resolve everywhere, not just on one resolver?",
    PORT: "is the door open, separately from the web server?",
}


def parse_checks(text: str | None) -> tuple[str, ...]:
    """A comma-separated list of check names, in REACH_CHECKS order, each once. ValueError (fixed
    wording, nothing posted is repeated) on anything unknown."""
    names = [n.strip() for n in (text or "").split(",") if n.strip()]
    if any(n not in REACH_CHECKS for n in names):
        raise ValueError("Unknown check. Untick the extra checks and try again.")
    return tuple(c for c in REACH_CHECKS if c in names)


def cost_per_place(checks) -> int:
    return sum(PER_PLACE[c] for c in checks)
