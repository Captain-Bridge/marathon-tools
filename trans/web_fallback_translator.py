from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from urllib import parse, request


MYMEMORY_URL = "https://api.mymemory.translated.net/get"
DEFAULT_CACHE_PATH = Path(r"C:\codes\marathon_tools\trans\translation_cache.json")
PLACEHOLDER_PATTERN = re.compile(
    r"(<[^>]+>|&[A-Za-z0-9#]+;|https?://[^\s<>\"]+|\[\[[A-Z0-9_]+\]\])"
)


class WebTranslationError(RuntimeError):
    pass


@dataclass(slots=True)
class PreparedSnippet:
    text: str
    placeholders: dict[str, str]


class GoogleTranslateWebTranslator:
    """HTTP fallback translator backed by public web translation endpoints.

    The class name is kept stable so existing imports do not need to change.
    """

    def __init__(
        self,
        *,
        source_language: str = "en",
        target_language: str = "zh-CN",
        timeout: int = 30,
        user_agent: str = "Mozilla/5.0 (Codex Marathon Translator)",
        max_query_chars: int = 450,
        cache_path: Path = DEFAULT_CACHE_PATH,
    ) -> None:
        self.source_language = source_language
        self.target_language = target_language
        self.timeout = timeout
        self.user_agent = user_agent
        self.max_query_chars = max_query_chars
        self.cache_path = cache_path
        self._cache = self._load_cache()

    def _load_cache(self) -> dict[str, str]:
        try:
            return json.loads(self.cache_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except Exception:
            return {}

    def _save_cache(self) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(
            json.dumps(self._cache, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _cache_key(self, text: str) -> str:
        payload = f"{self.source_language}|{self.target_language}|{text}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

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

    def _translate_via_mymemory(self, text: str) -> str:
        query = parse.urlencode(
            {
                "q": text,
                "langpair": f"{self.source_language}|{self.target_language}",
            }
        )
        req = request.Request(
            f"{MYMEMORY_URL}?{query}",
            headers={"User-Agent": self.user_agent},
            method="GET",
        )

        try:
            with request.urlopen(req, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8")
        except Exception as exc:
            raise WebTranslationError(f"MyMemory translation request failed: {exc}") from exc

        try:
            payload = json.loads(raw)
        except Exception as exc:
            raise WebTranslationError(f"Unexpected MyMemory response: {raw}") from exc

        translated = (
            payload.get("responseData", {}).get("translatedText")
            if isinstance(payload, dict)
            else None
        )
        if not translated:
            raise WebTranslationError(f"MyMemory returned no translation: {raw}")
        return str(translated)

    def _split_long_text(self, text: str) -> list[str]:
        compact = text.strip()
        if len(compact) <= self.max_query_chars:
            return [compact]

        parts: list[str] = []
        current = ""
        for piece in re.split(r"(\s+)", compact):
            candidate = f"{current}{piece}"
            if current and len(candidate) > self.max_query_chars:
                parts.append(current.strip())
                current = piece.lstrip()
            else:
                current = candidate

        if current.strip():
            parts.append(current.strip())

        normalized: list[str] = []
        for part in parts:
            if len(part) <= self.max_query_chars:
                normalized.append(part)
                continue

            start = 0
            while start < len(part):
                normalized.append(part[start : start + self.max_query_chars])
                start += self.max_query_chars

        return normalized

    def translate_batch(self, snippets: list[str]) -> list[str]:
        outputs: list[str] = []
        cache_changed = False
        for snippet in snippets:
            prepared = self._prepare_snippet(snippet)
            translated_chunks: list[str] = []
            for chunk in self._split_long_text(prepared.text):
                key = self._cache_key(chunk)
                cached = self._cache.get(key)
                if cached is None:
                    cached = self._translate_via_mymemory(chunk)
                    self._cache[key] = cached
                    cache_changed = True
                translated_chunks.append(cached)
            translated = "".join(translated_chunks)
            outputs.append(self._restore_snippet(translated, prepared.placeholders))
        if cache_changed:
            self._save_cache()
        return outputs
