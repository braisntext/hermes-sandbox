"""OpenRouter image generation backend.

Uses OpenRouter's chat completions API with ``modalities: ["image"]`` —
same OPENROUTER_API_KEY already used by the Hermes gateway for LLM calls,
so no extra credentials needed.

Endpoint: ``https://openrouter.ai/api/v1/chat/completions``
Default model: ``x-ai/grok-imagine-image-quality``
  - High-quality image generation via xAI Grok through OpenRouter
  - Response: base64 data URL in choices[0].message.images[0].image_url.url

Auth: ``OPENROUTER_API_KEY`` env var (already configured in Zeabur).
If the key is absent, ``is_available()`` returns False — no crash.

Model selection precedence:
  1. ``OPENROUTER_IMAGE_MODEL`` env var (escape hatch)
  2. ``image_gen.openrouter.model`` in config.yaml
  3. ``image_gen.model`` in config.yaml (when it matches our catalog)
  4. ``DEFAULT_MODEL``
"""

from __future__ import annotations

import base64
import logging
import os
from typing import Any, Dict, List, Optional, Tuple

from agent.image_gen_provider import (
    DEFAULT_ASPECT_RATIO,
    ImageGenProvider,
    error_response,
    resolve_aspect_ratio,
    save_b64_image,
    success_response,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Model catalog
# ---------------------------------------------------------------------------

_MODELS: Dict[str, Dict[str, Any]] = {
    "x-ai/grok-imagine-image-quality": {
        "display": "Grok Imagine (Quality)",
        "speed": "~5-15s",
        "strengths": "High-quality generation via xAI Grok on OpenRouter",
        "price": "~$0.07 per image",
    },
    "black-forest-labs/flux.2-klein-4b": {
        "display": "FLUX.2 Klein 4B",
        "speed": "~5-10s",
        "strengths": "Cheap, reliable on OpenRouter, good quality",
        "price": "~$0.014 per image",
    },
    "black-forest-labs/FLUX-schnell": {
        "display": "FLUX.1-schnell",
        "speed": "~5-10s",
        "strengths": "Free tier fallback",
        "price": "~$0.001-0.003 per image",
    },
    "black-forest-labs/FLUX-1.1-pro": {
        "display": "FLUX 1.1 Pro",
        "speed": "~10-20s",
        "strengths": "Higher fidelity, better prompt adherence",
        "price": "~$0.04 per image",
    },
}

DEFAULT_MODEL = "x-ai/grok-imagine-image-quality"

_OPENROUTER_CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------


def _load_or_config() -> Dict[str, Any]:
    try:
        from hermes_cli.config import load_config

        cfg = load_config()
        section = cfg.get("image_gen") if isinstance(cfg, dict) else None
        return section if isinstance(section, dict) else {}
    except Exception as exc:
        logger.debug("Could not load image_gen config: %s", exc)
        return {}


def _resolve_model() -> Tuple[str, Dict[str, Any]]:
    env_override = os.environ.get("OPENROUTER_IMAGE_MODEL", "").strip()
    if env_override and env_override in _MODELS:
        return env_override, _MODELS[env_override]

    cfg = _load_or_config()
    or_cfg = cfg.get("openrouter")
    if isinstance(or_cfg, dict):
        candidate = or_cfg.get("model", "").strip()
        if candidate in _MODELS:
            return candidate, _MODELS[candidate]

    top = cfg.get("model", "").strip() if isinstance(cfg.get("model"), str) else ""
    if top in _MODELS:
        return top, _MODELS[top]

    return DEFAULT_MODEL, _MODELS[DEFAULT_MODEL]


def _extract_b64_from_data_url(data_url: str) -> Optional[str]:
    """Strip the ``data:image/...;base64,`` prefix and return the raw b64 string."""
    if "," in data_url:
        return data_url.split(",", 1)[1]
    return None


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------


class OpenRouterImageGenProvider(ImageGenProvider):
    """OpenRouter image generation via chat completions + modalities: [image]."""

    @property
    def name(self) -> str:
        return "openrouter"

    @property
    def display_name(self) -> str:
        return "OpenRouter"

    def is_available(self) -> bool:
        if not os.environ.get("OPENROUTER_API_KEY"):
            logger.debug("image_gen/openrouter: OPENROUTER_API_KEY not set — provider unavailable")
            return False
        try:
            import requests  # noqa: F401
        except ImportError:
            logger.debug("image_gen/openrouter: requests package not installed")
            return False
        return True

    def list_models(self) -> List[Dict[str, Any]]:
        return [
            {
                "id": model_id,
                "display": meta["display"],
                "speed": meta["speed"],
                "strengths": meta["strengths"],
                "price": meta["price"],
            }
            for model_id, meta in _MODELS.items()
        ]

    def default_model(self) -> Optional[str]:
        return DEFAULT_MODEL

    def get_setup_schema(self) -> Dict[str, Any]:
        return {
            "name": "OpenRouter",
            "badge": "paid",
            "tag": "Grok Imagine (quality) via openrouter.ai — uses existing OPENROUTER_API_KEY",
            "env_vars": [
                {
                    "key": "OPENROUTER_API_KEY",
                    "prompt": "OpenRouter API key",
                    "url": "https://openrouter.ai/keys",
                },
            ],
        }

    def generate(
        self,
        prompt: str,
        aspect_ratio: str = DEFAULT_ASPECT_RATIO,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        prompt = (prompt or "").strip()
        aspect = resolve_aspect_ratio(aspect_ratio)

        if not prompt:
            return error_response(
                error="Prompt is required and must be a non-empty string",
                error_type="invalid_argument",
                provider="openrouter",
                aspect_ratio=aspect,
            )

        api_key = os.environ.get("OPENROUTER_API_KEY")
        if not api_key:
            logger.warning("image_gen/openrouter: OPENROUTER_API_KEY not set")
            return error_response(
                error="OPENROUTER_API_KEY not set.",
                error_type="auth_required",
                provider="openrouter",
                aspect_ratio=aspect,
            )

        try:
            import requests
        except ImportError:
            return error_response(
                error="requests package not installed (pip install requests)",
                error_type="missing_dependency",
                provider="openrouter",
                aspect_ratio=aspect,
            )

        model_id, _meta = _resolve_model()

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload: Dict[str, Any] = {
            "model": model_id,
            "modalities": ["image"],
            "messages": [{"role": "user", "content": prompt}],
        }

        try:
            logger.debug("image_gen/openrouter: POST %s model=%s", _OPENROUTER_CHAT_URL, model_id)
            response = requests.post(
                _OPENROUTER_CHAT_URL,
                headers=headers,
                json=payload,
                timeout=60,
            )
        except Exception as exc:
            logger.warning("image_gen/openrouter: request failed: %s", exc)
            return error_response(
                error=f"OpenRouter API request failed: {exc}",
                error_type="network_error",
                provider="openrouter",
                model=model_id,
                prompt=prompt,
                aspect_ratio=aspect,
            )

        if response.status_code != 200:
            body = response.text[:500]
            logger.warning("image_gen/openrouter: HTTP %d — %s", response.status_code, body)
            return error_response(
                error=f"OpenRouter API error {response.status_code}: {body}",
                error_type="api_error",
                provider="openrouter",
                model=model_id,
                prompt=prompt,
                aspect_ratio=aspect,
            )

        try:
            data = response.json()
            image_url = data["choices"][0]["message"]["images"][0]["image_url"]["url"]
            if not image_url:
                raise ValueError("empty image_url")
        except Exception as exc:
            logger.warning(
                "image_gen/openrouter: could not parse response: %s — body: %s",
                exc, response.text[:300],
            )
            return error_response(
                error=f"Could not parse OpenRouter response: {exc}",
                error_type="parse_error",
                provider="openrouter",
                model=model_id,
                prompt=prompt,
                aspect_ratio=aspect,
            )

        # Response is a base64 data URL: "data:image/png;base64,<data>"
        if image_url.startswith("data:"):
            b64 = _extract_b64_from_data_url(image_url)
            if not b64:
                return error_response(
                    error="Could not extract base64 data from image data URL",
                    error_type="parse_error",
                    provider="openrouter",
                    model=model_id,
                    prompt=prompt,
                    aspect_ratio=aspect,
                )
            try:
                saved_path = save_b64_image(b64, prefix=f"or_{model_id.split('/')[-1]}")
            except Exception as exc:
                logger.warning("image_gen/openrouter: could not save image: %s", exc)
                return error_response(
                    error=f"Could not save image to cache: {exc}",
                    error_type="io_error",
                    provider="openrouter",
                    model=model_id,
                    prompt=prompt,
                    aspect_ratio=aspect,
                )
            return success_response(
                image=str(saved_path),
                model=model_id,
                prompt=prompt,
                aspect_ratio=aspect,
                provider="openrouter",
            )

        # Plain URL fallback (future-proof)
        return success_response(
            image=image_url,
            model=model_id,
            prompt=prompt,
            aspect_ratio=aspect,
            provider="openrouter",
        )


# ---------------------------------------------------------------------------
# Plugin entry point
# ---------------------------------------------------------------------------


def register(ctx) -> None:
    """Plugin entry point — wire OpenRouterImageGenProvider into the registry."""
    ctx.register_image_gen_provider(OpenRouterImageGenProvider())
