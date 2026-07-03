# Local Translation Runtime

This directory provides a reusable local translation fallback for tools under
`C:\codes\marathon_tools\`.

## Install

```powershell
pip install -r C:\codes\marathon_tools\trans\requirements.txt
```

`torch` is expected to be available already. If CUDA is present, the runtime
will use it automatically; otherwise it falls back to CPU.

## Default model

The default English to Simplified Chinese model is:

`Helsinki-NLP/opus-mt-en-zh`

You can override it with:

```powershell
$env:MARATHON_TRANSLATION_MODEL="Helsinki-NLP/opus-mt-en-zh"
```

## What it preserves

The runtime protects these items before translation and restores them after:

- HTML tags
- HTML entities
- URLs
- placeholder tokens such as `[[TERM_0001]]`

This makes it suitable as a fallback for the Marathon news HTML translation
pipeline.
