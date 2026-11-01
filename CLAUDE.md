# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Current state

This repo has no code yet. It holds only the approved design, `docs/superpowers/specs/2026-11-01-speedward-design.md`, and a build plan (once written) under `docs/superpowers/plans/`. Read the design before doing anything. Build, lint and test commands do not exist yet; add them here when the first code lands.

## What this is

`speedward` is a working name for a self-hostable website performance tool, the sibling of **siteward** (a passive security scanner at `/home/sharif/Documents/vs_code/work/devbridge/web_test`, repo `sharifndlovu123/siteward`). It tests a site from locations around the world through the Globalping API and shows a world map. Match siteward's stack and conventions: Python 3.11+, Typer CLI, Django web UI, httpx, Jinja2 reports, whitenoise, gunicorn, pytest, a fake-server test helper. Read siteward's code before planning or writing anything.

## Rules that are easy to get wrong

- **Never show a fake result for a failed test.** Show "no data" with a reason, and leave failed tests out of averages and pass/fail limits. "Blocked" means the site itself refused or timed out; a Globalping-side problem is "test failed" or "test did not run".
- **Nothing about scanned sites is stored on the server.** Results live only in the browser's session storage (15-minute default limit, an env setting), and exports are the way to keep them. Logs never contain targets or results.
- **The Globalping key (`GLOBALPING_TOKEN`) is server-side only.** The web UI has no input for it, and it never reaches the browser, exports or logs. Describe it as "free up to a limit" (250 tests an hour with no account, 500 with a free key; credits beyond that), never as simply "free".
- **Show an estimated test cost before every scan**, and refuse a scan that will not fit in the remaining allowance unless the person confirms.
- **Unverified Globalping details** (HTTP result fields, HTTP/TLS versions, reused probes, rate-limit response) must be confirmed against the real API in the first plan task. If a field is missing, the feature shows "not available", never a guess.
- **No outside lookups that leak the scanned target** (map tiles, geo-IP services). Use self-hosted or offline data.

## Working with the author

For any design change or new idea, show a mockup (the visual companion browser tab) before deciding. Mockups from the design session are in `.superpowers/brainstorm/`, which is git-ignored.
