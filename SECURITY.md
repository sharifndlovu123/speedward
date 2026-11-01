# Security policy

Please report vulnerabilities in speedward privately to the maintainers at security@example.com (replace before publishing) rather than opening a public issue. Include steps to reproduce. We aim to acknowledge reports within a few days.

Areas of particular interest: anything that sends the Globalping key (`GLOBALPING_TOKEN`) to the browser, an export or a log; anything that makes the server store scan targets or results; injection into generated reports or the web page; and any way to point the Globalping key at a different endpoint.

The web app has no login. Run it on loopback or behind your own authentication (see README.md).
