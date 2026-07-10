import importlib
import json
import os
import unittest
from unittest.mock import patch

import app.config.settings as settings_module
import app.services.assistant_ollama as assistant_ollama
from app.schemas.assistant import AssistantAnswerRequest, AssistantSource


class AssistantOllamaSettingsTest(unittest.TestCase):
    def test_num_predict_defaults_to_256(self) -> None:
        self.assertEqual(self._load_num_predict(None), 256)

    def test_explicit_valid_num_predict_override_is_honored(self) -> None:
        self.assertEqual(self._load_num_predict("384"), 384)

    def test_non_positive_num_predict_is_rejected(self) -> None:
        for invalid_value in ("0", "-1"):
            with self.subTest(invalid_value=invalid_value):
                with self.assertRaisesRegex(
                    ValueError,
                    "ASSISTANT_OLLAMA_NUM_PREDICT must be >= 1",
                ):
                    self._load_num_predict(invalid_value)

    def _load_num_predict(self, value: str | None) -> int:
        try:
            with patch.dict(os.environ, {"DOTENV_PATH": "/tmp/no-dotenv"}, clear=False):
                if value is None:
                    os.environ.pop("ASSISTANT_OLLAMA_NUM_PREDICT", None)
                else:
                    os.environ["ASSISTANT_OLLAMA_NUM_PREDICT"] = value
                return importlib.reload(settings_module).settings.ASSISTANT_OLLAMA_NUM_PREDICT
        finally:
            importlib.reload(settings_module)


class OllamaAssistantClientTest(unittest.TestCase):
    def setUp(self) -> None:
        self.request = AssistantAnswerRequest(
            question="What does the source say?",
            sources=[
                AssistantSource(
                    sourceId="source-1",
                    assetId="asset-1",
                    transcriptRowId="row-1",
                    text="The source contains a grounded answer.",
                )
            ],
        )
        self.provider_response = {
            "response": json.dumps(
                {
                    "answer": "The source contains a grounded answer.",
                    "citedSourceIds": ["source-1"],
                    "insufficientContext": False,
                }
            )
        }

    def test_default_payload_is_bounded_and_preserves_structured_options(self) -> None:
        response, payload = self._answer_and_payload(256)

        self.assertEqual(payload["options"], {"temperature": 0, "num_predict": 256})
        self.assertIs(payload["stream"], False)
        self.assertIs(payload["think"], False)
        self.assertEqual(payload["format"], assistant_ollama.ASSISTANT_RESPONSE_SCHEMA)
        self.assertEqual(response.answer, "The source contains a grounded answer.")
        self.assertEqual(response.citedSourceIds, ["source-1"])
        self.assertIs(response.insufficientContext, False)

    def test_explicit_num_predict_override_is_included_in_payload(self) -> None:
        _, payload = self._answer_and_payload(384)

        self.assertEqual(payload["options"]["num_predict"], 384)
        self.assertEqual(payload["options"]["temperature"], 0)

    def _answer_and_payload(self, num_predict: int):
        client = assistant_ollama.OllamaAssistantClient()
        with (
            patch.object(assistant_ollama.settings, "ASSISTANT_LLM_ENABLED", True),
            patch.object(assistant_ollama.settings, "ASSISTANT_OLLAMA_BASE_URL", "http://ollama.invalid"),
            patch.object(assistant_ollama.settings, "ASSISTANT_OLLAMA_MODEL", "test-model"),
            patch.object(assistant_ollama.settings, "ASSISTANT_OLLAMA_NUM_PREDICT", num_predict),
            patch.object(client, "_post_generate", return_value=(self.provider_response, 10)) as post_generate,
        ):
            response = client.answer(self.request)

        return response, post_generate.call_args.args[0]


if __name__ == "__main__":
    unittest.main()
