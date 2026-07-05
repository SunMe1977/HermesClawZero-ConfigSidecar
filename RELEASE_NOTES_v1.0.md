# Release Notes v1.0.0

## Highlights
- Hardened security defaults and removed embedded DB secret from image build.
- Improved deployment reliability with service health checks and restart policies.
- Improved setup portability (Windows/Linux/macOS) and updater git autodetection.
- Improved README first impression with clearer quick start, architecture visuals, FAQ, and troubleshooting.

## Operations
- New endpoint: `GET /healthz`
- Compose now validates required env vars:
  - `DB_PASSWORD`
  - `API_KEY`

## Upgrade Notes
- Pull latest code and rebuild containers:
  - `docker compose up -d --build`
- Ensure `.env` contains `DB_PASSWORD` and `API_KEY`.
- For external DB clients (DBeaver), use host `localhost` and port `5666`.

## No Breaking Changes
This release is focused on reliability and professionalism improvements without changing core user workflows.
