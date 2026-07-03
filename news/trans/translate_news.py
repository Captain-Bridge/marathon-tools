from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import json
import os
import re
import time
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable
from urllib import error, request

import yaml
from bs4 import BeautifulSoup

SCRIPT_DIR = Path(__file__).resolve().parent
NEWS_ROOT = SCRIPT_DIR.parent
MARATHON_TOOLS_ROOT = NEWS_ROOT.parent
LOCAL_TRANS_ROOT = MARATHON_TOOLS_ROOT / "trans"
if str(MARATHON_TOOLS_ROOT) not in sys.path:
    sys.path.insert(0, str(MARATHON_TOOLS_ROOT))

try:
    from trans import (
        DEFAULT_MODEL_NAME,
        GoogleTranslateWebTranslator,
        LocalTransformersTranslator,
        LocalTranslationError,
        WebTranslationError,
    )
except Exception:  # pragma: no cover - fallback import handled at runtime
    DEFAULT_MODEL_NAME = "Helsinki-NLP/opus-mt-en-zh"

    class LocalTranslationError(RuntimeError):
        pass

    LocalTransformersTranslator = None  # type: ignore[assignment]
    GoogleTranslateWebTranslator = None  # type: ignore[assignment]

    class WebTranslationError(RuntimeError):
        pass


DEFAULT_SOURCE_ROOT = Path(r"C:\codes\myblog\source\news\articles")
DEFAULT_OUTPUT_ROOT = Path("output")
DEFAULT_GLOSSARY_PATH = Path("glossary.yaml")
DEFAULT_ALIGNMENT_GLOSSARY_PATH = Path("alignment_glossary.yaml")
DEFAULT_DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEFAULT_DEEPSEEK_MODEL = "deepseek-v4-flash"
DEFAULT_LOCAL_CONFIG_PATH = SCRIPT_DIR / "local_config.json"
VOID_TAGS = ("area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr")
TRANSLATABLE_TAGS = {
    "p",
    "li",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "figcaption",
    "blockquote",
    "td",
    "th",
}
SYSTEM_PROMPT = """You are a professional video game news translator.
Translate English article snippets into natural Simplified Chinese.

Rules:
1. Preserve every HTML tag, attribute, URL, and overall structure exactly.
2. Only translate visible human-readable text content.
3. Keep placeholder tokens such as [[TERM_0001]] unchanged.
4. Do not add explanations, notes, or extra markup.
5. Return valid JSON only, in the shape {"translations":["...", "..."]}.
6. The number and order of translations must exactly match the inputs.
"""
GENERIC_GLOSSARY_TERMS = {
    "Combat",
    "Contracts",
    "Developer Note",
    "Developer Note:",
    "DEVELOPER NOTE",
    "DEVELOPER NOTE:",
    "Equipment",
    "General",
    "Implants",
    "Item Economy",
    "Localization",
    "Mods",
    "New",
    "Progression",
    "Runners",
    "Stability",
    "User Interface and Experience",
    "Weapons",
    "Zones",
}
YAML_EXCLUDED_PREFIXES = (
    "Announcing ",
    "Check ",
    "Complete ",
    "Customize ",
    "Discover",
    "Getting ",
    "Going ",
    "How to ",
    "Introducing ",
    "Looking Ahead",
    "More to come",
    "New ",
    "Open Play Week Begins",
    "Prepare ",
    "Reach ",
    "Season ",
    "Welcome ",
)


@dataclass(slots=True)
class GlossaryEntry:
    source: str
    target: str
    strategy: str = "force"
    aliases: list[str] = field(default_factory=list)
    case_sensitive: bool = False
    notes: str = ""

    def terms(self) -> list[str]:
        values = [self.source, *self.aliases]
        unique: list[str] = []
        seen: set[str] = set()
        for value in values:
            if value not in seen:
                seen.add(value)
                unique.append(value)
        return unique

    def replacement(self) -> str:
        return self.source if self.strategy == "preserve" else self.target


