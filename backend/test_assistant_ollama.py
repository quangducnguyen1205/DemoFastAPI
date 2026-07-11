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
                    sourceId="src-4d70d9585e4c4b7a8b5923df87e1a0c4",
                    assetId="asset-1",
                    transcriptRowId="row-1",
                    text="First source: the library opens at nine.",
                ),
                AssistantSource(
                    sourceId="src-38b9a8f3d2214c8ab5a4435ff74bd621",
                    assetId="asset-2",
                    transcriptRowId="row-2",
                    text="Second source: the library closes at six.",
                ),
                AssistantSource(
                    sourceId="src-9f5a4f1b72d04f6b8a3c11d4e6b79025",
                    assetId="asset-3",
                    transcriptRowId="row-3",
                    text="Third source: the library is closed on Sunday.",
                ),
            ],
        )
        self.provider_response = {
            "response": json.dumps(
                {
                    "answer": "The library opens at nine.",
                    "citedSourceIds": ["S1"],
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
        self.assertEqual(response.answer, "The library opens at nine.")
        self.assertEqual(response.citedSourceIds, [self.request.sources[0].sourceId])
        self.assertIs(response.insufficientContext, False)

    def test_explicit_num_predict_override_is_included_in_payload(self) -> None:
        _, payload = self._answer_and_payload(384)

        self.assertEqual(payload["options"]["num_predict"], 384)
        self.assertEqual(payload["options"]["temperature"], 0)

    def test_prompt_uses_deterministic_aliases_without_canonical_identifiers(self) -> None:
        _, payload = self._answer_and_payload(256)
        prompt = payload["prompt"]

        marker_positions = [prompt.index(f"[SOURCE_ID: S{index}]") for index in range(1, 4)]
        for index, source in enumerate(self.request.sources, start=1):
            alias = f"S{index}"
            marker = f"[SOURCE_ID: {alias}]"
            self.assertIn(marker, prompt)
            self.assertNotIn(source.sourceId, prompt)
            self.assertNotIn(source.transcriptRowId, prompt)
            block_end = marker_positions[index] if index < len(self.request.sources) else len(prompt)
            self.assertIn(source.text, prompt[marker_positions[index - 1]:block_end])
        self.assertLess(marker_positions[0], marker_positions[1])
        self.assertLess(marker_positions[1], marker_positions[2])
        self.assertIn("citedSourceIds must contain only supplied SOURCE_ID aliases", prompt)
        self.assertIn("Copy supplied SOURCE_ID aliases exactly", prompt)

    def test_provider_alias_maps_to_the_matching_canonical_source_id(self) -> None:
        response, _ = self._answer_and_payload(
            256,
            cited_source_ids=["S2"],
        )

        self.assertEqual(response.citedSourceIds, [self.request.sources[1].sourceId])

    def test_multiple_aliases_preserve_provider_order_and_deduplicate(self) -> None:
        response, _ = self._answer_and_payload(
            256,
            cited_source_ids=["S3", "S1", "S3", "S2"],
        )

        self.assertEqual(
            response.citedSourceIds,
            [
                self.request.sources[2].sourceId,
                self.request.sources[0].sourceId,
                self.request.sources[1].sourceId,
            ],
        )

    def test_unknown_alias_is_rejected_through_safe_provider_failure_path(self) -> None:
        self._assert_invalid_citations_are_rejected(["S4"])

    def test_malformed_alias_is_not_fuzzy_matched(self) -> None:
        self._assert_invalid_citations_are_rejected(["s1"])

    def test_answer_without_citation_alias_is_rejected(self) -> None:
        self._assert_invalid_citations_are_rejected([])

    def test_insufficient_context_response_preserves_empty_canonical_citations(self) -> None:
        response, _ = self._answer_and_payload(
            256,
            cited_source_ids=[],
            insufficient_context=True,
        )

        self.assertEqual(response.citedSourceIds, [])
        self.assertIs(response.insufficientContext, True)

    def test_post_generate_uses_the_configured_timeout_without_real_http(self) -> None:
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc_value, traceback):
                return False

            def read(self) -> bytes:
                return b'{"response":"{}"}'

        client = assistant_ollama.OllamaAssistantClient()
        with (
            patch.object(assistant_ollama.settings, "ASSISTANT_OLLAMA_BASE_URL", "http://ollama.invalid"),
            patch.object(assistant_ollama.settings, "ASSISTANT_OLLAMA_TIMEOUT_SECONDS", 60),
            patch.object(assistant_ollama, "urlopen", return_value=FakeResponse()) as urlopen,
        ):
            client._post_generate({"model": "test-model"})

        self.assertEqual(urlopen.call_args.kwargs["timeout"], 60)

    def _assert_invalid_citations_are_rejected(self, cited_source_ids: list[str]) -> None:
        client = assistant_ollama.OllamaAssistantClient()
        with (
            patch.object(assistant_ollama.settings, "ASSISTANT_LLM_ENABLED", True),
            patch.object(assistant_ollama.settings, "ASSISTANT_OLLAMA_BASE_URL", "http://ollama.invalid"),
            patch.object(assistant_ollama.settings, "ASSISTANT_OLLAMA_MODEL", "test-model"),
            patch.object(client, "_post_generate", return_value=(self._provider_response(cited_source_ids), 10)),
            patch.object(client, "_log_provider_failure") as log_provider_failure,
        ):
            with self.assertRaises(assistant_ollama.AssistantLlmUnavailable):
                client.answer(self.request)

        log_provider_failure.assert_called_once_with(
            "assistant_ollama_invalid_citation_aliases",
            provider_elapsed_ms=10,
        )

    def _answer_and_payload(
        self,
        num_predict: int,
        cited_source_ids: list[str] | None = None,
        insufficient_context: bool = False,
    ):
        client = assistant_ollama.OllamaAssistantClient()
        with (
            patch.object(assistant_ollama.settings, "ASSISTANT_LLM_ENABLED", True),
            patch.object(assistant_ollama.settings, "ASSISTANT_OLLAMA_BASE_URL", "http://ollama.invalid"),
            patch.object(assistant_ollama.settings, "ASSISTANT_OLLAMA_MODEL", "test-model"),
            patch.object(assistant_ollama.settings, "ASSISTANT_OLLAMA_NUM_PREDICT", num_predict),
            patch.object(
                client,
                "_post_generate",
                return_value=(
                    self._provider_response(cited_source_ids, insufficient_context),
                    10,
                ),
            ) as post_generate,
        ):
            response = client.answer(self.request)

        return response, post_generate.call_args.args[0]

    def _provider_response(
        self,
        cited_source_ids: list[str] | None = None,
        insufficient_context: bool = False,
    ) -> dict:
        if cited_source_ids is None and not insufficient_context:
            return self.provider_response
        return {
            "response": json.dumps(
                {
                    "answer": "A structured assistant response.",
                    "citedSourceIds": cited_source_ids or [],
                    "insufficientContext": insufficient_context,
                }
            )
        }


if __name__ == "__main__":
    unittest.main()
