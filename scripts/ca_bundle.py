#!/usr/bin/env python3
"""Export a CA bundle so the TabPy calc can VERIFY TLS to internal gateways.

Corporate / DoD gateways are often signed by an internal CA that Python's
certifi bundle does not include -- which is why calcs end up with
verify=False. On macOS the trusted roots (including MDM-pushed corporate and
DoD roots) are in the System keychain; export them and point the calc at the
file with genai_calc.py --tls PATH.

  python3 ca_bundle.py [--out ~/.tableau-genai/ca-bundle.pem] [--test URL]
  python3 ca_bundle.py --add dod_roots.pem          # append extra PEM roots (e.g. DoD PKI)

--test makes an unauthenticated GET; any HTTP status (401/403/404) proves the
TLS handshake verified. No credential is sent.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys

KEYCHAINS = ["/Library/Keychains/System.keychain",
             "/System/Library/Keychains/SystemRootCertificates.keychain"]


def export(out: str, extra: list[str]) -> int:
    pem = ""
    if sys.platform == "darwin":
        r = subprocess.run(["/usr/bin/security", "find-certificate", "-a", "-p"] +
                           [k for k in KEYCHAINS if os.path.exists(k)], capture_output=True, text=True)
        pem = r.stdout
    else:
        for p in ("/etc/ssl/certs/ca-certificates.crt", "/etc/pki/tls/certs/ca-bundle.crt"):
            if os.path.exists(p):
                pem = open(p).read()
                break
    try:
        import certifi
        pem += "\n" + open(certifi.where()).read()
    except ImportError:
        pass
    for e in extra:
        pem += "\n" + open(os.path.expanduser(e)).read()
    os.makedirs(os.path.dirname(out), mode=0o700, exist_ok=True)
    with open(out, "w") as fh:
        fh.write(pem)
    return pem.count("BEGIN CERTIFICATE")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=os.path.expanduser("~/.tableau-genai/ca-bundle.pem"))
    ap.add_argument("--add", nargs="*", default=[], help="extra PEM files to append")
    ap.add_argument("--test", help="URL to probe with verification on")
    a = ap.parse_args(argv)
    out = os.path.expanduser(a.out)
    n = export(out, a.add)
    print("wrote %s (%d certificates)" % (out, n))
    if a.test:
        import requests
        try:
            r = requests.get(a.test, timeout=15, verify=out)
            print("TLS verified: HTTP %d from %s  -> use: genai_calc.py --tls %s" % (r.status_code, a.test, out))
        except requests.exceptions.SSLError as e:
            print("TLS still fails (%s). Get the gateway's issuing CA from your PKI team and "
                  "re-run with --add CA.pem" % str(e)[:160])
            return 1
        except requests.exceptions.RequestException as e:
            print("Could not reach %s: %s" % (a.test, type(e).__name__))
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