class Glossary:
    def __init__(self, entries: Iterable[GlossaryEntry]):
        self.entries = list(entries)

    @classmethod
    def load(cls, path: Path) -> "Glossary":
        if not path.exists():
            return cls([])

        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        raw_entries = data.get("entries", [])
        entries: list[GlossaryEntry] = []
        for index, item in enumerate(raw_entries, start=1):
            if not isinstance(item, dict):
                raise ValueError(f"Glossary entry #{index} is not a mapping.")

            source = str(item.get("source", "")).strip()
            target = str(item.get("target", "")).strip()
            strategy = str(item.get("strategy", "force")).strip() or "force"
            aliases = item.get("aliases", []) or []
            case_sensitive = bool(item.get("case_sensitive", False))
            notes = str(item.get("notes", "")).strip()

            if not source:
                raise ValueError(f"Glossary entry #{index} is missing source.")
            if strategy not in {"force", "preserve"}:
                raise ValueError(
                    f"Glossary entry #{index} has invalid strategy: {strategy!r}."
                )
            if strategy == "force" and not target:
                raise ValueError(
                    f"Glossary entry #{index} must set target when strategy=force."
                )
            if strategy == "preserve" and not target:
                target = source

            entries.append(
                GlossaryEntry(
                    source=source,
                    target=target,
                    strategy=strategy,
                    aliases=[str(alias) for alias in aliases],
                    case_sensitive=case_sensitive,
                    notes=notes,
                )
            )

        return cls(entries)

    @classmethod
    def load_many(cls, paths: Iterable[Path]) -> "Glossary":
        combined: list[GlossaryEntry] = []
        for path in paths:
            if path is None:
                continue
            combined.extend(cls.load(path).entries)
        return cls(combined)

    def protect(self, text: str) -> tuple[str, dict[str, str]]:
        protected = text
        placeholders: dict[str, str] = {}
        term_index = 0

        for entry in sorted(self.entries, key=lambda item: len(item.source), reverse=True):
            for term in sorted(entry.terms(), key=len, reverse=True):
                flags = 0 if entry.case_sensitive else re.IGNORECASE
                pattern = re.compile(re.escape(term), flags)

                def replacer(match: re.Match[str]) -> str:
                    nonlocal term_index
                    token = f"[[TERM_{term_index:04d}]]"
                    term_index += 1
                    placeholders[token] = entry.replacement()
                    return token

                protected = pattern.sub(replacer, protected)

        return protected, placeholders

    def restore(self, text: str, placeholders: dict[str, str]) -> str:
        restored = text
        for token, value in placeholders.items():
            restored = restored.replace(token, value)
        return restored


class TranslatorError(RuntimeError):
    pass


class BaseTranslator:
    def translate_batch(self, snippets: list[str]) -> list[str]:
        raise NotImplementedError


class MockTranslator(BaseTranslator):
    def translate_batch(self, snippets: list[str]) -> list[str]:
        return [snippet for snippet in snippets]


