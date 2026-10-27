# Security Policy

## Reporting a vulnerability

**Do not open a public issue for security-relevant bugs.** Use GitHub's
private ["Report a vulnerability"](https://github.com/hal-force/tableau-genai-tabpy-skill/security/advisories/new)
flow on this repository. Include the provider, mode (keystore / demo), TabPy
host type, and the smallest reproduction — **never include a real API key,
workbook with an embedded key, or TabPy log**.

## What this project protects

| Asset | Protection |
|---|---|
| LLM API keys | never in the keystore-build workbook; read on the TabPy host at call time; entered via hidden dialog / no-echo prompt; scripts print only lengths; publish refuses credential-bearing workbooks; patcher scans the whole output and refuses a keystore build with any credential-looking literal, or any build in which the source's key survives; keys echoed back by a provider are scrubbed from dashboard messages |
| TLS trust | CA bundles contain only certificates macOS trust evaluation accepts; `--add` roots persist across rebuilds |
| Data in transit to the provider | TLS verification on by default; CA-bundle support for internal PKI instead of `verify=False` |
| Data at rest in workbooks | saved Grounding values are detected and reset |
| The author's chat transcript | the skill's rules forbid asking for, echoing, or reading key material; secret scans print counts, not values |

## Known residual risks (by design — mitigate operationally)

- **Demo builds embed a credential.** Use the placeholder, keep injected
  builds local (they're chmod 600), never publish or email them.
- **TabPy executes arbitrary Python** from any authenticated client and, by
  default, **listens on all interfaces**. Enable TabPy authentication, use
  TLS, firewall the port, and treat TabPy credentials like shell access to the
  host that holds the LLM keys.
- **TabPy DEBUG logs** contain every script body and argument — grounding
  text, and keys in demo calcs. Run at INFO; protect and rotate logs.
- **The keystore is the TabPy host's.** Anyone who can run code in TabPy can
  read the keys it can read. Use per-environment, least-privilege, rotatable
  keys (or IAM roles: `bedrock-iam`).
- **Grounding is sent to the provider unredacted.** Match the provider's
  authorization level (FedRAMP / DoD IL) to the data classification; don't
  put PII / CUI / classified fields in the Payload of a dashboard wired to an
  unauthorised service.
- **Authoring-time prompt injection.** `profile_data.py` puts sample values
  from your data into `profile.md`, which Claude Code reads while writing
  prompts. A record crafted to look like instructions could try to steer the
  agent. The skill's rules treat profile samples as data only, the agent
  never handles key values, and every write is validated — but review the
  generated prompts and patch report before using a workbook built from data
  you don't control.
- **Clipboard history.** `set_secret.py --from-clipboard` clears the
  clipboard afterwards, but clipboard-history managers (Raycast, Alfred,
  Paste, Maccy, …) may already have kept a copy. Prefer the hidden dialog or
  `--from-terminal`, or exclude your password manager from the history tool.
- **Keychain access control.** Items written with `/usr/bin/security` are
  readable by that binary without a prompt, so any process running as your
  user can read them through it. The Keychain protects keys at rest and from
  other users, not from your own processes — the same holds for the env-var
  and secrets-file stores. Lock the screen, and use separate keys per
  machine so one can be revoked.
- **`vertex-sa` fallback.** Without `google-auth` on the TabPy host the calc
  runs `gcloud auth print-access-token`, i.e. it calls Vertex as **whoever is
  logged in to gcloud on that host** (on a laptop: the author). Install
  `google-auth` and store a least-privilege service account instead.
- **XML parsing.** The authoring scripts parse workbooks with the standard
  library's ElementTree, which doesn't fetch external entities but isn't
  hardened against entity-expansion ("billion laughs") input. Only patch
  workbooks you trust, or `pip install defusedxml` and adapt the import.
- **Prompt injection:** grounding text comes from your data. A record that
  contains instructions can influence the answer. The calc has no tools or
  side effects — the model can only return text to the dashboard — but treat
  answers as analysis to verify, not ground truth.

## Supported versions

Only `main` receives fixes during the preview.
