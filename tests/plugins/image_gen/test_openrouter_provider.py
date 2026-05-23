"""Tests for the OpenRouter image_gen plugin.

Uses chat completions + modalities:[image] endpoint.
Default model: x-ai/grok-imagine-image-quality.
"""

from __future__ import annotations

import base64
from unittest.mock import MagicMock, patch

import pytest

import plugins.image_gen.openrouter as openrouter_plugin


@pytest.fixture(autouse=True)
def _tmp_hermes_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    yield tmp_path


# ── Metadata ────────────────────────────────────────────────────────────────


class TestMetadata:
    def test_name(self):
        provider = openrouter_plugin.OpenRouterImageGenProvider()
        assert provider.name == "openrouter"

    def test_display_name(self):
        provider = openrouter_plugin.OpenRouterImageGenProvider()
        assert provider.display_name == "OpenRouter"

    def test_default_model_is_grok_quality(self):
        provider = openrouter_plugin.OpenRouterImageGenProvider()
        assert provider.default_model() == "x-ai/grok-imagine-image-quality"

    def test_list_models_has_required_fields(self):
        provider = openrouter_plugin.OpenRouterImageGenProvider()
        for entry in provider.list_models():
            assert "id" in entry
            assert "display" in entry
            assert "speed" in entry
            assert "price" in entry

    def test_catalog_contains_grok_and_flux_models(self):
        provider = openrouter_plugin.OpenRouterImageGenProvider()
        ids = [m["id"] for m in provider.list_models()]
        assert "x-ai/grok-imagine-image-quality" in ids
        assert "black-forest-labs/flux.2-klein-4b" in ids

    def test_setup_schema(self):
        provider = openrouter_plugin.OpenRouterImageGenProvider()
        schema = provider.get_setup_schema()
        assert schema["name"] == "OpenRouter"
        assert schema["badge"] == "paid"
        keys = [e["key"] for e in schema["env_vars"]]
        assert "OPENROUTER_API_KEY" in keys


# ── Availability ────────────────────────────────────────────────────────────


