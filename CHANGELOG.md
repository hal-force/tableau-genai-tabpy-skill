# Changelog

## v0.1.1-preview — 2026-10-27

Fixes from the code and security review of v0.1.0-preview.

Security
- `patch_workbook.py`: `.twbx` re-zip only includes files actually extracted
  under the temp dir (no zip-slip / absolute-path member pulling files from
  elsewhere, no symlinks); only a top-level `.twb` is patched.
- Credential detection is quote-agnostic and covers header dicts, `Bearer`
  literals, parameter values and common token formats; the patcher scans the
  whole output, and no credential value from the source may survive.
- Generated calc scrubs resolved credentials (and Ask Sage access tokens) from
  everything it returns, including provider error bodies.
- Template substitution is single-pass; secret / service names validated
  (`[A-Za-z0-9._-]{1,64}`) in `genai_calc.py` and `keystore.py`; split hint
  embedded with `repr()`.
- chmod 600 is enforced on existing files too (`.twbcalc`, patched outputs,
  secrets file); only the default `~/.tableau-genai` dir is chmodded;
  warning when `secrets.json` is group/other readable.
- `ca_bundle.py` skips System-keychain certificates macOS doesn't trust.
- `test_workbook_calc.py --tabpy` no longer writes the mock key into the
  TabPy process environment.
- `genai_calc.py` warns on plain-`http://` non-loopback URLs; Gemini model
  ids are URL-quoted.

Correctness
- `post_json`: read timeouts are not retried; `Retry-After` honoured (≤30 s);
  only `response.json()` failures map to "Non-JSON"; non-object JSON is a
  readable error instead of a traceback.
- Response calc chosen by caption (`--response`), not "first SCRIPT_ calc";
  ambiguity is an error. Same preference in `profile_data.py` and the tests.
- Renamed parameters found via the Response's `SCRIPT_STR` arguments.
- Creating **Select LLM** requires `--models` (no placeholder model id).
- `--payload-max-chars` / `--payload-type` accept the label or the caption;
  unknown names are an error.
- Re-patching keeps worksheet copies of Prompts alias-only; reports an
  existing Prompts control instead of "not found".
- `ca_bundle.py` keeps `--add` roots across rebuilds (`ca-extra/`).
- Temp dirs are removed on every exit path (`patch_workbook.py`,
  `profile_data.py`).
- `test_workbook_calc.py` refuses non-OpenAI-wire builds instead of
  silently calling a real endpoint.
- `publish` scans every `.twb`/`.tds` member of a package.

Tests: `test_calc_e2e.py` 33 → 39 cases (429 + Retry-After, ReadTimeout not
retried, non-object JSON, echoed-key scrubbing).

## v0.1.0-preview — 2026-10-27

First release.

- `genai_calc.py`: SCRIPT_STR generator for 11 providers (Ask Sage ×2,
  OpenAI, Azure OpenAI, Bedrock API key / IAM, Vertex API key / service
  account, Palantir AIP, Anthropic, OpenAI-compatible gateway); keystore and
  demo modes; guards, retries, readable errors, CA-bundle TLS.
- `keystore.py` / `set_secret.py`: env → macOS Keychain → keyring → secrets
  file; hidden-dialog / no-echo / clipboard / file input; values never printed.
- `ca_bundle.py`: export macOS System-keychain roots (+ extra PEMs), TLS probe.
- `profile_data.py`: workbook + Hyper/CSV profiling, scaffold and risk
  detection, Payload recommendation with token budget.
- `compose_prompts.py`, `payload.py`, `patch_workbook.py`: Prompts list
  parameter, Payload rebuild, Response calc replacement, dashboard control,
  validation; `.twb` and `.twbx`.
- `server_io.py`: Tableau Server / Cloud list, download, publish (refuses
  credential-bearing workbooks).
- `list_models.py`: model ids for Select LLM.
- Tests: mock providers, `test_calc_e2e.py` (33 cases),
  `test_workbook_calc.py` (keystore and demo builds).
- Worked example: DNFSB weekly site reports.
