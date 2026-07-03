# Marathon News Translator

`C:\codes\marathon_tools\news\trans` contains the translation pipeline used by
the Marathon news sync flow.

It is responsible for translating article body content while preserving HTML
structure, links, images, and placeholder tokens.

## Install

```powershell
pip install -r requirements.txt
```

## Recommended Provider

If the machine should not rely on local GPU inference, use DeepSeek API.

If you want to save keys in the code directory instead of typing environment
variables every time, create:

`C:\codes\marathon_tools\news\trans\local_config.json`

You can copy the template:

`C:\codes\marathon_tools\news\trans\local_config.example.json`

Supported environment variables:

```powershell
$env:DEEPSEEK_API_KEY="your-key"
$env:DEEPSEEK_MODEL="deepseek-v4-flash"
$env:DEEPSEEK_BASE_URL="https://api.deepseek.com"
```

DeepSeek is used through an OpenAI-compatible `chat/completions` interface.

You can also run it explicitly:

```powershell
python .\translate_news.py translate-bilingual-json --provider deepseek
```

## Other Providers

- `openai-compatible`: Uses `OPENAI_API_KEY`, `OPENAI_MODEL`, `OPENAI_BASE_URL`
- `deepseek`: Uses `DEEPSEEK_API_KEY`, `DEEPSEEK_MODEL`, `DEEPSEEK_BASE_URL`
- `web`: Uses the reusable fallback under `C:\codes\marathon_tools\trans`
- `local`: Tries local model inference
- `mock`: Dry-run pipeline without real translation

## Current Fallback Order

When the provider is not forced explicitly, the pipeline prefers:

1. `OPENAI_API_KEY`
2. `DEEPSEEK_API_KEY`
3. reusable web fallback under `C:\codes\marathon_tools\trans`
4. local model fallback

## Term Workflow

Before large-scale translation, you can scan and refine terminology:

```powershell
python .\translate_news.py extract-terms
python .\translate_news.py alignment-glossary
```

Important files:

- `glossary.yaml`
- `alignment_glossary.yaml`
- `alignment_glossary_candidates.csv`

## Common Commands

Translate one article:

```powershell
python .\translate_news.py translate --article welcome-season-2
```

Translate missing Chinese content in bilingual JSON:

```powershell
python .\translate_news.py translate-bilingual-json
```

Use DeepSeek explicitly:

```powershell
python .\translate_news.py translate-bilingual-json --provider deepseek
```

Use mock mode:

```powershell
python .\translate_news.py translate --provider mock --article welcome-season-2
```

## AMD Note

This machine uses an AMD GPU, so CUDA should not be assumed. For reliable
production translation, prefer DeepSeek API over local CUDA-based inference.
