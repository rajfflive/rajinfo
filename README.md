# @rajfflive API

Telegram userbot API with multi‑account support, caching, daily limits, expiry, and admin panel.

## Features
- 19 commands routed to two groups
- Round‑robin account rotation
- Auto‑delete command and bot replies
- Cache (24h) – instant 0.01s response on repeated queries
- Admin panel: manage accounts, API keys, view logs
- Permanent unlimited key for admin

## Deploy
1. Set environment variable `ADMIN_PASSWORD`.
2. Deploy on Railway / Render.
3. Add accounts via admin panel using `generate_session.py`.

## Environment Variables
- `ADMIN_PASSWORD` (required)
- `CACHE_EXPIRE_SECONDS` (optional, default 86400)
