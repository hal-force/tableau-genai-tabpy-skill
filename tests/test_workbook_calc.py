#!/usr/bin/env python3
"""Run the Generative AI Response calc FROM A PATCHED WORKBOOK through TabPy.

  python3 tests/test_workbook_calc.py "WB (GenAI - ...).twb" [--tabpy http://127.0.0.1:9111]
          [--grounding-hyper REPORTS.hyper] [--live]

* extracts the SCRIPT_STR body exactly as Tableau will send it ("" -> ")
* takes each Prompts member value from the workbook's Prompts parameter
* builds grounding the way the dashboard does (Payload per row, concatenated)
* default: API_URL is redirected to the local mock (no network, no key) and the
  mock key is supplied via env var -> checks the full prompt reaches the wire
* also runs the calc unmodified with no credential stored for a throwaway
  secret name -> must return the readable 'No credential named ...' message
* --live: run unmodified against the real endpoint with YOUR stored key
  (only the first 300 chars of the model's answer are printed)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
import mock_providers as mock  # noqa: E402

DEMO_RE = re.compile(r"^DEMO_SECRETS = (\{\}|\{.*?^\})", re.M | re.S)


def extract(twb):
    r = ET.parse(twb).getroot()
    resp = next(c for c in r.iter("column") if c.find("calculation") is not None
                and "SCRIPT_" in c.find("calculation").get("formula", ""))
    f = resp.find("calculation").get("formula")
    m = re.match(r'SCRIPT_STR\("(.*)",\n(.*)\n\)$', f, re.S)
    body = m.group(1).replace('""', '"')
    args = [a.strip() for a in m.group(2).split(",\n")]
    pd = next(d for d in r.iter("datasource") if d.get("name") == "Parameters")
    prompts = {}
    for c in pd.findall("column"):
        if (c.get("caption") or "") == "Prompts":
            for mem in c.findall("members/member"):
                v = mem.get("value")
                prompts[mem.get("alias") or v] = v[1:-1].replace('""', '"')
    payload = next(c for c in r.iter("column") if c.get("caption") == "Payload").find("calculation").get("formula")
    return body, args, prompts, payload


def grounding_from_hyper(path, payload_formula, n=5):
    """Evaluate the simple Payload formula ("lit" + IFNULL([col], "N/A") ...) in Python."""
    from tableauhyperapi import HyperProcess, Telemetry, Connection
    import shutil
    import tempfile
    d = tempfile.mkdtemp()
    f = os.path.join(d, "g.hyper")
    shutil.copy(path, f)
    parts = re.findall(r'"((?:[^"]|"")*)"|IFNULL\((?:LEFT\()?(?:STR\()?\[([^\]]+)\]', payload_formula)
    with HyperProcess(Telemetry.DO_NOT_SEND_USAGE_DATA_TO_TABLEAU, parameters={"log_dir": d}) as hp:
        with Connection(hp.endpoint, f) as cx:
            t = next(t for s in cx.catalog.get_schema_names() for t in cx.catalog.get_table_names(s))
            cols = [p[1] for p in parts if p[1]]
            rows = cx.execute_list_query("SELECT %s FROM %s LIMIT %d" % (", ".join('"%s"' % c for c in cols), t, n))
    out = []
    for row in rows:
        vals = iter(row)
        s = ""
        for lit, col in parts:
            if col:
                v = next(vals)
                s += "N/A" if v is None else str(v)
            elif lit != "N/A":
                s += lit.replace('""', '"')
        out.append(s)
    shutil.rmtree(d, ignore_errors=True)
    return ", ".join(out)  # Tableau's Concatenate aggregation joins with ', '


def evaluate(tabpy, body, data):
    if not tabpy:
        import textwrap
        ns = {}
        exec(compile("def _f(_arg1=None,_arg2=None,_arg3=None,_arg4=None,_arg5=None):\n" +
                     textwrap.indent(body, "    "), "<calc>", "exec"), ns)
        return ns["_f"](**data)[0]
    req = urllib.request.Request(tabpy.rstrip("/") + "/evaluate", data=json.dumps({"script": body, "data": data}).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.loads(r.read())[0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("twb")
    ap.add_argument("--tabpy")
    ap.add_argument("--grounding-hyper")
    ap.add_argument("--model", default="claude-sonnet-4-20250514")
    ap.add_argument("--live", action="store_true")
    a = ap.parse_args()
    body, args, prompts, payload = extract(a.twb)
    grounding = (grounding_from_hyper(a.grounding_hyper, payload) if a.grounding_hyper
                 else " Start of Record  Report ID: R-1 Report Content: test  End of Record ")
    tokens = len(grounding) // 4
    print("calc args: %s\nprompts: %d\ngrounding: %d chars (~%d tokens)" % (args, len(prompts), len(grounding), tokens))
    results = []
    if a.live:
        alias, val = next(iter(prompts.items()))
        out = evaluate(a.tabpy, body, {"_arg1": [val], "_arg2": [grounding], "_arg3": [a.model],
                                       "_arg4": [tokens], "_arg5": [""]})
        print("LIVE [%s] -> %s" % (alias, out[:300].replace("\n", " ")))
        return 0

    srv, base = mock.start()
    secret = re.search(r"get_secret\('([^']+)'\)", body)
    sname = secret.group(1) if secret else "gateway"
    env_var = "TABLEAU_GENAI_" + re.sub(r"[^A-Za-z0-9]", "_", sname).upper()
    mocked = re.sub(r"^API_URL = .*$", "API_URL = %r" % (base + "/chat/completions"), body, count=1, flags=re.M)
    mocked = mocked.replace("TLS_VERIFY = ", "TLS_VERIFY = False  # test\n_unused = ", 1)
    demo = re.search(r"^DEMO_SECRETS = ", body, re.M) is not None
    if demo:
        # DEMO build: swap the embedded dict (placeholder or injected key) for the mock key
        mocked = DEMO_RE.sub(lambda m: "DEMO_SECRETS = %r" % {sname: mock.TEST_KEY}, mocked, count=1)
    elif a.tabpy:
        # TabPy runs in another process: inject the mock key the same way an env var would
        mocked = mocked.replace("def get_secret(name):", "import os as _os\n_os.environ[%r] = %r\ndef get_secret(name):"
                                % (env_var, mock.TEST_KEY), 1)
    elif not demo:
        os.environ[env_var] = mock.TEST_KEY
    for alias, val in prompts.items():
        out = evaluate(a.tabpy, mocked, {"_arg1": [val], "_arg2": [grounding], "_arg3": [a.model],
                                         "_arg4": [tokens], "_arg5": ["Focus on Pantex." if not val else ""]})
        sent = mock.LAST.get("body", {}).get("messages", [{}])[0].get("content", "")
        ok = out.startswith("MOCK-OPENAI") and "GROUNDING DATA" in sent and \
            (val.split("\n")[0] in sent if val else "ANALYST QUESTION: Focus on Pantex." in sent) and \
            "COVERAGE:" in sent and grounding[:60] in sent
        results.append((ok, "prompt '%s' reaches the model (%d chars sent)" % (alias[:50], len(sent))))
    srv.shutdown()
    if demo:
        noenv = DEMO_RE.sub("DEMO_SECRETS = {}", body, count=1)
        expect = "Demo credential %r is empty" % sname
    else:
        noenv = body.replace("_secret(%r)" % sname, "_secret('genai-no-such-secret')")
        expect = "No credential named 'genai-no-such-secret'"
    out = evaluate(a.tabpy, noenv, {"_arg1": [next(iter(prompts.values()))], "_arg2": [grounding],
                                    "_arg3": [a.model], "_arg4": [tokens], "_arg5": [""]})
    results.append((expect in out, "unmodified calc, no key -> instructions (%s build)" % ("DEMO" if demo else "keystore")))
    out = evaluate(a.tabpy, body, {"_arg1": [""], "_arg2": [""], "_arg3": [a.model], "_arg4": [0], "_arg5": [""]})
    results.append(("Click the Analyze" in out, "empty grounding guard"))
    for ok, name in results:
        print("%s  %s" % ("PASS" if ok else "FAIL", name))
    failed = sum(not r[0] for r in results)
    print("%d/%d passed" % (len(results) - failed, len(results)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