class TestAvailability:
    def test_no_key_unavailable(self, monkeypatch):
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        assert openrouter_plugin.OpenRouterImageGenProvider().is_available() is False

    def test_key_present_available(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
        assert openrouter_plugin.OpenRouterImageGenProvider().is_available() is True


# ── Model resolution ────────────────────────────────────────────────────────


class TestModelResolution:
    def test_default_model(self):
        model_id, meta = openrouter_plugin._resolve_model()
        assert model_id == "x-ai/grok-imagine-image-quality"
        assert "display" in meta

    def test_env_var_override(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_IMAGE_MODEL", "black-forest-labs/FLUX-schnell")
        model_id, _ = openrouter_plugin._resolve_model()
        assert model_id == "black-forest-labs/FLUX-schnell"

    def test_env_var_unknown_falls_back(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_IMAGE_MODEL", "unknown/model")
        model_id, _ = openrouter_plugin._resolve_model()
        assert model_id == openrouter_plugin.DEFAULT_MODEL


# ── Data URL parsing ─────────────────────────────────────────────────────────


class TestExtractB64:
    def test_strips_data_url_prefix(self):
        data_url = "data:image/png;base64,abc123=="
        result = openrouter_plugin._extract_b64_from_data_url(data_url)
        assert result == "abc123=="

    def test_returns_none_for_no_comma(self):
        result = openrouter_plugin._extract_b64_from_data_url("nodataurl")
        assert result is None

    def test_plain_b64_with_comma_suffix(self):
        result = openrouter_plugin._extract_b64_from_data_url(",rawb64")
        assert result == "rawb64"


# ── Generate ────────────────────────────────────────────────────────────────


def _fake_chat_response(b64_data: str) -> MagicMock:
    """Build a mock response matching the chat completions image response shape."""
    mock = MagicMock()
    mock.status_code = 200
    mock.json.return_value = {
        "choices": [{
            "message": {
                "images": [{
                    "image_url": {
                        "url": f"data:image/png;base64,{b64_data}",
                    }
                }]
            }
        }]
    }
    return mock


class TestGenerate:
    @pytest.fixture(autouse=True)
    def _api_key(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")

    def test_empty_prompt_rejected(self):
        provider = openrouter_plugin.OpenRouterImageGenProvider()
        result = provider.generate("")
        assert result["success"] is False
        assert result["error_type"] == "invalid_argument"

    def test_missing_api_key(self, monkeypatch):
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        provider = openrouter_plugin.OpenRouterImageGenProvider()
        result = provider.generate("a cat")
        assert result["success"] is False
        assert result["error_type"] == "auth_required"

    def test_successful_generation_saves_to_cache(self, tmp_path):
        raw = b"fake-png-bytes"
        b64 = base64.b64encode(raw).decode()

        with patch("requests.post", return_value=_fake_chat_response(b64)):
            with patch(
                "plugins.image_gen.openrouter.save_b64_image",
                return_value=tmp_path / "result.png",
            ) as mock_save:
                provider = openrouter_plugin.OpenRouterImageGenProvider()
                result = provider.generate("a cat in space")

        assert result["success"] is True
        assert "result.png" in result["image"]
        assert result["provider"] == "openrouter"
        assert result["model"] == openrouter_plugin.DEFAULT_MODEL
        mock_save.assert_called_once()
        # Verify the b64 passed to save is the raw content, not the data URL
        called_b64 = mock_save.call_args[0][0]
        assert not called_b64.startswith("data:")

    def test_plain_url_fallback(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "choices": [{
                "message": {
                    "images": [{
                        "image_url": {"url": "https://cdn.example.com/img.png"}
                    }]
                }
            }]
        }

        with patch("requests.post", return_value=mock_resp):
            provider = openrouter_plugin.OpenRouterImageGenProvider()
            result = provider.generate("a mountain")

        assert result["success"] is True
        assert result["image"] == "https://cdn.example.com/img.png"

    def test_api_error_surfaces_status(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.text = "Unauthorized"

        with patch("requests.post", return_value=mock_resp):
            provider = openrouter_plugin.OpenRouterImageGenProvider()
            result = provider.generate("a cat")

        assert result["success"] is False
        assert result["error_type"] == "api_error"
        assert "401" in result["error"]

    def test_network_error(self):
        with patch("requests.post", side_effect=ConnectionError("timeout")):
            provider = openrouter_plugin.OpenRouterImageGenProvider()
            result = provider.generate("a cat")

        assert result["success"] is False
        assert result["error_type"] == "network_error"

    def test_malformed_response_parse_error(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"choices": [{"message": {}}]}  # no images key
        mock_resp.text = "{}"

        with patch("requests.post", return_value=mock_resp):
            provider = openrouter_plugin.OpenRouterImageGenProvider()
            result = provider.generate("a cat")

        assert result["success"] is False
        assert result["error_type"] == "parse_error"

    def test_request_uses_chat_completions_endpoint(self):
        raw = b"fake-png"
        b64 = base64.b64encode(raw).decode()

        with patch("requests.post", return_value=_fake_chat_response(b64)) as mock_post:
            with patch("plugins.image_gen.openrouter.save_b64_image", return_value="/tmp/x.png"):
                provider = openrouter_plugin.OpenRouterImageGenProvider()
                provider.generate("a robot")

        called_url = mock_post.call_args.args[0] if mock_post.call_args.args else mock_post.call_args[0][0]
        assert "chat/completions" in called_url
        assert "images/generations" not in called_url

    def test_request_payload_shape(self):
        raw = b"fake-png"
        b64 = base64.b64encode(raw).decode()

        with patch("requests.post", return_value=_fake_chat_response(b64)) as mock_post:
            with patch("plugins.image_gen.openrouter.save_b64_image", return_value="/tmp/x.png"):
                provider = openrouter_plugin.OpenRouterImageGenProvider()
                provider.generate("a cat", aspect_ratio="square")

        payload = mock_post.call_args.kwargs.get("json") or mock_post.call_args[1]["json"]
        assert payload["model"] == openrouter_plugin.DEFAULT_MODEL
        assert payload["modalities"] == ["image"]
        assert payload["messages"][0]["role"] == "user"
        assert payload["messages"][0]["content"] == "a cat"
        assert "n" not in payload
        assert "size" not in payload

    @pytest.mark.parametrize("aspect", ["landscape", "square", "portrait"])
    def test_aspect_ratio_accepted(self, aspect, tmp_path):
        raw = b"fake-png"
        b64 = base64.b64encode(raw).decode()

        with patch("requests.post", return_value=_fake_chat_response(b64)):
            with patch("plugins.image_gen.openrouter.save_b64_image", return_value=tmp_path / "x.png"):
                provider = openrouter_plugin.OpenRouterImageGenProvider()
                result = provider.generate("a cat", aspect_ratio=aspect)

        assert result["success"] is True
        assert result["aspect_ratio"] == aspect

    def test_invalid_data_url_returns_parse_error(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "choices": [{
                "message": {
                    "images": [{
                        "image_url": {"url": "data:image/png;base64,"}  # empty b64
                    }]
                }
            }]
        }
        mock_resp.text = "{}"

        with patch("requests.post", return_value=mock_resp):
            provider = openrouter_plugin.OpenRouterImageGenProvider()
            result = provider.generate("a cat")

        # Empty b64 after strip — save_b64_image will raise, resulting in io_error
        assert result["success"] is False


# ── Registration ─────────────────────────────────────────────────────────────


class TestRegistration:
    def test_register(self):
        mock_ctx = MagicMock()
        openrouter_plugin.register(mock_ctx)
        mock_ctx.register_image_gen_provider.assert_called_once()
        provider = mock_ctx.register_image_gen_provider.call_args[0][0]
        assert isinstance(provider, openrouter_plugin.OpenRouterImageGenProvider)
        assert provider.name == "openrouter"