class OpenAICompatibleTranslator(BaseTranslator):
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str = "https://api.openai.com/v1",
        timeout: int = 120,
    ):
        if not api_key:
            raise TranslatorError("Missing API key.")
        if not model:
            raise TranslatorError("Missing model name.")

        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def translate_batch(self, snippets: list[str]) -> list[str]:
        return self._translate_with_retry(snippets)

    def _request_translations(self, snippets: list[str]) -> list[str]:
        payload = {
            "model": self.model,
            "temperature": 0.2,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "target_language": "Simplified Chinese",
                            "translations": snippets,
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
        }
        url = f"{self.base_url}/chat/completions"
        req = request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        try:
            with request.urlopen(req, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8")
        except error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise TranslatorError(
                f"Translation API returned HTTP {exc.code}: {body}"
            ) from exc
        except error.URLError as exc:
            raise TranslatorError(f"Translation API request failed: {exc}") from exc

        try:
            data = json.loads(raw)
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, json.JSONDecodeError) as exc:
            raise TranslatorError(f"Invalid API response: {raw}") from exc

        parsed = parse_json_object(content)
        translated = parsed.get("translations")
        if not isinstance(translated, list):
            raise TranslatorError(f"Model response missing translations list: {content}")
        if len(translated) != len(snippets):
            raise TranslatorError(
                f"Model returned {len(translated)} items, expected {len(snippets)}."
            )
        return [str(item) for item in translated]

    def _translate_with_retry(self, snippets: list[str], depth: int = 0) -> list[str]:
        try:
            return self._request_translations(snippets)
        except TranslatorError as exc:
            if len(snippets) == 1:
                raise

            if depth >= 3:
                raise

            midpoint = max(1, len(snippets) // 2)
            time.sleep(min(1.5 * (depth + 1), 4.0))
            left = self._translate_with_retry(snippets[:midpoint], depth + 1)
            right = self._translate_with_retry(snippets[midpoint:], depth + 1)
            return left + right


def load_local_config(path: Path = DEFAULT_LOCAL_CONFIG_PATH) -> dict[str, object]:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in local config: {path}") from exc

    if not isinstance(parsed, dict):
        raise ValueError(f"Local config must be a JSON object: {path}")
    return parsed


def resolve_config_value(
    local_config: dict[str, object],
    env_name: str,
    config_path: tuple[str, ...],
    default: str = "",
) -> str:
    env_value = os.getenv(env_name, "").strip()
    if env_value:
        return env_value

    current: object = local_config
    for key in config_path:
        if not isinstance(current, dict):
            current = None
            break
        current = current.get(key)

    if isinstance(current, str) and current.strip():
        return current.strip()
    return default


def build_translator(args: argparse.Namespace) -> BaseTranslator:
    local_config = load_local_config()

    if args.provider == "mock":
        return MockTranslator()

    if args.provider == "deepseek":
        api_key = args.api_key or resolve_config_value(
            local_config,
            "DEEPSEEK_API_KEY",
            ("deepseek", "api_key"),
        )
        model = args.model or resolve_config_value(
            local_config,
            "DEEPSEEK_MODEL",
            ("deepseek", "model"),
            DEFAULT_DEEPSEEK_MODEL,
        )
        base_url = args.base_url or resolve_config_value(
            local_config,
            "DEEPSEEK_BASE_URL",
            ("deepseek", "base_url"),
            DEFAULT_DEEPSEEK_BASE_URL,
        )
        return OpenAICompatibleTranslator(
            api_key=api_key,
            model=model,
            base_url=base_url,
            timeout=args.timeout,
        )

    if args.provider == "local":
        if LocalTransformersTranslator is None:
            raise LocalTranslationError(
                "Local translator module is unavailable. "
                f"Expected reusable runtime under {LOCAL_TRANS_ROOT}."
            )
        model_name = args.local_model or os.getenv("MARATHON_TRANSLATION_MODEL", DEFAULT_MODEL_NAME)
        return LocalTransformersTranslator(
            model_name=model_name,
            device=args.local_device,
        )

    if args.provider == "web":
        if GoogleTranslateWebTranslator is None:
            raise WebTranslationError(
                "Web fallback translator module is unavailable. "
                f"Expected reusable runtime under {LOCAL_TRANS_ROOT}."
            )
        return GoogleTranslateWebTranslator()

    api_key = args.api_key or resolve_config_value(
        local_config,
        "OPENAI_API_KEY",
        ("openai", "api_key"),
    )
    if api_key:
        model = args.model or resolve_config_value(
            local_config,
            "OPENAI_MODEL",
            ("openai", "model"),
        )
        base_url = args.base_url or resolve_config_value(
            local_config,
            "OPENAI_BASE_URL",
            ("openai", "base_url"),
            "https://api.openai.com/v1",
        )
        return OpenAICompatibleTranslator(
            api_key=api_key,
            model=model,
            base_url=base_url,
            timeout=args.timeout,
        )

    deepseek_api_key = resolve_config_value(
        local_config,
        "DEEPSEEK_API_KEY",
        ("deepseek", "api_key"),
    )
    if deepseek_api_key:
        model = args.model or resolve_config_value(
            local_config,
            "DEEPSEEK_MODEL",
            ("deepseek", "model"),
            DEFAULT_DEEPSEEK_MODEL,
        )
        base_url = args.base_url or resolve_config_value(
            local_config,
            "DEEPSEEK_BASE_URL",
            ("deepseek", "base_url"),
            DEFAULT_DEEPSEEK_BASE_URL,
        )
        return OpenAICompatibleTranslator(
            api_key=deepseek_api_key,
            model=model,
            base_url=base_url,
            timeout=args.timeout,
        )

    if GoogleTranslateWebTranslator is not None:
        return GoogleTranslateWebTranslator()

    local_error: Exception | None = None
    if LocalTransformersTranslator is not None:
        try:
            model_name = args.local_model or os.getenv("MARATHON_TRANSLATION_MODEL", DEFAULT_MODEL_NAME)
            translator = LocalTransformersTranslator(
                model_name=model_name,
                device=args.local_device,
            )
            translator._load_runtime()
            return translator
        except Exception as exc:  # pragma: no cover - instantiated fallback
            local_error = exc

    error_parts = [
        "OPENAI_API_KEY is missing and no local translation fallback is available.",
        f"Install fallback dependencies from {LOCAL_TRANS_ROOT / 'requirements.txt'}.",
    ]
    if local_error is not None:
        error_parts.append(f"Local translator initialization failed: {local_error}")
    raise LocalTranslationError(" ".join(error_parts))


def parse_json_object(text: str) -> dict:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*|\s*```$", "", stripped, flags=re.DOTALL)

    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", stripped, flags=re.DOTALL)
        if not match:
            raise TranslatorError(f"Could not parse model JSON: {text}")
        parsed = json.loads(match.group(0))

    if not isinstance(parsed, dict):
        raise TranslatorError(f"Model JSON must be an object: {text}")
    return parsed


def split_front_matter(raw_text: str) -> tuple[str, str]:
    if raw_text.startswith("---\n"):
        match = re.match(r"(?s)\A---\n.*?\n---\n", raw_text)
        if match:
            return match.group(0), raw_text[match.end() :]
    return "", raw_text


def find_story_rendered(soup: BeautifulSoup):
    section = soup.select_one(".story-rendered")
    if section is None:
        raise ValueError("Could not find .story-rendered in HTML.")
    return section


def gather_translatable_blocks(section) -> list:
    blocks = []
    for element in section.find_all(TRANSLATABLE_TAGS):
        text = element.get_text(" ", strip=True)
        if not re.search(r"[A-Za-z]", text):
            continue
        blocks.append(element)
    return blocks


def chunk_list(items: list, size: int) -> Iterable[list]:
    for index in range(0, len(items), size):
        yield items[index : index + size]


def parse_fragment(fragment_html: str):
    fragment_soup = BeautifulSoup(fragment_html, "html.parser")
    root = next((node for node in fragment_soup.contents if getattr(node, "name", None)), None)
    if root is None:
        raise TranslatorError(f"Translated fragment is empty: {fragment_html!r}")
    return root


def replace_element_with_html(element, fragment_html: str) -> None:
    new_element = parse_fragment(fragment_html)
    element.replace_with(new_element)


def sanitize_output_html(html: str) -> str:
    sanitized = html
    for tag in VOID_TAGS:
        sanitized = re.sub(rf"</{tag}>", "", sanitized, flags=re.IGNORECASE)
    return sanitized


def translate_text_value(
    value: str,
    *,
    translator: BaseTranslator,
    glossary: Glossary,
) -> str:
    text = str(value or "").strip()
    if not text or not re.search(r"[A-Za-z]", text):
        return text

    protected_text, placeholders = glossary.protect(text)
    translated = translator.translate_batch([protected_text])[0]
    return glossary.restore(str(translated).strip(), placeholders)


def translate_html_fragment(
    html: str,
    *,
    translator: BaseTranslator,
    glossary: Glossary,
    chunk_size: int,
) -> tuple[str, int]:
    raw_html = str(html or "").strip()
    if not raw_html:
        return "", 0

    soup = BeautifulSoup(f"<div data-root='1'>{raw_html}</div>", "html.parser")
    root = soup.select_one("div[data-root='1']")
    if root is None:
        raise TranslatorError("Could not create translation root for HTML fragment.")

    blocks = gather_translatable_blocks(root)
    if not blocks:
        rendered = "".join(str(child) for child in root.contents)
        return sanitize_output_html(rendered), 0

    payloads: list[tuple[object, dict[str, str], str]] = []
    for block in blocks:
        original_html = str(block)
        protected_html, placeholders = glossary.protect(original_html)
        payloads.append((block, placeholders, protected_html))

    for batch in chunk_list(payloads, chunk_size):
        batch_inputs = [item[2] for item in batch]
        batch_outputs = translator.translate_batch(batch_inputs)
        for (original_block, placeholders, _), translated_html in zip(batch, batch_outputs):
            restored_html = glossary.restore(translated_html, placeholders)
            replace_element_with_html(original_block, restored_html)

    rendered = "".join(str(child) for child in root.contents)
    return sanitize_output_html(rendered), len(blocks)


def discover_article_paths(source_root: Path, article_filters: list[str]) -> list[Path]:
    candidates = sorted(source_root.glob("*/index.html"))
    if not article_filters:
        return candidates

    allowed = {value.lower() for value in article_filters}
    return [path for path in candidates if path.parent.name.lower() in allowed]


def translate_article(
    article_path: Path,
    *,
    translator: BaseTranslator,
    glossary: Glossary,
    chunk_size: int,
) -> tuple[str, int]:
    raw_text = article_path.read_text(encoding="utf-8")
    front_matter, html = split_front_matter(raw_text)
    soup = BeautifulSoup(html, "html.parser")
    section = find_story_rendered(soup)
    blocks = gather_translatable_blocks(section)

    if not blocks:
        return front_matter + sanitize_output_html(soup.decode(formatter="minimal")), 0

    payloads: list[tuple[object, dict[str, str], str]] = []
    for block in blocks:
        original_html = str(block)
        protected_html, placeholders = glossary.protect(original_html)
        payloads.append((block, placeholders, protected_html))

    for batch in chunk_list(payloads, chunk_size):
        batch_inputs = [item[2] for item in batch]
        batch_outputs = translator.translate_batch(batch_inputs)
        for (original_block, placeholders, _), translated_html in zip(batch, batch_outputs):
            restored_html = glossary.restore(translated_html, placeholders)
            replace_element_with_html(original_block, restored_html)

    return front_matter + sanitize_output_html(soup.decode(formatter="minimal")), len(blocks)


def write_output(
    *,
    article_path: Path,
    translated_html: str,
    source_root: Path,
    output_root: Path | None,
    in_place: bool,
    backup_suffix: str | None,
) -> Path:
    if in_place:
        destination = article_path
        if backup_suffix:
            backup_path = article_path.with_name(article_path.name + backup_suffix)
            if not backup_path.exists():
                backup_path.write_text(article_path.read_text(encoding="utf-8"), encoding="utf-8")
        destination.write_text(translated_html, encoding="utf-8")
        return destination

    if output_root is None:
        raise ValueError("output_root is required when not writing in place.")

    relative_path = article_path.relative_to(source_root)
    destination = output_root / relative_path
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(translated_html, encoding="utf-8")
    return destination


def normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def collect_story_text(article_path: Path) -> str:
    raw_text = article_path.read_text(encoding="utf-8")
    _, html = split_front_matter(raw_text)
    soup = BeautifulSoup(html, "html.parser")
    section = find_story_rendered(soup)
    return normalize_space(section.get_text(" ", strip=True))


def extract_term_candidates(text: str) -> list[tuple[str, str]]:
    patterns = [
        ("acronym", re.compile(r"\b[A-Z]{2,}(?:[A-Z0-9-]*[A-Z0-9])?\b")),
        (
            "phrase",
            re.compile(
                r"\b(?:[A-Z][a-zA-Z0-9'/-]+|[A-Z]{2,}[A-Z0-9-]*)"
                r"(?:\s+(?:[A-Z][a-zA-Z0-9'/-]+|[A-Z]{2,}[A-Z0-9-]*|[IVXLC]+|\d+))*"
            ),
        ),
        ("mixed", re.compile(r"\b[A-Za-z]+[A-Z0-9][A-Za-z0-9-]*\b")),
    ]

    ignored = {
        "The",
        "This",
        "That",
        "These",
        "Those",
        "Today",
        "Here",
        "During",
        "Season",
        "Marathon",
        "English",
        "Chinese",
        "Steam",
        "PlayStation",
        "Xbox",
    }

    results: list[tuple[str, str]] = []
    for kind, pattern in patterns:
        for match in pattern.finditer(text):
            term = match.group(0).strip(" .,:;!?()[]{}\"'")
            if len(term) < 3:
                continue
            if term in ignored:
                continue
            if " " not in term and term.istitle():
                continue
            results.append((term, kind))
    return results


def normalize_text(value: str) -> str:
    value = value.replace("\xa0", " ")
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def alignment_node_texts(html: str) -> list[tuple[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    return [
        (tag.name, normalize_text(tag.get_text(" ", strip=True)))
        for tag in soup.find_all(["h1", "h2", "h3", "strong"])
    ]


def is_good_alignment_pair(source: str, target: str) -> bool:
    if not source or not target:
        return False
    if len(source) > 80 or len(target) > 40:
        return False
    if sum(char.isalpha() for char in source) < 3:
        return False
    return True


def looks_like_glossary_term(source: str) -> bool:
    if source in GENERIC_GLOSSARY_TERMS:
        return False
    if re.search(r"\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\b", source):
        return False
    if re.search(r"\b\d{1,2}(?::\d{2})?\s*(?:AM|PM)\b", source):
        return False
    if len(source.split()) > 8:
        return False
    if re.search(r"\b[A-Z]{2,}[A-Z0-9_.-]*\b", source):
        return True
    if any(char.isdigit() for char in source):
        return True
    if "(" in source and ")" in source:
        return True
    keywords = (
        "Archive",
        "Armory",
        "Battle",
        "Chip",
        "Codex",
        "Contract",
        "Cradle",
        "Crew",
        "Edition",
        "Faction",
        "Kit",
        "Marsh",
        "Mode",
        "Outpost",
        "Perimeter",
        "Pistol",
        "Queue",
        "Rifle",
        "Runner",
        "Scope",
        "Shell",
        "System",
        "UESC",
        "Vault",
    )
    if any(keyword in source for keyword in keywords):
        return True
    return bool(re.fullmatch(r"(?:[A-Z][a-zA-Z0-9'/-]+)(?:\s+[A-Z][a-zA-Z0-9()'/-]+){0,5}", source))


def should_preserve_term(source: str, target: str) -> bool:
    if source == target:
        return True
    source_compact = re.sub(r"[\s\-_]+", "", source).lower()
    target_compact = re.sub(r"[\s\-_]+", "", target).lower()
    if source_compact and source_compact == target_compact:
        return True
    if re.fullmatch(r"[A-Z0-9_.-]+", source) and target == source:
        return True
    if source in {"Steam", "PlayStation 5", "Xbox Series X|S", "NIGHTFALL"} and target == source:
        return True
    return False


def is_noise_term(source: str) -> bool:
    if re.fullmatch(r"\+?\s*\d+\s+more!?", source, flags=re.IGNORECASE):
        return True
    if re.fullmatch(r"\([^)]*\)", source):
        return True
    if source.startswith("+ "):
        return True
    if source.endswith("!") or source.endswith("?"):
        return True
    if source in {"New", "FIRST STEP"}:
        return True
    return False


def is_yaml_recommendable(
    *,
    source: str,
    target: str,
    glossary_like: bool,
    ambiguous: bool,
    occurrences: int,
    origins: set[str],
) -> bool:
    if not glossary_like or ambiguous:
        return False
    if is_noise_term(source):
        return False
    if len(source.split()) > 6:
        return False
    if any(source.startswith(prefix) for prefix in YAML_EXCLUDED_PREFIXES):
        return False
    if re.search(r"\b(?:Begins|Returns|Launches|Releases)\b", source):
        return False
    if re.search(r"\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\b", source):
        return False
    if not (occurrences >= 2 or {"strong", "h3"} & origins):
        return False
    if source == target and not re.search(r"\b[A-Z]{2,}[A-Z0-9_.-]*\b", source):
        return False
    if len(target) <= 1:
        return False
    return True


def command_alignment_glossary(args: argparse.Namespace) -> int:
    alignment_path: Path = args.alignment
    data = json.loads(alignment_path.read_text(encoding="utf-8"))
    pairs = data.get("pairs", [])
    if not isinstance(pairs, list) or not pairs:
        raise ValueError("Alignment JSON does not contain any bilingual pairs.")

    records: dict[tuple[str, str], dict[str, object]] = {}
    targets_by_source: dict[str, set[str]] = {}

    def add_record(
        source: str,
        target: str,
        *,
        origin: str,
        article_path: str,
    ) -> None:
        if not is_good_alignment_pair(source, target):
            return

        key = (source, target)
        record = records.setdefault(
            key,
            {
                "source": source,
                "target": target,
                "occurrences": 0,
                "origins": set(),
                "articles": set(),
            },
        )
        record["occurrences"] = int(record["occurrences"]) + 1
        record["origins"].add(origin)
        record["articles"].add(article_path)
        targets_by_source.setdefault(source, set()).add(target)

    for pair in pairs:
        article_path = str(pair.get("paths", {}).get("enUs", ""))

        title_en = normalize_text(str(pair.get("title", {}).get("enUs", "")))
        title_zh = normalize_text(str(pair.get("title", {}).get("zhChs", "")))
        add_record(title_en, title_zh, origin="title", article_path=article_path)

        subtitle_en = normalize_text(str(pair.get("subtitle", {}).get("enUs", "")))
        subtitle_zh = normalize_text(str(pair.get("subtitle", {}).get("zhChs", "")))
        add_record(subtitle_en, subtitle_zh, origin="subtitle", article_path=article_path)

        en_nodes = alignment_node_texts(str(pair.get("bodyHtml", {}).get("enUs", "")))
        zh_nodes = alignment_node_texts(str(pair.get("bodyHtml", {}).get("zhChs", "")))
        for (en_tag, en_text), (zh_tag, zh_text) in zip(en_nodes, zh_nodes):
            if en_tag != zh_tag:
                continue
            add_record(en_text, zh_text, origin=en_tag, article_path=article_path)

    csv_rows: list[dict[str, object]] = []
    yaml_entries: list[dict[str, object]] = []
    for record in records.values():
        source = str(record["source"])
        target = str(record["target"])
        ambiguous_targets = sorted(targets_by_source.get(source, set()))
        ambiguous = len(ambiguous_targets) > 1
        glossary_like = looks_like_glossary_term(source)
        preserve = should_preserve_term(source, target)
        origins = set(record["origins"])
        occurrences = int(record["occurrences"])
        recommended = is_yaml_recommendable(
            source=source,
            target=target,
            glossary_like=glossary_like,
            ambiguous=ambiguous,
            occurrences=occurrences,
            origins=origins,
        )
        csv_rows.append(
            {
                "source": source,
                "target": target,
                "occurrences": occurrences,
                "origin_count": len(origins),
                "origins": "|".join(sorted(origins)),
                "article_count": len(record["articles"]),
                "glossary_like": "yes" if glossary_like else "no",
                "ambiguous": "yes" if ambiguous else "no",
                "ambiguous_targets": " | ".join(ambiguous_targets),
                "recommended_strategy": "preserve" if preserve else "force",
                "recommended_for_yaml": "yes" if recommended else "no",
            }
        )

        if recommended:
            yaml_entries.append(
                {
                    "source": source,
                    "target": target,
                    "strategy": "preserve" if preserve else "force",
                    "aliases": [],
                    "case_sensitive": False if not re.fullmatch(r"[A-Z0-9_.-]+", source) else True,
                    "notes": f"Imported from alignment sample; occurrences={occurrences}",
                }
            )

    csv_rows.sort(
        key=lambda row: (
            row["recommended_for_yaml"] != "yes",
            row["ambiguous"] == "yes",
            -int(row["occurrences"]),
            str(row["source"]).lower(),
        )
    )
    yaml_entries.sort(key=lambda item: item["source"].lower())

    args.csv_output.parent.mkdir(parents=True, exist_ok=True)
    with args.csv_output.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "source",
                "target",
                "occurrences",
                "origin_count",
                "origins",
                "article_count",
                "glossary_like",
                "ambiguous",
                "ambiguous_targets",
                "recommended_strategy",
                "recommended_for_yaml",
            ],
        )
        writer.writeheader()
        writer.writerows(csv_rows)

    yaml_payload = {
        "version": 1,
        "source": str(alignment_path),
        "pair_count": len(pairs),
        "generated_entries": len(yaml_entries),
        "entries": yaml_entries,
    }
    args.yaml_output.parent.mkdir(parents=True, exist_ok=True)
    args.yaml_output.write_text(
        yaml.safe_dump(yaml_payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )

    print(f"[written] {args.csv_output}")
    print(f"[written] {args.yaml_output}")
    print(
        "Summary: "
        f"{len(csv_rows)} candidate pair(s), "
        f"{len(yaml_entries)} YAML-ready entry(ies)."
    )
    return 0


def command_translate(args: argparse.Namespace) -> int:
    glossary = Glossary.load(args.glossary)
    article_paths = discover_article_paths(args.source_root, args.article)

    if not article_paths:
        print("No articles matched the requested filters.", file=sys.stderr)
        return 1

    if args.limit is not None:
        article_paths = article_paths[: args.limit]

    translator = build_translator(args)

    translated_files = 0
    translated_blocks = 0
    for article_path in article_paths:
        translated_html, block_count = translate_article(
            article_path,
            translator=translator,
            glossary=glossary,
            chunk_size=args.chunk_size,
        )
        destination = write_output(
            article_path=article_path,
            translated_html=translated_html,
            source_root=args.source_root,
            output_root=args.output_root,
            in_place=args.in_place,
            backup_suffix=args.backup_suffix,
        )
        translated_files += 1
        translated_blocks += block_count
        print(f"[translated] {article_path.parent.name} -> {destination}")

    print(
        f"Completed: {translated_files} file(s), {translated_blocks} translated block(s)."
    )
    return 0


def build_bilingual_translation_output(
    *,
    input_path: Path,
    translated_items: list[dict[str, object]],
) -> dict[str, object]:
    return {
        "generatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "source": str(input_path),
        "count": len(translated_items),
        "items": translated_items,
    }


def command_translate_bilingual_json(args: argparse.Namespace) -> int:
    glossary = Glossary.load_many([args.glossary, args.alignment_glossary])
    data = json.loads(args.input.read_text(encoding="utf-8"))
    items = data.get("items", [])
    if not isinstance(items, list):
        raise ValueError("Input bilingual JSON is missing items[].")

    candidates = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if item.get("zhChs"):
            continue
        en_us = item.get("enUs")
        if not isinstance(en_us, dict):
            continue
        candidates.append(item)

    if args.limit is not None:
        candidates = candidates[: args.limit]

    if not candidates:
        output = build_bilingual_translation_output(
            input_path=args.input,
            translated_items=[],
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(output, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"[written] {args.output}")
        print("Completed: 0 bilingual item(s) required generated Chinese content.")
        return 0

    translator = build_translator(args)

    translated_items: list[dict[str, object]] = []
    translated_blocks = 0
    for item in candidates:
        en_us = item["enUs"]
        translated_body_html, block_count = translate_html_fragment(
            str(en_us.get("bodyHtml", "")),
            translator=translator,
            glossary=glossary,
            chunk_size=args.chunk_size,
        )
        translated_blocks += block_count
        translated_body_text = normalize_space(
            BeautifulSoup(translated_body_html, "html.parser").get_text(" ", strip=True)
        )
        translated_title = translate_text_value(
            str(en_us.get("title", "")),
            translator=translator,
            glossary=glossary,
        )
        translated_subtitle = translate_text_value(
            str(en_us.get("subtitle", "")),
            translator=translator,
            glossary=glossary,
        )

        translated_items.append(
            {
                "contentId": item.get("contentId"),
                "articlePath": item.get("articlePath"),
                "articleUrl": {
                    "enUs": item.get("articleUrl", {}).get("enUs"),
                    "zhChs": None,
                },
                "source": "generated",
                "zhChs": {
                    "title": translated_title,
                    "subtitle": translated_subtitle,
                    "bodyHtml": translated_body_html,
                    "bodyText": translated_body_text,
                },
            }
        )
        print(f"[translated] {item.get('articlePath')}")

    output = build_bilingual_translation_output(
        input_path=args.input,
        translated_items=translated_items,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"[written] {args.output}")
    print(
        f"Completed: {len(translated_items)} bilingual item(s), "
        f"{translated_blocks} translated block(s)."
    )
    return 0


def command_extract_terms(args: argparse.Namespace) -> int:
    article_paths = discover_article_paths(args.source_root, args.article)
    if not article_paths:
        print("No articles matched the requested filters.", file=sys.stderr)
        return 1

    if args.limit is not None:
        article_paths = article_paths[: args.limit]

    counts: dict[str, dict[str, object]] = {}
    for article_path in article_paths:
        text = collect_story_text(article_path)
        for term, kind in extract_term_candidates(text):
            record = counts.setdefault(
                term,
                {
                    "term": term,
                    "kind": kind,
                    "count": 0,
                    "articles": set(),
                },
            )
            record["count"] = int(record["count"]) + 1
            record["articles"].add(article_path.parent.name)

    candidates = sorted(
        counts.values(),
        key=lambda item: (-int(item["count"]), str(item["term"]).lower()),
    )

    output = {
        "source_root": str(args.source_root),
        "article_count": len(article_paths),
        "candidate_count": len(candidates),
        "candidates": [
            {
                "term": item["term"],
                "kind": item["kind"],
                "count": item["count"],
                "articles": sorted(item["articles"]),
            }
            for item in candidates[: args.top]
        ],
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        yaml.safe_dump(output, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    print(f"[written] {args.output}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Translate Marathon News article bodies and manage terminology."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    translate_parser = subparsers.add_parser(
        "translate",
        help="Translate article body HTML under .story-rendered.",
    )
    translate_parser.add_argument(
        "--source-root",
        type=Path,
        default=DEFAULT_SOURCE_ROOT,
        help=f"Article root directory. Default: {DEFAULT_SOURCE_ROOT}",
    )
    translate_parser.add_argument(
        "--output-root",
        type=Path,
        default=DEFAULT_OUTPUT_ROOT,
        help="Output root when not using --in-place.",
    )
    translate_parser.add_argument(
        "--glossary",
        type=Path,
        default=DEFAULT_GLOSSARY_PATH,
        help="Glossary YAML path.",
    )
    translate_parser.add_argument(
        "--article",
        action="append",
        default=[],
        help="Only process the given article folder name. Repeatable.",
    )
    translate_parser.add_argument(
        "--limit",
        type=int,
        help="Only process the first N matched articles.",
    )
    translate_parser.add_argument(
        "--provider",
        choices=["openai-compatible", "deepseek", "local", "web", "mock"],
        default="openai-compatible",
        help="Translation backend.",
    )
    translate_parser.add_argument("--api-key", help="Override OPENAI_API_KEY.")
    translate_parser.add_argument("--model", help="Override model name for the selected remote provider.")
    translate_parser.add_argument("--base-url", help="Override base URL for the selected remote provider.")
    translate_parser.add_argument(
        "--local-model",
        help=f"Override local translation model. Default: {DEFAULT_MODEL_NAME}",
    )
    translate_parser.add_argument(
        "--local-device",
        choices=["cpu", "cuda"],
        help="Force local translation device.",
    )
    translate_parser.add_argument(
        "--timeout",
        type=int,
        default=120,
        help="HTTP timeout in seconds.",
    )
    translate_parser.add_argument(
        "--chunk-size",
        type=int,
        default=8,
        help="Number of HTML blocks per translation request.",
    )
    translate_parser.add_argument(
        "--in-place",
        action="store_true",
        help="Overwrite source articles directly.",
    )
    translate_parser.add_argument(
        "--backup-suffix",
        default=".bak",
        help="Backup suffix used with --in-place. Set empty string to disable.",
    )
    translate_parser.set_defaults(func=command_translate)

    extract_parser = subparsers.add_parser(
        "extract-terms",
        help="Scan article bodies and export term candidates to YAML.",
    )
    extract_parser.add_argument(
        "--source-root",
        type=Path,
        default=DEFAULT_SOURCE_ROOT,
        help=f"Article root directory. Default: {DEFAULT_SOURCE_ROOT}",
    )
    extract_parser.add_argument(
        "--article",
        action="append",
        default=[],
        help="Only scan the given article folder name. Repeatable.",
    )
    extract_parser.add_argument(
        "--limit",
        type=int,
        help="Only process the first N matched articles.",
    )
    extract_parser.add_argument(
        "--top",
        type=int,
        default=300,
        help="Maximum number of candidates to write.",
    )
    extract_parser.add_argument(
        "--output",
        type=Path,
        default=Path("term_candidates.yaml"),
        help="Output YAML file path.",
    )
    extract_parser.set_defaults(func=command_extract_terms)

    alignment_parser = subparsers.add_parser(
        "alignment-glossary",
        help="Build glossary candidates from bilingual alignment JSON.",
    )
    alignment_parser.add_argument(
        "--alignment",
        type=Path,
        default=Path(r"C:\codes\marathon_tools\news\sample-marathon-news-translation-alignment.json"),
        help="Alignment JSON path.",
    )
    alignment_parser.add_argument(
        "--csv-output",
        type=Path,
        default=Path("alignment_glossary_candidates.csv"),
        help="CSV output path.",
    )
    alignment_parser.add_argument(
        "--yaml-output",
        type=Path,
        default=Path("alignment_glossary.yaml"),
        help="YAML output path.",
    )
    alignment_parser.set_defaults(func=command_alignment_glossary)

    bilingual_parser = subparsers.add_parser(
        "translate-bilingual-json",
        help="Generate Chinese content for bilingual JSON entries that lack official zh-chs content.",
    )
    bilingual_parser.add_argument(
        "--input",
        type=Path,
        default=Path(r"C:\codes\marathon_tools\news\sample-marathon-news-bilingual.json"),
        help="Bilingual JSON path.",
    )
    bilingual_parser.add_argument(
        "--output",
        type=Path,
        default=Path(r"C:\codes\marathon_tools\news\sample-marathon-news-zh-generated.json"),
        help="Output JSON path.",
    )
    bilingual_parser.add_argument(
        "--glossary",
        type=Path,
        default=DEFAULT_GLOSSARY_PATH,
        help="Primary glossary YAML path.",
    )
    bilingual_parser.add_argument(
        "--alignment-glossary",
        type=Path,
        default=DEFAULT_ALIGNMENT_GLOSSARY_PATH,
        help="Alignment-enhanced glossary YAML path.",
    )
    bilingual_parser.add_argument(
        "--limit",
        type=int,
        help="Only translate the first N missing-zh entries.",
    )
    bilingual_parser.add_argument(
        "--provider",
        choices=["openai-compatible", "deepseek", "local", "web", "mock"],
        default="openai-compatible",
        help="Translation backend.",
    )
    bilingual_parser.add_argument("--api-key", help="Override OPENAI_API_KEY.")
    bilingual_parser.add_argument("--model", help="Override model name for the selected remote provider.")
    bilingual_parser.add_argument("--base-url", help="Override base URL for the selected remote provider.")
    bilingual_parser.add_argument(
        "--local-model",
        help=f"Override local translation model. Default: {DEFAULT_MODEL_NAME}",
    )
    bilingual_parser.add_argument(
        "--local-device",
        choices=["cpu", "cuda"],
        help="Force local translation device.",
    )
    bilingual_parser.add_argument(
        "--timeout",
        type=int,
        default=120,
        help="HTTP timeout in seconds.",
    )
    bilingual_parser.add_argument(
        "--chunk-size",
        type=int,
        default=8,
        help="Number of HTML blocks per translation request.",
    )
    bilingual_parser.set_defaults(func=command_translate_bilingual_json)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if hasattr(args, "backup_suffix") and args.backup_suffix == "":
        args.backup_suffix = None
    try:
        return args.func(args)
    except (TranslatorError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
