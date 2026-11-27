#!/usr/bin/env python3
"""Generate deterministic registry.yaml from OpenRouter pricing and optional provider /models APIs."""

from __future__ import annotations

import copy
import datetime as dt
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
BASE_FILE = ROOT / "providers.base.yaml"
OUT_FILE = ROOT / "registry.yaml"

OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
TIMEOUT_SECONDS = 15
MIN_PRICED_MODELS = 50
MAX_OPENROUTER_FREE_MODELS = 40

NOISE_SUBSTRINGS = (
    "guard",
    "safeguard",
    "moderation",
    "tts",
    "playai",
    "orpheus",
    "imagen",
    "veo",
    "lyria",
    "robotics",
    "native-audio",
    "live-",
    "realtime",
)

SECRET_RE = re.compile(
    r"(gsk_[A-Za-z0-9]{12,}|sk-or-v1-[A-Za-z0-9]{12,}|sk-ant-[A-Za-z0-9\-_]{12,}|ghp_[A-Za-z0-9]{12,}|github_pat_[A-Za-z0-9_]{12,}|AIza[0-9A-Za-z\-_]{16,}|accounts/[0-9a-fA-F]{16,})"
)


def http_get_json(url: str, headers: dict[str, str] | None = None) -> Any:
    req_headers = {"User-Agent": "2lk-router-registry/1.0", "Accept": "application/json"}
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, headers=req_headers)
    with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
        if resp.status != 200:
            raise RuntimeError(f"HTTP {resp.status} from {url}")
        raw = resp.read(16 * 1024 * 1024)
    return json.loads(raw.decode("utf-8"))


def is_noise_model(model_id: str) -> bool:
    low = model_id.lower()
    return any(tok in low for tok in NOISE_SUBSTRINGS)


def extract_model_ids(payload: Any, strip_prefix: str = "") -> list[str]:
    items: list[Any] = []
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict):
        for key in ("data", "result", "models"):
            if isinstance(payload.get(key), list):
                items = payload[key]
                break

    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        if isinstance(item, str):
            mid = item.strip()
        elif isinstance(item, dict):
            mid = str(item.get("id") or item.get("name") or "").strip()
        else:
            continue
        if strip_prefix and mid.startswith(strip_prefix):
            mid = mid[len(strip_prefix) :]
        if not mid or is_noise_model(mid) or mid in seen:
            continue
        seen.add(mid)
        out.append(mid)
    return sorted(out)


def fetch_openrouter() -> tuple[dict[str, dict[str, float]], list[str]]:
    payload = http_get_json(OPENROUTER_MODELS_URL)
    rows = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise RuntimeError("OpenRouter payload missing 'data' list")

    prices: dict[str, dict[str, float]] = {}
    free_models: list[str] = []
    seen_free: set[str] = set()

    for entry in rows:
        if not isinstance(entry, dict):
            continue
        mid = str(entry.get("id") or "").strip()
        if not mid:
            continue
        norm = mid.lower()
        is_free = norm.endswith(":free") or norm == "openrouter/free"

        pricing = entry.get("pricing")
        if isinstance(pricing, dict):
            try:
                p_tok = float(pricing.get("prompt", "-1"))
                c_tok = float(pricing.get("completion", "-1"))
            except (TypeError, ValueError):
                p_tok, c_tok = -1.0, -1.0

            if p_tok >= 0 and c_tok >= 0 and not (is_free and p_tok == 0 and c_tok == 0):
                p_1m = round(p_tok * 1_000_000.0, 6)
                c_1m = round(c_tok * 1_000_000.0, 6)
                if p_1m > 0 or c_1m > 0:
                    prices[norm] = {
                        "prompt_per_1m": p_1m,
                        "completion_per_1m": c_1m,
                    }

        if is_free and not is_noise_model(mid) and mid not in seen_free:
            seen_free.add(mid)
            free_models.append(mid)

    if len(prices) < MIN_PRICED_MODELS:
        raise RuntimeError(f"OpenRouter returned only {len(prices)} priced models (< {MIN_PRICED_MODELS})")

    free_sorted = sorted(free_models)[:MAX_OPENROUTER_FREE_MODELS]
    if "openrouter/free" not in free_sorted:
        free_sorted.append("openrouter/free")
    return dict(sorted(prices.items())), free_sorted


