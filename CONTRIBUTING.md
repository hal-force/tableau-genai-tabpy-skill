# Contributing

## Ground rules

- **Preview status** (`v0.1.0-preview`): interfaces may change.
- **License:** contributions are Apache-2.0 (see `LICENSE`).
- **No secrets, ever.** No API keys, PATs, `.env`, `secrets.json`, `.pem`
  bundles, TabPy logs, or Tableau workbooks/extracts (`*.twb*`, `*.hyper`,
  `*.tds*` are gitignored because they can carry credentials and data). If one
  gets staged, stop, remove it from history, and rotate the credential.
- **No non-public data** in `examples/`. Replace local paths and internal
  hostnames with placeholders.
- **Scripts never print secret values** — only presence and length. Keep it
  that way in new code, including error paths (`text.replace(key, "<key>")`).

## Setup

```sh
git clone https://github.com/hal-force/tableau-genai-tabpy-skill.git
cd tableau-genai-tabpy-skill
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python3 tests/test_calc_e2e.py          # must stay all-pass
```

Through a real TabPy (recommended before a PR that touches `genai_calc.py`):

```sh
pip install tabpy && tabpy --port 9111 &   # throwaway, no auth, loopback use only
python3 tests/test_calc_e2e.py --tabpy http://127.0.0.1:9111
```

## Adding a provider

1. Add an entry to `PROVIDERS` in `scripts/genai_calc.py` (`label`, `wire`,
   `url` with `{placeholders}`, auth header/prefix, `secrets`, `notes`).
2. Reuse a wire format if the API is compatible; otherwise add a `WIRE_*`
   section that returns `out['_error']` on failure and plain text on success.
3. Add the response shape to `tests/mock_providers.py` (check auth like the
   real service) and a `case(...)` in `tests/test_calc_e2e.py`.
4. Document it in `reference/providers.md` and the README table.
5. Regenerate the CLI docs: `python3 tools/gen_cli_docs.py`.

## Changing the workbook patcher

Test against at least one `.twb` and one `.twbx`, run
`tests/test_workbook_calc.py` on the output for both `--mode keystore` and
`--mode demo`, and open the result in Tableau Desktop. Describe any XML
structure you depend on in `reference/twb_patching.md`.

## Pull requests

- one topic per PR; update `CHANGELOG.md` under *Unreleased*
- include test output (counts, not secrets)
- security-relevant changes: say so in the description; see `SECURITY.md`
