#!/usr/bin/env python3
"""Generate the 'Generative AI Response' TabPy calculation for a provider.

Emits a Tableau SCRIPT_STR(...) formula (caption-based field references,
pasteable into Tableau's calc editor or consumed by patch_workbook.py) plus
a reviewable copy of the embedded Python.

  python3 genai_calc.py --provider asksage --mode keystore --out OUT_DIR
  python3 genai_calc.py --provider bedrock --region us-gov-west-1 --mode keystore
  python3 genai_calc.py --provider gateway --url https://.../chat/completions --mode demo
  python3 genai_calc.py --list-providers

Modes
  demo      credential literal inside the calc (placeholder by default; use
            --demo-key-from-keystore to inject the stored key at write time
            without it passing through the LLM). Laptop demos only.
  keystore  no credential in the workbook; resolved on the TabPy host at
            call time (env -> macOS Keychain -> keyring -> secrets file).

SCRIPT_STR arguments (the calc expects these Tableau fields):
  _arg1 [Prompts]                engineered prompt (list parameter)  *
  _arg2 [Grounding]              string parameter set by the parameter action
  _arg3 [Select LLM]             model id parameter
  _arg4 [Grounding Token Count]  INT(LEN([Grounding])/4)
  _arg5 [Prompt]                 free-text analyst question          *
  * with --no-prompts-param, _arg1 is [Prompt] and there is no _arg5.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import sys
import textwrap

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

PLACEHOLDER = "PASTE_YOUR_API_KEY_HERE"
NAME_RX = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

# ----------------------------------------------------------------- registry
# wire: openai_chat | anthropic_messages | bedrock_converse | bedrock_boto3 |
#       gemini_generate | asksage_query | palantir_router
PROVIDERS = {
    "gateway": dict(
        label="OpenAI-compatible gateway", wire="openai_chat",
        url=None, auth_header="Authorization", auth_prefix="Bearer ",
        secrets=["gateway"],
        notes="Any /chat/completions endpoint: Salesforce model gateway, LiteLLM, "
              "vLLM, Ollama, GenAI.mil (if OpenAI-compatible). --url required."),
    "openai": dict(
        label="OpenAI (ChatGPT API)", wire="openai_chat",
        url="https://api.openai.com/v1/chat/completions",
        auth_header="Authorization", auth_prefix="Bearer ", secrets=["openai"]),
    "azure-openai": dict(
        label="Azure OpenAI", wire="openai_chat",
        url="https://{resource}.openai.azure.com/openai/v1/chat/completions",
        auth_header="api-key", auth_prefix="", secrets=["azure-openai"],
        notes="Model = deployment name. Azure Government: --url "
              "https://{resource}.openai.azure.us/openai/v1/chat/completions"),
    "asksage": dict(
        label="Ask Sage", wire="openai_chat",
        url="https://api.asksage.ai/server/openai/v1/chat/completions",
        auth_header="Authorization", auth_prefix="Bearer ", secrets=["asksage"],
        notes="OpenAI-compatible route; takes the Ask Sage API key as Bearer. "
              "Other instances: replace the instance segment of api.<instance>.ai."),
    "asksage-native": dict(
        label="Ask Sage (native /server/query)", wire="asksage_query",
        url="https://api.asksage.ai/server/query",
        token_url="https://api.asksage.ai/user/get-token-with-api-key",
        secrets=["asksage", "asksage.email"],
        notes="Exchanges email + API key for an access token on every call."),
    "bedrock": dict(
        label="Amazon Bedrock (API key)", wire="bedrock_converse",
        url="https://bedrock-runtime.{region}.amazonaws.com",
        secrets=["bedrock"],
        notes="Bedrock API key (Bearer) against the Converse API. GovCloud: "
              "--region us-gov-west-1."),
    "bedrock-iam": dict(
        label="Amazon Bedrock (IAM / SigV4)", wire="bedrock_boto3",
        url="", secrets=[],
        optional_secrets=["bedrock.access_key_id", "bedrock.secret_access_key",
                          "bedrock.session_token"],
        notes="boto3 Converse. Uses stored access keys if present, else the TabPy "
              "host's default AWS credential chain (instance role, profile)."),
    "vertex": dict(
        label="Google Vertex AI (API key)", wire="gemini_generate",
        url="https://aiplatform.googleapis.com/v1", secrets=["vertex"],
        notes="Vertex express-mode API key, Gemini generateContent."),
    "vertex-sa": dict(
        label="Google Vertex AI (service account)", wire="openai_chat",
        url="https://{host}/v1/projects/{project}/locations/{location}/endpoints/openapi/chat/completions",
        auth_header="Authorization", auth_prefix="Bearer ",
        secrets=["vertex.service_account"], token="vertex_sa",
        notes="OpenAI-compatible Vertex endpoint; OAuth token minted from a stored "
              "service-account JSON (needs google-auth in TabPy), falling back to "
              "`gcloud auth print-access-token`."),
    "palantir": dict(
        label="Palantir AIP (Foundry LLM proxy)", wire="palantir_router",
        url="https://{host}", secrets=["palantir"],
        notes="Routes Claude/Anthropic models to /api/v2/llm/proxy/anthropic/v1/messages "
              "and everything else to /api/v2/llm/proxy/openai/v1/chat/completions. "
              "Model ids are usually RIDs (ri.language-model-service..language-model.*)."),
    "anthropic": dict(
        label="Anthropic API", wire="anthropic_messages",
        url="https://api.anthropic.com/v1/messages",
        auth_header="x-api-key", auth_prefix="", secrets=["anthropic"]),
}

# ------------------------------------------------------------ code sections
HEADER = '''
import json
import time
import requests

# =====================================================================
# Generative AI Response -- {{LABEL}}
# Mode: {{MODE_NOTE}}
# Generated by the tableau-genai-tabpy skill on {{DATE}}.
# Regenerate with genai_calc.py rather than hand-editing.
# =====================================================================

def scalar(x):
    return x[0] if isinstance(x, (list, tuple)) else x

SCRUB = []   # every secret / token resolved below; removed from anything returned to Tableau

def scrub(s):
    s = str(s)
    for v in SCRUB:
        if v:
            s = s.replace(v, '<redacted>')
    return s

PROVIDER_LABEL = {{LABEL_REPR}}
API_URL = {{API_URL_REPR}}
MODEL = str(scalar(_arg3) or '').strip()
MAX_GROUNDING_TOKENS = {{MAX_GROUNDING_TOKENS}}
MAX_OUTPUT_TOKENS = {{MAX_OUTPUT_TOKENS}}   # 0 = provider default (omitted)
TLS_VERIFY = {{TLS_VERIFY}}   # True | False (demo only) | path to a CA bundle (e.g. DoD PKI)
REQUEST_TIMEOUT = {{TIMEOUT}}
{{EXTRA_CONFIG}}
if TLS_VERIFY is False:
    try:
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    except Exception:
        pass
if isinstance(TLS_VERIFY, str):
    import os as _os
    if not _os.path.isfile(TLS_VERIFY):
        return ['TLS CA bundle ' + TLS_VERIFY + ' was not found on the TabPy host. Copy it there (python3 ca_bundle.py on that host), or regenerate the calc with --tls PATH-ON-TABPY-HOST.']
'''

SECRETS_DEMO = '''
# ---------------------------------------------------------------------
# DEMO MODE -- the credential is embedded in this calculation.
# Anyone who can open, download, or copy this workbook can read it.
# Laptop demos only. Never publish this version to Server or Cloud.
# ---------------------------------------------------------------------
DEMO_SECRETS = {{DEMO_SECRETS}}

def get_secret(name):
    v = str(DEMO_SECRETS.get(name, '') or '').strip()
    return '' if v == {{PLACEHOLDER_REPR}} else v

def missing_secret(name):
    return 'Demo credential ' + repr(name) + ' is empty. Paste it into DEMO_SECRETS in this calculation, or switch to the keystore version.'
'''

SECRETS_KEYSTORE = '''
# ---------------------------------------------------------------------
# KEYSTORE MODE -- no credential is stored in this workbook.
# Secrets are resolved on the TabPy host at call time, in this order:
#   1. env var TABLEAU_GENAI_<NAME>         (e.g. TABLEAU_GENAI_ASKSAGE)
#   2. macOS Keychain, service {{SERVICE}}, account <name>
#   3. OS keyring (Windows Credential Manager / Linux Secret Service)
#   4. JSON file $TABLEAU_GENAI_SECRETS_FILE or ~/.tableau-genai/secrets.json
# Store one with:  python3 set_secret.py <name>   (value never displayed)
# ---------------------------------------------------------------------
KEYSTORE_SERVICE = {{SERVICE_REPR}}

def get_secret(name):
    import os
    import sys
    import base64
    import subprocess

    def decode(v):
        v = (v or '').strip()
        if v.startswith('b64:'):
            try:
                v = base64.b64decode(v[4:]).decode('utf-8').strip()
            except Exception:
                return ''
        return v

    env_name = 'TABLEAU_GENAI_' + ''.join(c if c.isalnum() else '_' for c in name).upper()
    v = decode(os.environ.get(env_name, ''))
    if v:
        return v
    if sys.platform == 'darwin' and os.path.exists('/usr/bin/security'):
        try:
            r = subprocess.run(['/usr/bin/security', 'find-generic-password', '-s', KEYSTORE_SERVICE, '-a', name, '-w'], capture_output=True, text=True, timeout=15)
            if r.returncode == 0:
                v = decode(r.stdout)
                if v:
                    return v
        except Exception:
            pass
    try:
        import keyring
        v = decode(keyring.get_password(KEYSTORE_SERVICE, name))
        if v:
            return v
    except Exception:
        pass
    path = os.environ.get('TABLEAU_GENAI_SECRETS_FILE') or os.path.expanduser('~/.tableau-genai/secrets.json')
    try:
        with open(path) as fh:
            v = decode(str((json.load(fh) or {}).get(name, '') or ''))
        if v:
            return v
    except Exception:
        pass
    return ''

def missing_secret(name):
    env_name = 'TABLEAU_GENAI_' + ''.join(c if c.isalnum() else '_' for c in name).upper()
    return ('No credential named ' + repr(name) + ' was found on the TabPy host. Store it with: python3 set_secret.py ' + name
            + '  (macOS Keychain service ' + KEYSTORE_SERVICE + '), or set env ' + env_name
            + ' / add it to ~/.tableau-genai/secrets.json on the TabPy host, then click Analyze again.')
'''

GUARDS = '''
try:
    token_count = float(scalar(_arg4))
except (TypeError, ValueError):
    token_count = 0.0

if token_count < 1:
    return [{{EMPTY_MSG_REPR}}]

if token_count > MAX_GROUNDING_TOKENS:
    return ['Grounding context is too large (' + str(int(token_count)) + ' tokens, max ' + str(MAX_GROUNDING_TOKENS) + '). Apply additional filters in the dashboard to narrow the dataset, or split the analysis into multiple focused queries by ' + {{SPLIT_HINT_REPR}} + '.']

if not MODEL:
    return ['Select a model in the Select LLM parameter.']
'''

PROMPT_WITH_TEMPLATE = '''
template = str(scalar(_arg1) or '').strip()   # engineered prompt (Prompts parameter)
question = str(scalar(_arg5) or '').strip()   # free-text analyst question (Prompt parameter)
'''

PROMPT_NO_TEMPLATE = '''
template = ''
question = str(scalar(_arg1) or '').strip()   # analyst question (Prompt parameter)
'''

PROMPT_ASSEMBLY = '''
if not template and not question:
    return ['Choose an analysis in the Prompts list or type a question in the Prompt box.']

sections = []
if template:
    sections.append(template)
if question:
    sections.append('ANALYST QUESTION: ' + question)
sections.append('GROUNDING DATA (the only evidence you may use; cite record IDs):\\n' + str(scalar(_arg2) or ''))

self_assessment = (
    '\\n\\nANALYSIS QUALITY SELF-ASSESSMENT (REQUIRED):\\n'
    'After producing your analysis, append a structured assessment block in this exact format:\\n'
    '---\\n'
    'COVERAGE: [HIGH | MEDIUM | LOW]\\n'
    'COVERAGE_RATIONALE: One sentence explaining whether the grounding data was sufficient, sparse, or contained gaps relevant to the question asked.\\n'
    'CONFIDENCE: [HIGH | MEDIUM | LOW]\\n'
    'CONFIDENCE_RATIONALE: One sentence explaining how confident you are in the conclusions, citing any ambiguity, conflicting signals, or extrapolation beyond what the data supports.\\n'
    'DATA_GAPS: List any specific data elements that would have improved the analysis but were missing or underrepresented in the grounding. If none, state None identified.\\n'
    'RECOMMENDED_ACTION: [PROCEED | NARROW_QUERY | EXPAND_GROUNDING | HUMAN_REVIEW]\\n'
    '---\\n'
    'Be honest. If the grounding was thin, sparse, or off-topic relative to the question, say so explicitly rather than producing analysis that overreaches the evidence. Analysts depend on you flagging weak inputs.\\n'
)

task = question if question else 'Complete the OBJECTIVE stated above for the stated AUDIENCE.'
question_reminder = '\\n\\nREMINDER OF ANALYST TASK: ' + task + '\\nAnswer using the grounding data. Be specific.'

prompt = '\\n\\n'.join(sections) + self_assessment + question_reminder
'''

HTTP = '''
def _retry_delay(attempt, response, base_delay):
    delay = base_delay * (2 ** (attempt - 1))
    try:
        delay = max(delay, float(response.headers.get('Retry-After')))
    except Exception:
        pass
    return min(delay, 30)

def post_json(url, headers, body, max_retries=3, base_delay=1.0):
    # Retries 429/5xx and connection failures. A ReadTimeout is NOT retried:
    # the provider already has the request, and retrying would multiply the
    # wait far past Tableau's analytics-extension timeout.
    attempt = 0
    while True:
        response = None
        try:
            response = requests.post(url, json=body, headers=headers, timeout=REQUEST_TIMEOUT, verify=TLS_VERIFY)
        except requests.exceptions.ReadTimeout:
            return {'_error': 'No answer from ' + PROVIDER_LABEL + ' within ' + str(REQUEST_TIMEOUT) + ' s. Select fewer marks, ask for a shorter answer, or rebuild with a larger --timeout (and raise the Tableau / TabPy timeouts to match).'}
        except requests.exceptions.RequestException as e:
            if attempt < max_retries:
                attempt += 1
                time.sleep(base_delay * (2 ** (attempt - 1)))
                continue
            return {'_error': 'Request to ' + PROVIDER_LABEL + ' failed (' + type(e).__name__ + '): ' + str(e)[:500]}
        except Exception as e:
            return {'_error': 'Request to ' + PROVIDER_LABEL + ' failed (' + type(e).__name__ + '): ' + str(e)[:500]}
        code = response.status_code
        if code >= 400:
            if code in (429, 500, 502, 503, 504) and attempt < max_retries:
                attempt += 1
                time.sleep(_retry_delay(attempt, response, base_delay))
                continue
            return {'_error': 'HTTP ' + str(code) + ' from ' + PROVIDER_LABEL + ': ' + response.text[:1500]}
        try:
            out = response.json()
        except ValueError:
            return {'_error': 'Non-JSON response from ' + PROVIDER_LABEL + ': ' + response.text[:500]}
        if not isinstance(out, dict):
            return {'_error': 'Unexpected response shape from ' + PROVIDER_LABEL + ' (expected a JSON object): ' + response.text[:300]}
        return out

def join_text(blocks):
    if isinstance(blocks, str):
        return blocks
    if isinstance(blocks, list):
        return ''.join(b.get('text', '') for b in blocks if isinstance(b, dict) and b.get('type', 'text') == 'text')
    return ''
'''

WIRE_OPENAI = '''
def openai_chat(url, headers, model, prompt):
    body = {'model': model, 'messages': [{'role': 'user', 'content': prompt}], 'stream': False}
    if MAX_OUTPUT_TOKENS:
        body['max_tokens'] = MAX_OUTPUT_TOKENS
    out = post_json(url, headers, body)
    if '_error' in out:
        return out['_error']
    choices = out.get('choices') or []
    if not choices:
        return 'No choices in response: ' + json.dumps(out)[:500]
    return join_text(choices[0].get('message', {}).get('content')) or 'No content returned'
'''

WIRE_ANTHROPIC = '''
def anthropic_messages(url, headers, model, prompt):
    headers = dict(headers)
    headers['anthropic-version'] = '2023-06-01'
    body = {'model': model, 'max_tokens': MAX_OUTPUT_TOKENS or 4096, 'messages': [{'role': 'user', 'content': prompt}]}
    out = post_json(url, headers, body)
    if '_error' in out:
        return out['_error']
    text = join_text(out.get('content'))
    if not text:
        return 'No content returned (stop_reason=' + str(out.get('stop_reason')) + ')'
    return text
'''

WIRE_BEDROCK_TEXT = '''
def bedrock_text(out):
    blocks = ((out.get('output') or {}).get('message') or {}).get('content') or []
    text = ''.join(b.get('text', '') for b in blocks if isinstance(b, dict))
    return text or ('No content returned (stopReason=' + str(out.get('stopReason')) + ')')

def bedrock_body(prompt):
    return {'messages': [{'role': 'user', 'content': [{'text': prompt}]}], 'inferenceConfig': {'maxTokens': MAX_OUTPUT_TOKENS or 4096}}
'''

# -- per-provider credential + call sections ---------------------------------
CALL = {
    "openai_chat": '''
API_KEY = get_secret({{SECRET0_REPR}})
if not API_KEY:
    return [missing_secret({{SECRET0_REPR}})]

def call_llm(prompt):
    headers = {'Content-Type': 'application/json', {{AUTH_HEADER_REPR}}: {{AUTH_PREFIX_REPR}} + API_KEY}
    return openai_chat(API_URL, headers, MODEL, prompt)
''',
    "anthropic_messages": '''
API_KEY = get_secret({{SECRET0_REPR}})
if not API_KEY:
    return [missing_secret({{SECRET0_REPR}})]

def call_llm(prompt):
    headers = {'Content-Type': 'application/json', {{AUTH_HEADER_REPR}}: {{AUTH_PREFIX_REPR}} + API_KEY}
    return anthropic_messages(API_URL, headers, MODEL, prompt)
''',
    "palantir_router": '''
API_KEY = get_secret({{SECRET0_REPR}})
if not API_KEY:
    return [missing_secret({{SECRET0_REPR}})]

def call_llm(prompt):
    headers = {'Content-Type': 'application/json', 'Authorization': 'Bearer ' + API_KEY}
    base = API_URL.rstrip('/')
    m = MODEL.lower()
    if 'anthropic' in m or 'claude' in m:
        return anthropic_messages(base + '/api/v2/llm/proxy/anthropic/v1/messages', headers, MODEL, prompt)
    return openai_chat(base + '/api/v2/llm/proxy/openai/v1/chat/completions', headers, MODEL, prompt)
''',
    "bedrock_converse": '''
API_KEY = get_secret({{SECRET0_REPR}})
if not API_KEY:
    return [missing_secret({{SECRET0_REPR}})]

def call_llm(prompt):
    from urllib.parse import quote
    url = API_URL.rstrip('/') + '/model/' + quote(MODEL, safe='') + '/converse'
    headers = {'Content-Type': 'application/json', 'Authorization': 'Bearer ' + API_KEY}
    out = post_json(url, headers, bedrock_body(prompt))
    if '_error' in out:
        return out['_error']
    return bedrock_text(out)
''',
    "bedrock_boto3": '''
def call_llm(prompt):
    try:
        import boto3
        from botocore.config import Config
    except ImportError:
        return 'boto3 is not installed in the TabPy interpreter (pip install boto3, then restart TabPy).'
    kw = {'region_name': AWS_REGION, 'config': Config(read_timeout=REQUEST_TIMEOUT, retries={'max_attempts': 4, 'mode': 'adaptive'})}
    key_id = get_secret('bedrock.access_key_id')
    key_secret = get_secret('bedrock.secret_access_key')
    if key_id and key_secret:
        kw['aws_access_key_id'] = key_id
        kw['aws_secret_access_key'] = key_secret
        session_token = get_secret('bedrock.session_token')
        if session_token:
            kw['aws_session_token'] = session_token
    if API_URL:
        kw['endpoint_url'] = API_URL
    if TLS_VERIFY is not True:
        kw['verify'] = TLS_VERIFY
    try:
        client = boto3.client('bedrock-runtime', **kw)
        body = bedrock_body(prompt)
        out = client.converse(modelId=MODEL, messages=body['messages'], inferenceConfig=body['inferenceConfig'])
    except Exception as e:
        return 'Bedrock call failed (' + type(e).__name__ + '): ' + str(e)[:800]
    return bedrock_text(out)
''',
    "gemini_generate": '''
API_KEY = get_secret({{SECRET0_REPR}})
if not API_KEY:
    return [missing_secret({{SECRET0_REPR}})]

def call_llm(prompt):
    model = MODEL[len('google/'):] if MODEL.startswith('google/') else MODEL
    from urllib.parse import quote
    url = API_URL.rstrip('/') + '/publishers/google/models/' + quote(model, safe='') + ':generateContent'
    headers = {'Content-Type': 'application/json', 'x-goog-api-key': API_KEY}
    body = {'contents': [{'role': 'user', 'parts': [{'text': prompt}]}]}
    if MAX_OUTPUT_TOKENS:
        body['generationConfig'] = {'maxOutputTokens': MAX_OUTPUT_TOKENS}
    out = post_json(url, headers, body)
    if '_error' in out:
        return out['_error']
    candidates = out.get('candidates') or []
    if not candidates:
        return 'No candidates in response: ' + json.dumps(out.get('promptFeedback', out))[:500]
    parts = (candidates[0].get('content') or {}).get('parts') or []
    text = ''.join(p.get('text', '') for p in parts if isinstance(p, dict) and not p.get('thought'))
    return text or ('No content returned (finishReason=' + str(candidates[0].get('finishReason')) + ')')
''',
    "asksage_query": '''
API_KEY = get_secret({{SECRET0_REPR}})
if not API_KEY:
    return [missing_secret({{SECRET0_REPR}})]
ASKSAGE_EMAIL = get_secret({{SECRET1_REPR}})
if not ASKSAGE_EMAIL:
    return [missing_secret({{SECRET1_REPR}})]

def call_llm(prompt):
    tok = post_json(TOKEN_URL, {'Content-Type': 'application/json', 'Accept': 'application/json'}, {'email': ASKSAGE_EMAIL, 'api_key': API_KEY})
    if '_error' in tok:
        return 'Ask Sage token exchange failed: ' + tok['_error']
    access = tok.get('access_token') or ''
    resp = tok.get('response')
    if not access and isinstance(resp, dict):
        access = resp.get('access_token') or resp.get('token') or ''
    if not access:
        return 'Ask Sage token exchange returned no access_token (status ' + str(tok.get('status')) + ').'
    SCRUB.append(access)
    headers = {'Content-Type': 'application/json', 'x-access-tokens': access}
    body = {'message': prompt, 'model': MODEL, 'dataset': 'none', 'temperature': ASKSAGE_TEMPERATURE, 'live': 0}
    out = post_json(API_URL, headers, body)
    if '_error' in out:
        return out['_error']
    msg = out.get('message')
    if isinstance(msg, str) and msg.strip():
        return msg
    resp = out.get('response')
    if isinstance(resp, str) and resp.strip() and resp.strip().upper() != 'OK':
        return resp
    if isinstance(resp, dict) and isinstance(resp.get('message'), str):
        return resp['message']
    return 'Unexpected Ask Sage response: ' + json.dumps(out)[:500]
''',
}

VERTEX_SA_TOKEN = '''
def vertex_access_token():
    sa_json = get_secret({{SECRET0_REPR}})
    if sa_json:
        try:
            from google.oauth2 import service_account
            import google.auth.transport.requests
        except ImportError:
            return '', 'google-auth is not installed in the TabPy interpreter (pip install google-auth, then restart TabPy).'
        try:
            creds = service_account.Credentials.from_service_account_info(json.loads(sa_json), scopes=['https://www.googleapis.com/auth/cloud-platform'])
            creds.refresh(google.auth.transport.requests.Request())
            return creds.token, ''
        except Exception as e:
            return '', 'Could not mint a Vertex token from the stored service account (' + type(e).__name__ + ').'
    try:
        import subprocess
        r = subprocess.run(['gcloud', 'auth', 'print-access-token'], capture_output=True, text=True, timeout=30)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip(), ''
    except Exception:
        pass
    return '', missing_secret({{SECRET0_REPR}})

API_KEY, token_error = vertex_access_token()
if not API_KEY:
    return [token_error]
SCRUB.append(API_KEY)

def call_llm(prompt):
    headers = {'Content-Type': 'application/json', 'Authorization': 'Bearer ' + API_KEY}
    model = MODEL if '/' in MODEL else 'google/' + MODEL
    return openai_chat(API_URL, headers, model, prompt)
'''

SECRETS_WRAP = '''
_get_secret_store = get_secret

def get_secret(name):
    v = _get_secret_store(name)
    if v and len(v) >= 6:
        SCRUB.append(v)
    return v
'''

FOOTER = '''
result = call_llm(prompt)
return [scrub(result)]
'''


# ---------------------------------------------------------------- builder
def resolve_url(provider: str, a) -> str:
    p = PROVIDERS[provider]
    url = a.url or p.get("url")
    if url is None:
        raise SystemExit("--url is required for provider %r" % provider)
    location = a.location or "global"
    host = "aiplatform.googleapis.com" if location == "global" else location + "-aiplatform.googleapis.com"
    fills = {"region": a.region, "project": a.project, "location": location,
             "host": a.host if provider == "palantir" else host, "resource": a.resource}
    for k, v in fills.items():
        if "{" + k + "}" in url:
            if not v:
                raise SystemExit("--%s is required for provider %r (url %s)" % (k, provider, url))
            url = url.replace("{" + k + "}", v)
    return url


def secret_names(provider: str, override: str | None) -> list[str]:
    names = list(PROVIDERS[provider].get("secrets", []))
    if override and names:
        names[0] = override
    return names


def tls_literal(tls: str) -> str:
    if tls == "verify":
        return "True"
    if tls == "insecure":
        return "False"
    return repr(os.path.expanduser(tls))


def build_body(provider: str, mode: str, a, demo_values: dict | None = None) -> str:
    p = PROVIDERS[provider]
    wire = p["wire"]
    url = resolve_url(provider, a)
    names = secret_names(provider, a.secret_name)
    service = a.keystore_service
    for n in names + [service] + list(p.get("optional_secrets", [])):
        if not NAME_RX.match(n or ""):
            raise SystemExit("invalid secret / keystore service name %r (allowed: letters, digits, . _ -)" % n)

    extra = []
    if wire in ("bedrock_boto3", "bedrock_converse"):
        extra.append("AWS_REGION = %r" % (a.region or "us-east-1"))
    if wire == "asksage_query":
        extra.append("TOKEN_URL = %r" % (a.token_url or p["token_url"]))
        extra.append("ASKSAGE_TEMPERATURE = %r" % a.temperature)

    mode_note = ("DEMO (credential embedded -- do not publish)" if mode == "demo"
                 else "KEYSTORE (no credential in workbook)")
    max_out = a.max_output_tokens
    if max_out is None:
        max_out = 0 if wire in ("openai_chat", "palantir_router", "gemini_generate",
                                "asksage_query") else 4096

    parts = [HEADER]
    if mode == "demo":
        all_names = names + p.get("optional_secrets", [])
        vals = {n: (demo_values or {}).get(n, PLACEHOLDER if n == (names[0] if names else None) else "")
                for n in all_names}
        parts.append(SECRETS_DEMO.replace("{{DEMO_SECRETS}}", _dict_literal(vals)))
    else:
        parts.append(SECRETS_KEYSTORE)
    parts.append(SECRETS_WRAP)
    parts += [GUARDS, PROMPT_NO_TEMPLATE if a.no_prompts_param else PROMPT_WITH_TEMPLATE,
              PROMPT_ASSEMBLY, HTTP]
    if wire in ("openai_chat", "palantir_router"):
        parts.append(WIRE_OPENAI)
    if wire in ("anthropic_messages", "palantir_router"):
        parts.append(WIRE_ANTHROPIC)
    if wire in ("bedrock_converse", "bedrock_boto3"):
        parts.append(WIRE_BEDROCK_TEXT)
    parts.append(VERTEX_SA_TOKEN if p.get("token") == "vertex_sa" else CALL[wire])
    parts.append(FOOTER)
    body = "".join(parts)

    subs = {
        "{{LABEL}}": p["label"], "{{LABEL_REPR}}": repr(p["label"]),
        "{{MODE_NOTE}}": mode_note, "{{DATE}}": _dt.date.today().isoformat(),
        "{{API_URL_REPR}}": repr(url),
        "{{MAX_GROUNDING_TOKENS}}": str(int(a.max_grounding_tokens)),
        "{{MAX_OUTPUT_TOKENS}}": str(int(max_out)),
        "{{TLS_VERIFY}}": tls_literal(a.tls), "{{TIMEOUT}}": str(int(a.timeout)),
        "{{EXTRA_CONFIG}}": "\n".join(extra),
        "{{SERVICE}}": service, "{{SERVICE_REPR}}": repr(service),
        "{{PLACEHOLDER_REPR}}": repr(PLACEHOLDER),
        "{{EMPTY_MSG_REPR}}": repr(a.empty_message),
        "{{SPLIT_HINT_REPR}}": repr(a.split_hint),
        "{{AUTH_HEADER_REPR}}": repr(p.get("auth_header", "Authorization")),
        "{{AUTH_PREFIX_REPR}}": repr(p.get("auth_prefix", "Bearer ")),
        "{{SECRET0_REPR}}": repr(names[0]) if names else "''",
        "{{SECRET1_REPR}}": repr(names[1]) if len(names) > 1 else "''",
    }
    # one pass: a value that happens to contain "{{SLOT}}" is never expanded again
    body = re.sub(r"\{\{[A-Z0-9_]+\}\}", lambda m: subs.get(m.group(0), m.group(0)), body)
    leftover = re.findall(r"\{\{[A-Z0-9_]+\}\}", body)
    if leftover:
        raise RuntimeError("unfilled template slots: %s" % leftover)
    return body


def _dict_literal(d: dict) -> str:
    if not d:
        return "{}"
    rows = ["    %r: %r," % (k, v) for k, v in d.items()]
    return "{\n" + "\n".join(rows) + "\n}"


def check_compiles(body: str, nargs: int = 5) -> None:
    """TabPy wraps the script in a function; mimic that and compile."""
    args = ", ".join("_arg%d=None" % i for i in range(1, nargs + 1))
    src = "def _tabpy_user_script(%s):\n%s" % (args, textwrap.indent(body, "    "))
    compile(src, "<generative_ai_response>", "exec")


def to_tableau_formula(body: str, arg_fields: list[str]) -> str:
    """Wrap the Python body in SCRIPT_STR with caption-based field refs.
    Tableau string literals escape a double quote by doubling it."""
    escaped = body.replace('"', '""')
    return 'SCRIPT_STR("' + escaped + '",\n' + ",\n".join(arg_fields) + "\n)"


def arg_fields(a) -> list[str]:
    f = a.fields
    if a.no_prompts_param:
        return [f["prompt"], f["grounding"], f["model"], f["token_count"]]
    return [f["prompts"], f["grounding"], f["model"], f["token_count"], f["prompt"]]


def redact(body: str, values: dict) -> str:
    """Remove secret values from the reviewable .py copy. The body holds them
    as repr() literals (escaped backslashes / newlines), so redact that form
    first, then the raw value."""
    for v in values.values():
        if v and v != PLACEHOLDER and len(v) >= 4:
            for form in (repr(v), repr(v)[1:-1], v):
                body = body.replace(form, "'<redacted %d chars>'" % len(v) if form == repr(v)
                                    else "<redacted %d chars>" % len(v))
    return body


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--provider", choices=sorted(PROVIDERS))
    ap.add_argument("--mode", choices=["demo", "keystore"], default="keystore")
    ap.add_argument("--list-providers", action="store_true")
    ap.add_argument("--url", help="override the endpoint URL")
    ap.add_argument("--region", help="AWS region (bedrock*), e.g. us-east-1, us-gov-west-1")
    ap.add_argument("--project", help="GCP project id (vertex-sa)")
    ap.add_argument("--location", help="Vertex location (default global)")
    ap.add_argument("--host", help="Foundry hostname (palantir), e.g. acme.palantirfoundry.com")
    ap.add_argument("--resource", help="Azure OpenAI resource name")
    ap.add_argument("--token-url", help="Ask Sage native token URL override")
    ap.add_argument("--temperature", type=float, default=0.2, help="asksage-native only")
    ap.add_argument("--secret-name", help="override the primary secret name "
                                          "(e.g. asksage.prod for a second account)")
    ap.add_argument("--keystore-service", default="tableau-genai")
    ap.add_argument("--demo-key-from-keystore", action="store_true",
                    help="demo mode: inject the stored secret(s) into the calc at write "
                         "time. The value is written to disk only, never printed.")
    ap.add_argument("--max-grounding-tokens", type=int, default=90000)
    ap.add_argument("--max-output-tokens", type=int, default=None)
    ap.add_argument("--timeout", type=int, default=120)
    ap.add_argument("--tls", default="verify",
                    help="verify | insecure | /path/to/ca-bundle.pem")
    ap.add_argument("--no-prompts-param", action="store_true",
                    help="workbook has no Prompts list parameter; _arg1 = [Prompt]")
    ap.add_argument("--empty-message", default="Click the Analyze Using Agentic AI button to "
                    "load data for the Large Language Model (LLM) to conduct analysis.")
    ap.add_argument("--split-hint", default="region, time window, or incident type")
    ap.add_argument("--field-prompts", default="[Prompts]")
    ap.add_argument("--field-prompt", default="[Prompt]")
    ap.add_argument("--field-grounding", default="[Grounding]")
    ap.add_argument("--field-model", default="[Select LLM]")
    ap.add_argument("--field-token-count", default="[Grounding Token Count]")
    ap.add_argument("--out", help="output directory")
    return ap


def main(argv=None) -> int:
    a = build_parser().parse_args(argv)
    if a.list_providers:
        for k in sorted(PROVIDERS):
            p = PROVIDERS[k]
            print("%-15s %-40s secrets=%s\n%17s%s" % (k, p["label"], p.get("secrets"), "",
                                                   p.get("notes", p.get("url") or "")))
        return 0
    if not a.provider:
        raise SystemExit("--provider is required (see --list-providers)")
    a.fields = {"prompts": a.field_prompts, "prompt": a.field_prompt,
                "grounding": a.field_grounding, "model": a.field_model,
                "token_count": a.field_token_count}

    demo_values = {}
    if a.mode == "demo" and a.demo_key_from_keystore:
        import keystore
        p = PROVIDERS[a.provider]
        for n in secret_names(a.provider, a.secret_name) + p.get("optional_secrets", []):
            v, _ = keystore.get_secret(n, a.keystore_service)
            if v:
                demo_values[n] = v
        if not demo_values:
            raise SystemExit("--demo-key-from-keystore: nothing stored for %s. "
                             "Run set_secret.py first." % secret_names(a.provider, a.secret_name))

    url = resolve_url(a.provider, a) if PROVIDERS[a.provider].get("url") != "" else ""
    if url.startswith("http://") and not re.match(r"http://(localhost|127\.0\.0\.1|\[::1\])[:/]", url):
        print("WARNING: %s is plain http -- the API key and grounding data would cross the network "
              "unencrypted." % url, file=sys.stderr)
    body = build_body(a.provider, a.mode, a, demo_values)
    check_compiles(body)
    formula = to_tableau_formula(body, arg_fields(a))

    out = a.out or "."
    os.makedirs(out, exist_ok=True)
    stem = "generative_ai_response.%s.%s" % (a.provider, a.mode)
    calc_path = os.path.join(out, stem + ".twbcalc")
    py_path = os.path.join(out, stem + ".py")
    fd = os.open(calc_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600 if demo_values else 0o644)
    os.fchmod(fd, 0o600 if demo_values else 0o644)   # mode= only applies on creation
    with os.fdopen(fd, "w") as fh:
        fh.write(formula)
    with open(py_path, "w") as fh:
        fh.write(redact(body, demo_values))
    meta = {
        "provider": a.provider, "label": PROVIDERS[a.provider]["label"], "mode": a.mode,
        "url": resolve_url(a.provider, a), "secrets": secret_names(a.provider, a.secret_name),
        "tls": a.tls, "args": arg_fields(a), "calc_file": calc_path, "python_file": py_path,
        "embedded_secret": bool(demo_values),
        "formula_chars": len(formula),
    }
    with open(os.path.join(out, stem + ".json"), "w") as fh:
        json.dump(meta, fh, indent=2)
    print(json.dumps(meta, indent=2))
    if demo_values:
        print("\nWARNING: %s contains a live credential (chmod 600). Do not open it in "
              "a shared screen, commit it, or publish a workbook built from it." % calc_path,
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
