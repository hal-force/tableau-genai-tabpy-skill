# Security model

## Where the key lives

Keystore-mode calcs contain **no credential**. At call time, on the TabPy
host, the inlined `get_secret(name)` looks in order:

1. env var `TABLEAU_GENAI_<NAME>` (non-alphanumerics → `_`, upper-case),
   e.g. `TABLEAU_GENAI_ASKSAGE`, `TABLEAU_GENAI_ASKSAGE_EMAIL`
2. macOS Keychain, service `tableau-genai`, account `<name>` (via `security`)
3. Python `keyring` (Windows Credential Manager, Secret Service), if installed
4. JSON file `$TABLEAU_GENAI_SECRETS_FILE` or `~/.tableau-genai/secrets.json`
   (must be chmod 600)

A missing key returns a dashboard message telling the user what to run; it
never falls back to a literal.

## Getting the key in without the LLM seeing it

`set_secret.py NAME` reads from a hidden macOS dialog (default when launched
by Claude), a no-echo terminal prompt (`--from-terminal`), the clipboard
(`--from-clipboard`, cleared afterwards), or a file, and writes straight into
the keystore. Output is only `stored (N chars)`. Equivalent native command:

```
security add-generic-password -U -s tableau-genai -a asksage -w
```
(`-w` last, no value → prompts without echo.)

## Where TabPy runs decides which store works

| TabPy host | Works | Doesn't |
|---|---|---|
| macOS, same user as the Keychain (Desktop's localhost TabPy) | Keychain, env, file | — |
| Docker / Linux container | env (`-e TABLEAU_GENAI_X` from a secret manager), mounted secrets file, `bedrock-iam` with a role | macOS Keychain; host file paths (CA bundle!) unless mounted |
| Tableau Server / Cloud analytics extension (TabPy on a server) | env/secrets file on that server, instance role | anything on the author's laptop |

The CA bundle path in `--tls PATH` is resolved **on the TabPy host**; the calc
returns a readable error if it isn't there. Copy it (`ca_bundle.py` on that
host) or regenerate with a host path.

For production, consider deploying a TabPy function (`tabpy.deploy`) that
owns the provider call, so the workbook calls `tabpy.query('genai', …)` and
no provider code or endpoint lives in the workbook at all.

## TabPy itself

* **DEBUG logging writes every script body and argument to the log** —
  including any embedded key (DEMO builds, or older hand-written calcs) and
  the full grounding text. Set `[loggers] … level=INFO` in TabPy's logging
  config, then purge old `tabpy_log.log*` files *after* rotating any key that
  was ever embedded.
* TabPy binds all interfaces by default. On a laptop, restrict with the OS
  firewall, or keep auth on (`TABPY_PWD_FILE`) and use TLS
  (`TABPY_TRANSFER_PROTOCOL = https`) if it must be reachable.
* TabPy evaluates arbitrary Python from any authenticated client — treat its
  credentials like shell access to the host that holds the LLM keys.

## Data leaving the boundary

The grounding (Payload rows the user selected) is sent to the provider. Check
the provider's authorization level (IL2/4/5/6, FedRAMP) against the data's
classification and CUI/PII content **before** wiring a dashboard to it. The
calc caps grounding at `MAX_GROUNDING_TOKENS` but does not redact.

## Guards in the scripts

* `profile_data.py` flags `PLAINTEXT_API_KEY`, `GROUNDING_SAVED_VALUE`
  (a saved Grounding parameter value is data at rest in the .twb), and
  `TLS_VERIFY_DISABLED`; it prints redacted snippets only.
* `patch_workbook.py` fails if a keystore build contains any credential-looking
  literal, or if the source's old key survives anywhere in the output; DEMO
  builds with an injected key are written chmod 600.
* `server_io.py publish` refuses any workbook that embeds a credential.
* `--tls insecure` sets `TLS_VERIFY = False` (certificate checks off, warning
  suppressed) — it exists only to reproduce the old `verify=False` calc; use a
  CA bundle instead.
