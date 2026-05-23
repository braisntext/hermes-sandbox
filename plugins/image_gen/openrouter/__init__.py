"""OpenRouter image generation backend.

Uses OpenRouter's image generation API — same OPENROUTER_API_KEY already
used by the Hermes gateway for LLM calls, so no extra credentials needed.

Endpoint: ``https://openrouter.ai/api/v1/images/generations``
Default model: ``black-forest-labs/FLUX-schnell``
  - Fast (~5-10s), high quality, pay-per-use via OpenRouter credits
  - Compatible with OpenAI images.generate() wire format

Auth: ``OPENROUTER_API_KEY`` env var (already configured in Zeabur).
If the key is absent, ``is_available()`` returns False — no crash.

Model selection precedence:
  1. ``OPENROUTER_IMAGE_MODEL`` env var (escape hatch)
  2. ``image_gen.openrouter.model`` in config.yaml
  3. ``image_gen.model`` in config.yaml (when it matches our catalog)
  4. ``DEFAULT_MODEL``
"""

from __future__ import annotations

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
    "black-forest-labs/FLUX-schnell": {
        "display": "FLUX.1-schnell",
        "speed": "~5-10s",
        "strengths": "Fast, high quality, pay-per-use via OpenRouter credits",
        "price": "~$0.001-0.003 per image",
    },
    "black-forest-labs/FLUX-1.1-pro": {
        "display": "FLUX 1.1 Pro",
        "speed": "~10-20s",
        "strengths": "Higher fidelity, better prompt adherence",
        "price": "~$0.04 per image",
    },
}

DEFAULT_MODEL = "black-forest-labs/FLUX-schnell"

_OPENROUTER_IMAGE_URL = "https://openrouter.ai/api/v1/images/generations"

# Aspect ratio → approximate pixel dimensions for the API request.
# OpenRouter/FLUX accept a "size" string (WxH).
_ASPECT_TO_SIZE: Dict[str, str] = {
    "landscape": "1344x768",
    "square": "1024x1024",
    "portrait": "768x1344",
}


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


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------


class OpenRouterImageGenProvider(ImageGenProvider):
    """OpenRouter image generation backend (FLUX via openrouter.ai)."""

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
            "tag": "FLUX-schnell via openrouter.ai — uses existing OPENROUTER_API_KEY",
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
            logger.warning(
                "image_gen/openrouter: OPENROUTER_API_KEY not set — "
                "cannot generate image."
            )
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
        size = _ASPECT_TO_SIZE.get(aspect, _ASPECT_TO_SIZE["square"])

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload: Dict[str, Any] = {
            "model": model_id,
            "prompt": prompt,
            "n": 1,
            "size": size,
            "response_format": "b64_json",
        }

        try:
            logger.debug("image_gen/openrouter: POST %s model=%s", _OPENROUTER_IMAGE_URL, model_id)
            response = requests.post(
                _OPENROUTER_IMAGE_URL,
                headers=headers,
                json=payload,
                timeout=30,
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
            logger.warning(
                "image_gen/openrouter: HTTP %d — %s", response.status_code, body
            )
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
            b64 = data["data"][0]["b64_json"]
        except Exception as exc:
            # Fallback: try URL format
            try:
                url = data["data"][0]["url"]
                return success_response(
                    image=url,
                    model=model_id,
                    prompt=prompt,
                    aspect_ratio=aspect,
                    provider="openrouter",
                    extra={"size": size},
                )
            except Exception:
                pass
            logger.warning("image_gen/openrouter: could not parse response: %s", exc)
            return error_response(
                error=f"Could not parse OpenRouter response: {exc}",
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
            extra={"size": size},
        )


# ---------------------------------------------------------------------------
# Plugin entry point
# ---------------------------------------------------------------------------


def register(ctx) -> None:
    """Plugin entry point — wire OpenRouterImageGenProvider into the registry."""
    ctx.register_image_gen_provider(OpenRouterImageGenProvider())
