# Providers

`python3 scripts/genai_calc.py --list-providers` is the source of truth; this
page adds the context for choosing one. Every provider's calc has the same
inputs (Prompts, Grounding, Select LLM, Grounding Token Count, Prompt) and the
same guards (empty grounding → instructions; over budget → "split by …";
missing key → how to store it; HTTP / TLS errors → readable text, never a
stack trace in the dashboard).

| Provider id | Wire format | Credential(s) (keystore names) | Endpoint / required flags | Model id in **Select LLM** |
|---|---|---|---|---|
| `gateway` | OpenAI `/chat/completions` | `gateway` (Bearer) | `--url` required | whatever the gateway exposes (`list_models.py`) |
| `openai` | OpenAI chat | `openai` | api.openai.com | `gpt-5`, `gpt-4.1`, … |
| `azure-openai` | OpenAI chat, `api-key` header | `azure-openai` | `--resource NAME`; Azure Gov: `--url https://NAME.openai.azure.us/openai/v1/chat/completions` | **deployment name** |
| `asksage` | OpenAI chat (Ask Sage compat route) | `asksage` (API key as Bearer) | api.asksage.ai; other tenants: `--url` | Ask Sage model names (`gpt-4o`, `claude-35-sonnet`, …; `list_models.py`) |
| `asksage-native` | `/server/query` | `asksage`, `asksage.email` | token exchange each call (`--token-url` to override) | same as above |
| `bedrock` | Converse REST | `bedrock` (Bedrock API key, Bearer) | `--region` (GovCloud `us-gov-west-1`) | model or inference-profile id, e.g. `us.anthropic.claude-sonnet-4-…-v1:0` |
| `bedrock-iam` | boto3 Converse | optional `bedrock.access_key_id`, `bedrock.secret_access_key`, `bedrock.session_token`; else host credential chain | `--region` | same as bedrock |
| `vertex` | Gemini `generateContent` | `vertex` (express-mode API key) | — | `gemini-2.5-pro`, `gemini-2.5-flash` |
| `vertex-sa` | OpenAI chat (Vertex OpenAPI endpoint) | `vertex.service_account` (JSON) | `--project`, `--location` | `google/gemini-2.5-pro` |
| `palantir` | Foundry LLM proxy (routes Anthropic vs OpenAI shape by model) | `palantir` (Foundry token) | `--host X.palantirfoundry.com` | usually a language-model RID |
| `anthropic` | Messages API | `anthropic` (`x-api-key`) | api.anthropic.com | `claude-sonnet-4-…`, `claude-opus-4-…` |

## Choosing for DoD / IC work

* **Ask Sage** — IL5/IL6-authorized SaaS used widely in DoD; prefer the
  OpenAI-compatible route (`asksage`). The native response shape
  (`asksage-native`) was implemented from public docs and is **not yet
  verified against a live tenant**; test with `test_workbook_calc.py --live`.
* **Bedrock GovCloud** — `bedrock --region us-gov-west-1` with a Bedrock API
  key, or `bedrock-iam` when TabPy runs on EC2 with an instance role (no key
  to store at all — the best option on AWS).
* **Palantir AIP** — when the data and users already live in Foundry.
* **GenAI.mil / internal LiteLLM / vLLM** — `gateway --url …` if the endpoint
  is OpenAI-compatible; usually needs `--tls` with the DoD PKI bundle
  (`ca_bundle.py --add DoD_PKE_CA_chain.pem`).
* **Vertex** — express-mode API key for demos; service account (`vertex-sa`)
  for real deployments; needs `google-auth` installed in the TabPy Python or
  `gcloud` on the TabPy host.

## Model list

`python3 scripts/list_models.py --provider P [--url …] [--tls …]` prints ids
and a ready-made `--models "X=X" …` argument for `patch_workbook.py`.
Bedrock / Vertex / Palantir: use the console's model-access page.

## Limits to set per provider

* `--max-grounding-tokens` (default 90000) — keep below the model's context
  minus output. The calc estimates tokens as `LEN/4`.
* `--max-output-tokens` — required by Anthropic (defaulted for you); optional
  elsewhere.
* `--timeout` (default 120 s) — must be ≤ TabPy's `TABPY_EVALUATE_TIMEOUT`
  and Tableau's analytics-extension timeout, or Tableau gives up first.
