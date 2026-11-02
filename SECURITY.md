# Security policy

Please report vulnerabilities privately through GitHub, not in a public issue:

1. Open the repository's **Security** tab and choose **Report a vulnerability**, or go straight to <https://github.com/sharifndlovu123/speedward/security/advisories/new>.
2. Describe the problem and the steps to reproduce it. Leave out real Globalping keys, MaxMind licence keys and anything about sites you do not own.

Speedward is maintained by one person in their spare time, so there is no guaranteed response time. The aim is to acknowledge a report within about a week and to keep you updated in the advisory while a fix is worked on. Please give reasonable time for a fix before disclosing publicly.

Areas of particular interest: anything that sends the Globalping key (`GLOBALPING_TOKEN`) to the browser, an export or a log; anything that makes the server store scan targets or results; injection into generated reports or the web page; and any way to point the Globalping key at a different endpoint.

The web app has no login. Run it on loopback or behind your own authentication (see README.md).
