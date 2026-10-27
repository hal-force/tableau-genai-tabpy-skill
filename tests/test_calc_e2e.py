#!/usr/bin/env python3
"""End-to-end test of every generated provider calc against a local mock.

  python3 tests/test_calc_e2e.py                          # run scripts in-process
  python3 tests/test_calc_e2e.py --tabpy http://127.0.0.1:9111   # via a real TabPy /evaluate

--tabpy must be a TabPy on THIS host (it has to reach the loopback mock).
The keychain case creates and deletes a throwaway item
(service tableau-genai, account genai-selftest).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import textwrap
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
sys.path.insert(0, HERE)

import genai_calc  # noqa: E402
import keystore  # noqa: E402
import mock_providers as mock  # noqa: E402

GROUNDING = " Start of Record  Report ID: R-001 Report Content: valve leak found  End of Record "
TEMPLATE = "PERSONA: You are a test analyst.\nAUDIENCE: QA.\nOBJECTIVE: Echo.\nCONSTRAINTS:\n- Be brief."


def body_for(provider, mode, extra, demo=None):
    a = genai_calc.build_parser().parse_args(["--provider", provider, "--mode", mode] + extra)
    a.fields = None
    b = genai_calc.build_body(provider, mode, a, demo)
    genai_calc.check_compiles(b)
    return b


def run_local(body, args):
    src = "def _f(_arg1=None, _arg2=None, _arg3=None, _arg4=None, _arg5=None):\n" + textwrap.indent(body, "    ")
    ns = {}
    exec(compile(src, "<calc>", "exec"), ns)
    return ns["_f"](**args)


def run_tabpy(url, body, args):
    req = urllib.request.Request(url.rstrip("/") + "/evaluate",
                                 data=json.dumps({"script": body, "data": args}).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())


def args_for(model, tokens=25, template=TEMPLATE, question="What failed?"):
    return {"_arg1": [template], "_arg2": [GROUNDING], "_arg3": [model],
            "_arg4": [tokens], "_arg5": [question]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tabpy", help="run through this TabPy /evaluate instead of in-process")
    a = ap.parse_args()
    run = (lambda b, x: run_tabpy(a.tabpy, b, x)) if a.tabpy else run_local
    srv, base = mock.start()
    K = mock.TEST_KEY
    results = []

    def case(name, body, args, expect, forbid=None):
        try:
            out = run(body, args)
            text = out[0] if isinstance(out, list) else json.dumps(out)
        except Exception as e:  # noqa: BLE001
            text = "EXCEPTION %s: %s" % (type(e).__name__, e)
        ok = all(e in text for e in (expect if isinstance(expect, list) else [expect]))
        if forbid and forbid in text:
            ok = False
        results.append((ok, name, text[:140].replace("\n", " ")))

    demo = lambda name: {name: K}  # noqa: E731
    # ---- happy path, every provider, demo mode ---------------------------
    case("gateway", body_for("gateway", "demo", ["--url", base + "/chat/completions"], demo("gateway")),
         args_for("claude-sonnet-4-20250514"), "MOCK-OPENAI model=claude-sonnet-4-20250514")
    case("openai", body_for("openai", "demo", ["--url", base + "/v1/chat/completions"], demo("openai")),
         args_for("gpt-5"), "MOCK-OPENAI model=gpt-5")
    case("azure-openai (api-key header)", body_for("azure-openai", "demo", ["--url", base + "/openai/v1/chat/completions"], demo("azure-openai")),
         args_for("my-deployment"), "MOCK-OPENAI model=my-deployment")
    case("asksage (openai-compatible)", body_for("asksage", "demo", ["--url", base + "/server/openai/v1/chat/completions"], demo("asksage")),
         args_for("gpt-6-astra"), "MOCK-OPENAI model=gpt-6-astra")
    case("asksage-native (token exchange)", body_for("asksage-native", "demo",
         ["--url", base + "/server/query", "--token-url", base + "/user/get-token-with-api-key"],
         {"asksage": K, "asksage.email": mock.TEST_EMAIL}),
         args_for("gpt-6-astra"), "MOCK-ASKSAGE-NATIVE model=gpt-6-astra")
    case("bedrock (API key, ':' in model id)", body_for("bedrock", "demo", ["--url", base], demo("bedrock")),
         args_for("anthropic.claude-3-5-sonnet-20240620-v1:0"), ["MOCK-BEDROCK-bearer", "v1%3A0"])
    case("bedrock-iam (boto3 SigV4)", body_for("bedrock-iam", "demo", ["--url", base, "--region", "us-gov-west-1"],
         {"bedrock.access_key_id": "AKIATESTTESTTEST", "bedrock.secret_access_key": "secret/test"}),
         args_for("us.anthropic.claude-sonnet-4-6"), "MOCK-BEDROCK-sigv4")
    case("vertex (API key, Gemini)", body_for("vertex", "demo", ["--url", base + "/v1"], demo("vertex")),
         args_for("google/gemini-3.5-flash"), "MOCK-GEMINI model=gemini-3.5-flash")
    case("palantir -> anthropic proxy", body_for("palantir", "demo", ["--url", base], demo("palantir")),
         args_for("ri.language-model-service..language-model.anthropic-claude-4-6-opus"),
         "path=/api/v2/llm/proxy/anthropic/v1/messages")
    case("palantir -> openai proxy", body_for("palantir", "demo", ["--url", base], demo("palantir")),
         args_for("ri.language-model-service..language-model.gpt-5"),
         "path=/api/v2/llm/proxy/openai/v1/chat/completions")
    case("anthropic", body_for("anthropic", "demo", ["--url", base + "/v1/messages"], demo("anthropic")),
         args_for("claude-sonnet-4-6"), "MOCK-ANTHROPIC model=claude-sonnet-4-6")
    vsa = body_for("vertex-sa", "demo", ["--url", base + "/v1/projects/p/locations/global/endpoints/openapi/chat/completions"],
                   {"vertex.service_account": '{"type":"service_account"}'})
    # A fake SA can't mint a token: expect the readable failure, never a traceback.
    # (google-auth missing -> install hint; present -> 'Could not mint a Vertex token')
    out = run(vsa, args_for("gemini-3.5-flash"))
    text = out[0] if isinstance(out, list) else json.dumps(out)
    results.append((("google-auth is not installed" in text or "Could not mint a Vertex token" in text),
                    "vertex-sa: bad/absent google-auth -> readable error", text[:140]))

    # ---- prompt assembly reached the wire --------------------------------
    gw = body_for("gateway", "demo", ["--url", base + "/chat/completions"], demo("gateway"))
    run(gw, args_for("m1"))
    sent = mock.LAST.get("body", {}).get("messages", [{}])[0].get("content", "")
    for frag in ["PERSONA: You are a test analyst.", "ANALYST QUESTION: What failed?",
                 "GROUNDING DATA", "Report ID: R-001", "ANALYSIS QUALITY SELF-ASSESSMENT",
                 "REMINDER OF ANALYST TASK: What failed?"]:
        results.append((frag in sent, "prompt contains %r" % frag, ""))
    run(gw, args_for("m1", question=""))
    sent = mock.LAST.get("body", {}).get("messages", [{}])[0].get("content", "")
    results.append(("ANALYST QUESTION" not in sent and "Complete the OBJECTIVE" in sent,
                    "template-only prompt omits ANALYST QUESTION", ""))

    # ---- guards ------------------------------------------------------------
    case("guard: empty grounding", gw, args_for("m1", tokens=0), "Click the Analyze")
    case("guard: grounding too large", gw, args_for("m1", tokens=95000), "too large (95000 tokens")
    case("guard: nothing to ask", gw, args_for("m1", template="", question=""), "Choose an analysis")
    case("guard: no model", gw, args_for(""), "Select a model")
    ph = body_for("gateway", "demo", ["--url", base + "/chat/completions"])
    case("demo placeholder not sent", ph, args_for("m1"), "is empty. Paste it")
    bad = body_for("gateway", "demo", ["--url", base + "/chat/completions"], {"gateway": "wrong-key-should-not-leak"})
    case("401 surfaced, key not echoed", bad, args_for("m1"), "HTTP 401", forbid="wrong-key-should-not-leak")
    nf = body_for("gateway", "demo", ["--url", "http://127.0.0.1:9/chat/completions", "--timeout", "2"], demo("gateway"))
    case("unreachable host -> readable error", nf, args_for("m1"), "failed (ConnectionError)")
    np_body = body_for("gateway", "demo", ["--url", base + "/chat/completions", "--no-prompts-param"], demo("gateway"))
    case("--no-prompts-param uses _arg1 as question", np_body,
         {"_arg1": ["Q only"], "_arg2": [GROUNDING], "_arg3": ["m1"], "_arg4": [25]}, "MOCK-OPENAI")

    # ---- HTTP edge cases ---------------------------------------------------
    fl = body_for("gateway", "demo", ["--url", base + "/flaky/chat/completions"], demo("gateway"))
    case("429 + Retry-After retried, then OK", fl, args_for("m1"), "MOCK-OPENAI")
    results.append((mock.HITS.get("/flaky/chat/completions") == 2, "429 retried exactly once", str(mock.HITS)))
    import time as _time
    sl = body_for("gateway", "demo", ["--url", base + "/slow/chat/completions", "--timeout", "1"], demo("gateway"))
    t0 = _time.time()
    case("ReadTimeout -> readable error", sl, args_for("m1"), "No answer from")
    results.append((_time.time() - t0 < 2.5 and mock.HITS.get("/slow/chat/completions") == 1,
                    "ReadTimeout is not retried", "%.1fs" % (_time.time() - t0)))
    ls = body_for("gateway", "demo", ["--url", base + "/list/chat/completions"], demo("gateway"))
    case("non-object JSON -> readable error", ls, args_for("m1"), "Unexpected response shape")
    ec = body_for("gateway", "demo", ["--url", base + "/echo401/chat/completions"], demo("gateway"))
    case("credential echoed by provider is scrubbed", ec, args_for("m1"), ["HTTP 401", "<redacted>"], forbid=K)

    cab = body_for("gateway", "demo", ["--url", base + "/chat/completions", "--tls", "/nonexistent/ca.pem"], demo("gateway"))
    case("missing CA bundle -> readable error", cab, args_for("m1"), "TLS CA bundle /nonexistent/ca.pem was not found")
    import tempfile
    real_ca = os.path.join(tempfile.mkdtemp(), "ca.pem")
    open(real_ca, "w").write("")
    cab2 = body_for("gateway", "demo", ["--url", base + "/chat/completions", "--tls", real_ca], demo("gateway"))
    case("CA bundle path on plain-http mock still works", cab2, args_for("m1"), "MOCK-OPENAI")

    # ---- keystore mode -----------------------------------------------------
    ks = body_for("gateway", "keystore", ["--url", base + "/chat/completions", "--secret-name", "genai-selftest"])
    case("keystore: missing -> instructions", ks, args_for("m1"), "No credential named 'genai-selftest'")
    if not a.tabpy:
        os.environ["TABLEAU_GENAI_GENAI_SELFTEST"] = K
        case("keystore: env var", ks, args_for("m1"), "MOCK-OPENAI")
        del os.environ["TABLEAU_GENAI_GENAI_SELFTEST"]
    if keystore.is_macos_keychain():
        keystore.store_secret("genai-selftest", K, backend="macos-keychain")
        try:
            case("keystore: macOS Keychain" + (" via TabPy" if a.tabpy else ""), ks, args_for("m1"), "MOCK-OPENAI")
        finally:
            keystore.delete_secret("genai-selftest")
        weird = 'sk-"quoted" key\\with spaces'
        keystore.store_secret("genai-selftest", weird, backend="macos-keychain")
        try:
            got, _ = keystore.get_secret("genai-selftest")
            results.append((got == weird, "keychain round-trips quotes/backslashes (b64)", ""))
        finally:
            keystore.delete_secret("genai-selftest")

    srv.shutdown()
    width = max(len(r[1]) for r in results)
    for ok, name, text in results:
        print("%s  %-*s  %s" % ("PASS" if ok else "FAIL", width, name, text if not ok else ""))
    failed = sum(1 for r in results if not r[0])
    print("\n%d/%d passed" % (len(results) - failed, len(results)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
