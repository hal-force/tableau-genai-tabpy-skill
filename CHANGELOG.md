# Changelog

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
