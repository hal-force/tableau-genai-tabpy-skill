# Troubleshooting

Messages below are what appears in the **Generative AI Response** worksheet
unless noted.

## Credentials

**`No credential named 'asksage' was found on the TabPy host …`**
The keystore build looked in env → Keychain → keyring → secrets file *on the
machine TabPy runs on* and found nothing.
- Laptop TabPy: `python3 scripts/set_secret.py asksage`, then
  `python3 scripts/set_secret.py asksage --check`.
- TabPy runs as a different macOS user, under `launchd`, or with a locked
  login keychain → use the env var `TABLEAU_GENAI_ASKSAGE` in TabPy's
  environment, or the secrets file.
- Docker / Linux / Server TabPy → `-e TABLEAU_GENAI_ASKSAGE=…` from your
  secret manager, or mount a chmod-600 `secrets.json` and set
  `TABLEAU_GENAI_SECRETS_FILE`.
- Secret name mismatch → the calc uses the name passed with `--secret-name`
  (see the `get_secret('…')` line in the calc).

**macOS asks "TabPy/python wants to use your confidential information stored
in 'tableau-genai'"** — the first Keychain read from a new Python binary.
Choose *Always Allow*. If TabPy is headless, prefer the env var.

**`Demo credential 'gateway' is empty …`** — the DEMO build still holds the
placeholder. Edit the calc (`DEMO_SECRETS`) or rebuild with
`--demo-key-from-keystore`, or use the keystore build.

## TLS

**`… failed (SSLError): … CERTIFICATE_VERIFY_FAILED`** — the endpoint is
signed by a corporate / DoD CA that certifi doesn't include.
```
python3 scripts/ca_bundle.py --test https://endpoint/   # 401/403/404 = handshake OK
```
Rebuild with `--tls ~/.tableau-genai/ca-bundle.pem`. Add DoD PKI roots with
`--add dod_chain.pem`. Don't use `--tls insecure`.

**`TLS CA bundle /…/ca-bundle.pem was not found on the TabPy host`** — the
path is resolved where TabPy runs (often a container). Run `ca_bundle.py`
there, mount the file, or rebuild with the host's path.

## Timeouts and size

**Tableau shows "An error occurred while communicating with the analytics
extension" or a timeout after a few seconds** — Tableau's extension timeout
is shorter than the model's answer time. Raise it (Desktop: the
analytics-extension connection's timeout; Server: `tsm configuration set -k
vizqlserver.extsvc.default_timeout_in_seconds`) and TabPy's
`TABPY_EVALUATE_TIMEOUT`, both above the calc's `--timeout` (120 s default).

**`Grounding context is too large (N tokens, max 90000)`** — select fewer
marks or filter; or truncate long text in the Payload
(`--payload-max-chars "Report Content=4000"`); or raise
`--max-grounding-tokens` if the model's context allows.

**`Click the Analyze … button`** — Grounding is empty: no marks selected, or
the parameter action didn't fire. Check *Dashboard → Actions* still targets
**Grounding** with the **Payload** field.

## Provider errors

**`HTTP 401/403 from <provider>`** — wrong / expired / rotated key, or the key
lacks access to the model. Re-store it. The response body is shown (truncated)
so the provider's reason is visible; the key itself is never echoed.

**`HTTP 404`** — wrong `--url` path or model id. Use `list_models.py`; for
Azure, model = deployment name; for Bedrock, use an inference-profile id
(`us.` / `us-gov.` prefix) where required.

**`HTTP 429`** — retried 3 times with backoff before surfacing; reduce
concurrency (each mark change re-evaluates) or ask for higher limits.

**`Non-JSON response`** — usually a proxy login page or an HTML error from a
gateway; check `--url` and network path from the TabPy host.

## Workbook

**Prompts control missing on phone / tablet** — device layouts that were
customised after the patch don't inherit zones; add the parameter control in
that layout.

**Response shows the old 4-argument behaviour** — you opened the original,
not `… (GenAI - X).twb`.

**`VALIDATION FAILED: …` from `patch_workbook.py`** — the output was not
written. Typical causes: a `--payload` caption that doesn't exist in the data
source (`profile.md` lists captions), or a credential literal surviving in a
keystore build (report it — it's a bug).

**Publishing a `.twb` warns about local `.hyper` files** — export a packaged
workbook (*File → Export Packaged Workbook*) or publish the data sources
first.

## Logs

TabPy logs at DEBUG include whole script bodies and arguments (grounding
text; keys in demo calcs). Set TabPy's logger to INFO for anything but
debugging, and purge old logs after rotating a key that was ever embedded.
