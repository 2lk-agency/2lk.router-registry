# 2lk.router-registry

Community-maintained, daily-updated **provider catalog and reference pricing registry** for [2lk.router](https://github.com/2lk-agency/2lk.router).

## Design Principles

- **Zero Telemetry / Zero Tracking:** Served strictly as a static YAML file from GitHub (`raw.githubusercontent.com`). `2lk.router` **never** calls this repository automatically at startup or in the background — users trigger synchronization strictly **on demand** (`-sync-registry` CLI flag or the **Sync Registry** button in the Web UI).
- **Zero Lock-In:** Fork this repository, customize `providers.base.yaml` / secrets, or point `server.registry_sync_url` to any URL you control (or sync pricing directly from OpenRouter).
- **Keep-Last-Good Guardrails:** Daily updates in GitHub Actions validate schema integrity, retain last-known model lists if an upstream `/models` query fails transiently, and scan outputs for accidental secret patterns before committing.

## Files

- `registry.yaml` — Generated catalog (free-tier + BYOK paid providers) and reference USD-per-1M-token pricing.
- `providers.base.yaml` — Base provider metadata (`id`, `type`, `tier`, `base_url`, `env_var`, `strip_fields`, token/batch limits, and fallback models).
- `scripts/update_registry.py` — Deterministic generator reading OpenRouter public pricing + optional provider `/models` endpoints.
- `.github/workflows/daily-update.yml` — Scheduled daily update (`03:00 UTC`) committing only when `registry.yaml` content actually changes.

## Forking With Your Own Keys (Optional)

By default, `scripts/update_registry.py` fetches live model pricing and `:free` models from OpenRouter without any API key. To also refresh live model lists for providers whose `/models` endpoints require authentication, configure optional repository Actions Secrets on your fork:

- `GROQ_API_KEY`
- `GEMINI_FREE_API_KEY`
- `COHERE_API_KEY`
- `GITHUB_MODELS_KEY`
- `CLOUDFLARE_API_KEY` + `CLOUDFLARE_ACCOUNT_ID` *(only used to query `/models/search`; the account ID is never written into `registry.yaml`)*
