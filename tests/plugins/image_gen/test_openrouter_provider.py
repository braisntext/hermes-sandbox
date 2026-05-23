"""Tests for the OpenRouter image_gen plugin (FLUX via openrouter.ai)."""

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

    def test_default_model(self):
        provider = openrouter_plugin.OpenRouterImageGenProvider()
        assert provider.default_model() == "black-forest-labs/flux.2-klein-4b"

    def test_list_models_has_required_fields(self):
        provider = openrouter_plugin.OpenRouterImageGenProvider()
        for entry in provider.list_models():
            assert "id" in entry
            assert "display" in entry
            assert "speed" in entry
            assert "price" in entry

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
        assert model_id == openrouter_plugin.DEFAULT_MODEL
        assert "display" in meta

    def test_env_var_override(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_IMAGE_MODEL", "black-forest-labs/FLUX-schnell")
        model_id, _ = openrouter_plugin._resolve_model()
        assert model_id == "black-forest-labs/FLUX-schnell"

    def test_env_var_unknown_falls_back(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_IMAGE_MODEL", "unknown/model")
        model_id, _ = openrouter_plugin._resolve_model()
        assert model_id == openrouter_plugin.DEFAULT_MODEL


# ── Generate ────────────────────────────────────────────────────────────────


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

    def test_url_response_success(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "data": [{"url": "https://openrouter.ai/img/result.png"}],
        }

        with patch("requests.post", return_value=mock_resp):
            provider = openrouter_plugin.OpenRouterImageGenProvider()
            result = provider.generate("a cat in space")

        assert result["success"] is True
        assert result["image"] == "https://openrouter.ai/img/result.png"
        assert result["provider"] == "openrouter"
        assert result["model"] == openrouter_plugin.DEFAULT_MODEL

    def test_b64_response_saves_to_cache(self, tmp_path):
        raw = b"fake-png-bytes"
        b64 = base64.b64encode(raw).decode()

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"data": [{"b64_json": b64}]}

        with patch("requests.post", return_value=mock_resp):
            with patch(
                "plugins.image_gen.openrouter.save_b64_image",
                return_value=tmp_path / "test.png",
            ) as mock_save:
                provider = openrouter_plugin.OpenRouterImageGenProvider()
                result = provider.generate("a mountain")

        assert result["success"] is True
        assert "test.png" in result["image"]
        mock_save.assert_called_once()

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
        mock_resp.json.return_value = {"data": [{}]}  # no url or b64_json
        mock_resp.text = "{}"

        with patch("requests.post", return_value=mock_resp):
            provider = openrouter_plugin.OpenRouterImageGenProvider()
            result = provider.generate("a cat")

        assert result["success"] is False
        assert result["error_type"] == "parse_error"

    def test_request_payload_shape(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "data": [{"url": "https://openrouter.ai/img/out.png"}],
        }

        with patch("requests.post", return_value=mock_resp) as mock_post:
            provider = openrouter_plugin.OpenRouterImageGenProvider()
            provider.generate("a cat", aspect_ratio="square")

        payload = mock_post.call_args.kwargs.get("json") or mock_post.call_args[1]["json"]
        assert payload["model"] == openrouter_plugin.DEFAULT_MODEL
        assert payload["prompt"] == "a cat"
        assert payload["n"] == 1
        assert "size" in payload
        assert "response_format" not in payload

    @pytest.mark.parametrize("aspect", ["landscape", "square", "portrait"])
    def test_aspect_ratio_accepted(self, aspect):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "data": [{"url": "https://openrouter.ai/img/out.png"}],
        }

        with patch("requests.post", return_value=mock_resp):
            provider = openrouter_plugin.OpenRouterImageGenProvider()
            result = provider.generate("a cat", aspect_ratio=aspect)

        assert result["success"] is True
        assert result["aspect_ratio"] == aspect


# ── Registration ─────────────────────────────────────────────────────────────


class TestRegistration:
    def test_register(self):
        mock_ctx = MagicMock()
        openrouter_plugin.register(mock_ctx)
        mock_ctx.register_image_gen_provider.assert_called_once()
        provider = mock_ctx.register_image_gen_provider.call_args[0][0]
        assert isinstance(provider, openrouter_plugin.OpenRouterImageGenProvider)
        assert provider.name == "openrouter"
