import json
import unittest
from unittest.mock import patch

from app.assistant import _model


class FakeStream:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    def raise_for_status(self):
        return None

    async def aiter_lines(self):
        for text, finished in (("Hello", None), (" world", None), ("!", "stop")):
            yield "data: " + json.dumps({
                "choices": [{"delta": {"content": text}, "finish_reason": finished}],
            })
        yield "data: [DONE]"


class FakeClient:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    def stream(self, method, url, **kwargs):
        assert method == "POST"
        assert kwargs["json"]["stream"] is True
        assert url.endswith("/chat/completions")
        return FakeStream()


class StreamingChatTests(unittest.IsolatedAsyncioTestCase):
    async def test_model_emits_chunks_before_completion(self):
        pieces = []

        async def emit(piece: str):
            pieces.append(piece)

        with (
            patch("app.assistant.settings.ai_provider", "deepseek"),
            patch("app.assistant.settings.deepseek_api_key", "test-key"),
            patch("app.assistant.httpx.AsyncClient", return_value=FakeClient()),
        ):
            result = await _model("test", "hello", raise_on_error=True, on_delta=emit)

        self.assertEqual(pieces, ["Hello", " world", "!"])
        self.assertEqual(result, "Hello world!")


if __name__ == "__main__":
    unittest.main()