def fetch_optional_provider_models(provider_id: str) -> list[str] | None:
    try:
        if provider_id == "groq":
            key = os.getenv("GROQ_API_KEY", "").strip()
            if not key:
                return None
            data = http_get_json(
                "https://api.groq.com/openai/v1/models",
                {"Authorization": f"Bearer {key}"},
            )
            return extract_model_ids(data)

        if provider_id in ("gemini-free", "gemini"):
            key = os.getenv("GEMINI_FREE_API_KEY", "").strip()
            if not key:
                return None
            data = http_get_json(
                "https://generativelanguage.googleapis.com/v1beta/openai/models",
                {"Authorization": f"Bearer {key}"},
            )
            ids = extract_model_ids(data, strip_prefix="models/")
            return [m for m in ids if any(k in m.lower() for k in ("gemini", "gemma", "embedding"))]

        if provider_id == "cohere":
            key = os.getenv("COHERE_API_KEY", "").strip()
            if not key:
                return None
            data = http_get_json(
                "https://api.cohere.com/compatibility/v1/models",
                {"Authorization": f"Bearer {key}"},
            )
            return extract_model_ids(data)

        if provider_id == "github":
            key = (os.getenv("GH_MODELS_KEY") or os.getenv("GITHUB_MODELS_KEY") or "").strip()
            if not key:
                return None
            data = http_get_json(
                "https://models.inference.ai.azure.com/models",
                {"Authorization": f"Bearer {key}"},
            )
            return extract_model_ids(data)

        if provider_id == "cloudflare":
            token = os.getenv("CLOUDFLARE_API_KEY", "").strip()
            acc_id = os.getenv("CLOUDFLARE_ACCOUNT_ID", "").strip()
            if not token or not acc_id:
                return None
            headers = {"Authorization": f"Bearer {token}"}
            gen = http_get_json(
                f"https://api.cloudflare.com/client/v4/accounts/{acc_id}/ai/models/search?task=Text%20Generation&per_page=100",
                headers,
            )
            emb = http_get_json(
                f"https://api.cloudflare.com/client/v4/accounts/{acc_id}/ai/models/search?task=Text%20Embeddings&per_page=50",
                headers,
            )
            merged = sorted(set(extract_model_ids(gen)) | set(extract_model_ids(emb)))
            return merged or None
    except Exception as exc:  # noqa: BLE001 — Keep-Last-Good fallback
        print(f"[warn] provider '{provider_id}' refresh failed, keeping last-good list: {exc}", file=sys.stderr)
        return None
    return None


def main() -> int:
    base_data = yaml.safe_load(BASE_FILE.read_text(encoding="utf-8"))
    base_providers = base_data.get("providers") or []

    existing_doc: dict[str, Any] = {}
    if OUT_FILE.exists():
        loaded = yaml.safe_load(OUT_FILE.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            existing_doc = loaded

    existing_providers: dict[str, list[str]] = {}
    for p in existing_doc.get("providers") or []:
        if isinstance(p, dict) and p.get("id") and isinstance(p.get("models"), list):
            existing_providers[str(p["id"])] = [str(m) for m in p["models"] if str(m).strip()]

    prices, openrouter_free = fetch_openrouter()

    providers_out: list[dict[str, Any]] = []
    for raw_p in base_providers:
        p = copy.deepcopy(raw_p)
        pid = str(p["id"])
        fallback_models = existing_providers.get(pid) or [str(m) for m in (p.get("models") or [])]

        if pid == "openrouter":
            p["models"] = openrouter_free or fallback_models
        else:
            live = fetch_optional_provider_models(pid)
            p["models"] = live if live else fallback_models

        providers_out.append(p)

    # Preserve content stability so daily cron skips commit when nothing changed
    content_new = {
        "schema_version": 1,
        "source": "https://github.com/2lk-agency/2lk.router-registry",
        "pricing": {
            "default": {
                "prompt_per_1m": 0.5,
                "completion_per_1m": 1.5,
            },
            "models": prices,
        },
        "providers": providers_out,
    }
    content_old = {
        "schema_version": existing_doc.get("schema_version"),
        "source": existing_doc.get("source"),
        "pricing": existing_doc.get("pricing"),
        "providers": existing_doc.get("providers"),
    }

    if existing_doc and content_new == content_old:
        print(f"No changes detected ({len(prices)} priced models, {len(providers_out)} providers).")
        return 0

    now_utc = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    final_doc = {
        "schema_version": 1,
        "source": content_new["source"],
        "updated_at": now_utc,
        "pricing": content_new["pricing"],
        "providers": content_new["providers"],
    }

    rendered = (
        "# Auto-generated by 2lk.router-registry (OpenRouter pricing + provider models).\n"
        "# Trigger manually via `2lk-router -sync-registry` or Web Dashboard -> Sync Registry.\n"
        + yaml.safe_dump(final_doc, sort_keys=False, allow_unicode=False, width=120)
    )

    match = SECRET_RE.search(rendered)
    if match:
        print(f"FATAL: Secret-like pattern detected in generated output: {match.group(1)[:12]}...", file=sys.stderr)
        return 2

    OUT_FILE.write_text(rendered, encoding="utf-8")
    print(f"Updated registry.yaml: {len(prices)} priced models, {len(providers_out)} providers at {now_utc}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
