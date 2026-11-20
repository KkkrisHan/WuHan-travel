import json
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app


class StubResponse:
    def __init__(self, status_code):
        self.status_code = status_code


class StubAsyncClient:
    def __init__(self, response):
        self.response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def post(self, *_args, **_kwargs):
        return self.response


class ServiceConfigApiTest(unittest.TestCase):
    def test_status_exposes_only_browser_js_key(self):
        effective = {
            "amapJsKey": "public-js-key-sentinel",
            "amapJsSecurityCode": "secret-security-sentinel",
            "amapWebServiceKey": "secret-web-key-sentinel",
            "provider": "deepseek",
            "deepseekApiKey": "secret-ai-key-sentinel",
            "openaiApiKey": "",
            "deepseekModel": "deepseek-flash",
            "openaiModel": "gpt-4.1-mini",
        }
        with patch("app.main.service_config.effective_config", return_value=effective):
            response = TestClient(app).get("/api/services/status")

        self.assertEqual(response.status_code, 200)
        body = json.dumps(response.json())
        self.assertIn("public-js-key-sentinel", body)
        for secret in ("secret-security-sentinel", "secret-web-key-sentinel", "secret-ai-key-sentinel"):
            self.assertNotIn(secret, body)
        self.assertNotIn("amapJsSecurityCode", body)
        self.assertNotIn("amapWebServiceKey", body)
        self.assertNotIn("deepseekApiKey", body)

    def test_config_write_rejects_non_local_host_before_validation(self):
        with patch("app.main.service_config.save_ai") as save:
            response = TestClient(app).post(
                "/api/services/ai",
                headers={"Host": "attacker.example", "Origin": "https://attacker.example"},
                json={"provider": "deepseek", "apiKey": "test-placeholder", "model": "deepseek-flash"},
            )
        self.assertEqual(response.status_code, 403)
        save.assert_not_called()

    def test_config_write_rejects_non_local_origin(self):
        with patch("app.main.service_config.save_ai") as save:
            response = TestClient(app).post(
                "/api/services/ai",
                headers={"Host": "localhost:8000", "Origin": "https://attacker.example"},
                json={"provider": "deepseek", "apiKey": "test-placeholder", "model": "deepseek-flash"},
            )
        self.assertEqual(response.status_code, 403)
        save.assert_not_called()

    def test_config_write_rejects_untrusted_local_origin_port(self):
        with patch("app.main.service_config.save_ai") as save:
            response = TestClient(app).post(
                "/api/services/ai",
                headers={"Host": "localhost:8000", "Origin": "http://localhost:3000"},
                json={"provider": "deepseek", "apiKey": "test-placeholder", "model": "deepseek-flash"},
            )
        self.assertEqual(response.status_code, 403)
        save.assert_not_called()

    def test_invalid_ai_key_is_not_persisted(self):
        with patch("app.main.httpx.AsyncClient", return_value=StubAsyncClient(StubResponse(401))), patch("app.main.service_config.save_ai") as save:
            response = TestClient(app).post(
                "/api/services/ai",
                headers={"Host": "localhost:8000", "Origin": "http://localhost:5173"},
                json={"provider": "deepseek", "apiKey": "test-placeholder", "model": "deepseek-flash"},
            )
        self.assertEqual(response.status_code, 422)
        save.assert_not_called()

    def test_valid_ai_key_is_persisted_after_validation(self):
        with patch("app.main.httpx.AsyncClient", return_value=StubAsyncClient(StubResponse(200))), patch("app.main.service_config.save_ai") as save:
            response = TestClient(app).post(
                "/api/services/ai",
                headers={"Host": "localhost:8000", "Origin": "http://localhost:5173"},
                json={"provider": "deepseek", "apiKey": "test-placeholder", "model": "deepseek-flash"},
            )
        self.assertEqual(response.status_code, 200)
        save.assert_called_once_with("deepseek", "test-placeholder", "deepseek-flash")

    def test_existing_provider_key_can_be_reused_without_returning_it(self):
        current = {
            "amapJsKey": "", "amapJsSecurityCode": "", "amapWebServiceKey": "",
            "provider": "deepseek", "deepseekApiKey": "reused-key-sentinel", "openaiApiKey": "",
            "deepseekModel": "deepseek-flash", "openaiModel": "gpt-4.1-mini",
        }
        with patch("app.main.service_config.effective_config", return_value=current), patch("app.main.httpx.AsyncClient", return_value=StubAsyncClient(StubResponse(200))), patch("app.main.service_config.save_ai") as save:
            response = TestClient(app).post(
                "/api/services/ai",
                headers={"Host": "localhost:8000", "Origin": "http://localhost:5173"},
                json={"provider": "deepseek", "apiKey": "", "model": "deepseek-flash"},
            )
        self.assertEqual(response.status_code, 200)
        save.assert_called_once_with("deepseek", "reused-key-sentinel", "deepseek-flash")
        self.assertNotIn("reused-key-sentinel", response.text)

    def test_invalid_secret_field_is_not_echoed_by_validation_response(self):
        oversized = "test-placeholder" * 40
        response = TestClient(app).post(
            "/api/services/ai",
            headers={"Host": "localhost:8000", "Origin": "http://localhost:5173"},
            json={"provider": "deepseek", "apiKey": oversized, "model": "deepseek-flash"},
        )
        self.assertEqual(response.status_code, 422)
        self.assertNotIn(oversized, response.text)


if __name__ == "__main__":
    unittest.main()
