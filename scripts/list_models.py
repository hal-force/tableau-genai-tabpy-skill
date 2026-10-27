#!/usr/bin/env python3
"""List model IDs a provider exposes, to populate the Select LLM parameter.

  python3 list_models.py --provider gateway --url https://host/chat/completions [--tls PATH]
  python3 list_models.py --provider openai | asksage | anthropic
  python3 list_models.py --provider asksage-native

Uses the stored key (keystore, never printed). Prints one model id per line,
plus a ready-to-paste --models argument for patch_workbook.py.
Bedrock / Vertex / Palantir: use the console (model access page) instead;
their list APIs need different auth scopes.
"""
from __future__ import annotations

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import genai_calc  # noqa: E402
import keystore  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--provider", required=True, choices=["gateway", "openai", "asksage", "asksage-native",
                                                          "anthropic", "azure-openai"])
    ap.add_argument("--url")
    ap.add_argument("--resource")
    ap.add_argument("--tls", default="verify")
    ap.add_argument("--secret-name")
    ap.add_argument("--filter", default="")
    a = ap.parse_args(argv)
    import requests
    p = genai_calc.PROVIDERS[a.provider]
    names = genai_calc.secret_names(a.provider, a.secret_name)
    key, _ = keystore.get_secret(names[0])
    if not key:
        raise SystemExit("no %r credential stored - run set_secret.py %s" % (names[0], names[0]))
    verify = {"verify": True, "insecure": False}.get(a.tls, os.path.expanduser(a.tls))
    ns = argparse.Namespace(url=a.url, region=None, project=None, location=None, host=None, resource=a.resource)
    url = genai_calc.resolve_url(a.provider, ns)
    if a.provider == "asksage-native":
        email, _ = keystore.get_secret(names[1])
        tok = requests.post(p["token_url"], json={"email": email, "api_key": key}, timeout=30, verify=verify).json()
        access = (tok.get("response") or {}).get("access_token") or tok.get("access_token")
        r = requests.post(url.rsplit("/", 1)[0] + "/get-models", headers={"x-access-tokens": access},
                          json={}, timeout=30, verify=verify)
        data = r.json()
        ids = [m.get("id") if isinstance(m, dict) else m for m in (data.get("response") or data.get("data") or [])]
    else:
        base = url.rsplit("/chat/completions", 1)[0].rsplit("/messages", 1)[0]
        hdr = {p.get("auth_header", "Authorization"): p.get("auth_prefix", "Bearer ") + key}
        if a.provider == "anthropic":
            hdr["anthropic-version"] = "2023-06-01"
        r = requests.get(base + "/models", headers=hdr, timeout=30, verify=verify)
        if r.status_code != 200:
            raise SystemExit("HTTP %d from %s/models: %s" % (r.status_code, base, r.text[:300].replace(key, "<key>")))
        data = r.json()
        ids = [m.get("id") for m in data.get("data", []) if isinstance(m, dict)]
    ids = sorted(i for i in ids if i and a.filter.lower() in i.lower())
    for i in ids:
        print(i)
    print('\n--models ' + " ".join('"%s=%s"' % (i, i) for i in ids[:8]), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
