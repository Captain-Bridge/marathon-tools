from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Iterable


DEFAULT_MODEL_NAME = "Helsinki-NLP/opus-mt-en-zh"
DEFAULT_MAX_NEW_TOKENS = 384
PLACEHOLDER_PATTERN = re.compile(
    r"(<[^>]+>|&[A-Za-z0-9#]+;|https?://[^\s<>\"]+|\[\[[A-Z0-9_]+\]\])"
)


class LocalTranslationError(RuntimeError):
    pass


@dataclass(slots=True)
class PreparedSnippet:
    text: str
    placeholders: dict[str, str]


class LocalTransformersTranslator:
    def __init__(
        self,
        *,
        model_name: str = DEFAULT_MODEL_NAME,
        device: str | None = None,
        max_new_tokens: int = DEFAULT_MAX_NEW_TOKENS,
    ) -> None:
        self.model_name = model_name
        self.device = device
        self.max_new_tokens = max_new_tokens
        self._tokenizer = None
        self._model = None
        self._torch = None

    def _load_runtime(self) -> None:
        if self._model is not None and self._tokenizer is not None and self._torch is not None:
            return

        try:
            import torch
            from transformers import AutoModelForSeq2SeqLM, AutoTokenizer
        except ImportError as exc:
            raise LocalTranslationError(
                "Local translation runtime is unavailable. "
                "Install dependencies from C:\\codes\\marathon_tools\\trans\\requirements.txt."
            ) from exc

        resolved_device = self.device
        if not resolved_device:
            resolved_device = "cuda" if torch.cuda.is_available() else "cpu"

        try:
            tokenizer = AutoTokenizer.from_pretrained(self.model_name)
            model = AutoModelForSeq2SeqLM.from_pretrained(self.model_name)
        except Exception as exc:
            raise LocalTranslationError(
                f"Unable to load local translation model {self.model_name!r}: {exc}"
            ) from exc

        if resolved_device == "cuda" and not torch.cuda.is_available():
            resolved_device = "cpu"

        model = model.to(resolved_device)
        model.eval()

        self.device = resolved_device
        self._tokenizer = tokenizer
        self._model = model
        self._torch = torch

    def _prepare_snippet(self, text: str) -> PreparedSnippet:
        placeholders: dict[str, str] = {}
        counter = 0

        def replacer(match: re.Match[str]) -> str:
            nonlocal counter
            token = f"zzkeep{counter:04d}zz"
            placeholders[token] = match.group(0)
            counter += 1
            return f" {token} "

        prepared = PLACEHOLDER_PATTERN.sub(replacer, text)
        prepared = re.sub(r"\s+", " ", prepared).strip()
        return PreparedSnippet(text=prepared, placeholders=placeholders)

    def _restore_snippet(self, text: str, placeholders: dict[str, str]) -> str:
        restored = text
        for token, value in placeholders.items():
            restored = re.sub(re.escape(token), value, restored, flags=re.IGNORECASE)

        restored = re.sub(r">\s+<", "><", restored)
        restored = re.sub(r"\s+</", "</", restored)
        restored = re.sub(r">\s+", ">", restored)
        restored = re.sub(r"\s{2,}", " ", restored)
        return restored.strip()

    def translate_batch(self, snippets: list[str]) -> list[str]:
        if not snippets:
            return []

        self._load_runtime()
        tokenizer = self._tokenizer
        model = self._model
        torch = self._torch
        if tokenizer is None or model is None or torch is None:
            raise LocalTranslationError("Local translation runtime did not initialize correctly.")

        prepared = [self._prepare_snippet(snippet) for snippet in snippets]
        texts = [item.text for item in prepared]

        try:
            encoded = tokenizer(
                texts,
                return_tensors="pt",
                padding=True,
                truncation=True,
                max_length=512,
            )
            encoded = {key: value.to(self.device) for key, value in encoded.items()}
            with torch.inference_mode():
                generated = model.generate(
                    **encoded,
                    max_new_tokens=self.max_new_tokens,
                    num_beams=4,
                )
            decoded = tokenizer.batch_decode(generated, skip_special_tokens=True)
        except Exception as exc:
            raise LocalTranslationError(f"Local translation request failed: {exc}") from exc

        return [
            self._restore_snippet(text, item.placeholders)
            for text, item in zip(decoded, prepared, strict=True)
        ]


def describe_local_runtime() -> str:
    model_name = os.getenv("MARATHON_TRANSLATION_MODEL", DEFAULT_MODEL_NAME)
    return (
        "Local translation fallback uses Hugging Face seq2seq models. "
        f"Default model: {model_name}."
    )
