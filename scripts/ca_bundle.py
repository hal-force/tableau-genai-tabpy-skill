#!/usr/bin/env python3
"""Export a CA bundle so the TabPy calc can VERIFY TLS to internal gateways.

Corporate / DoD gateways are often signed by an internal CA that Python's
certifi bundle does not include -- which is why calcs end up with
verify=False. On macOS the trusted roots (including MDM-pushed corporate and
DoD roots) are in the System keychain; export them and point the calc at the
file with genai_calc.py --tls PATH.

  python3 ca_bundle.py [--out ~/.tableau-genai/ca-bundle.pem] [--test URL]
  python3 ca_bundle.py --add dod_roots.pem          # append extra PEM roots (e.g. DoD PKI)

Certificates from the System keychain are kept only if `security verify-cert`
accepts them (so an untrusted or expired cert someone added is not turned
into a trust anchor). --add files are remembered in <out dir>/ca-extra/.

--test makes an unauthenticated GET; any HTTP status (401/403/404) proves the
TLS handshake verified. No credential is sent.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile

SYSTEM_KC = "/Library/Keychains/System.keychain"
ROOTS_KC = "/System/Library/Keychains/SystemRootCertificates.keychain"
PEM_RX = re.compile(r"-----BEGIN CERTIFICATE-----.+?-----END CERTIFICATE-----", re.S)


def _keychain_pems(keychain: str) -> list[str]:
    if not os.path.exists(keychain):
        return []
    r = subprocess.run(["/usr/bin/security", "find-certificate", "-a", "-p", keychain],
                       capture_output=True, text=True)
    return PEM_RX.findall(r.stdout)


def _trusted(pem: str) -> bool:
    """True if macOS trust evaluation (local only, no network) accepts the cert."""
    with tempfile.NamedTemporaryFile("w", suffix=".pem", delete=False) as fh:
        fh.write(pem + "\n")
    try:
        r = subprocess.run(["/usr/bin/security", "verify-cert", "-c", fh.name, "-p", "basic", "-L", "-q"],
                           capture_output=True, text=True, timeout=20)
        return r.returncode == 0
    except subprocess.TimeoutExpired:
        return False
    finally:
        os.unlink(fh.name)


def export(out: str, extra: list[str]) -> tuple[int, int]:
    """Write the bundle; returns (certificates written, System-keychain certs dropped)."""
    pems, dropped = [], 0
    if sys.platform == "darwin":
        pems += _keychain_pems(ROOTS_KC)       # Apple's root store
        # System.keychain also holds admin/MDM-added certs, including ones that are
        # NOT trusted (or are expired). Only keep what macOS itself would trust.
        for pem in _keychain_pems(SYSTEM_KC):
            if _trusted(pem):
                pems.append(pem)
            else:
                dropped += 1
    else:
        for p in ("/etc/ssl/certs/ca-certificates.crt", "/etc/pki/tls/certs/ca-bundle.crt"):
            if os.path.exists(p):
                pems += PEM_RX.findall(open(p).read())
                break
    try:
        import certifi
        pems += PEM_RX.findall(open(certifi.where()).read())
    except ImportError:
        pass
    # --add roots are kept next to the bundle so a later rebuild doesn't drop them
    extra_dir = os.path.join(os.path.dirname(out), "ca-extra")
    for e in extra:
        src = os.path.expanduser(e)
        found = PEM_RX.findall(open(src).read())
        if not found:
            raise SystemExit("%s contains no PEM certificate" % src)
        os.makedirs(extra_dir, mode=0o700, exist_ok=True)
        shutil.copyfile(src, os.path.join(extra_dir, os.path.basename(src)))
    if os.path.isdir(extra_dir):
        for name in sorted(os.listdir(extra_dir)):
            pems += PEM_RX.findall(open(os.path.join(extra_dir, name)).read())
    seen, unique = set(), []
    for pem in pems:
        if pem not in seen:
            seen.add(pem)
            unique.append(pem)
    os.makedirs(os.path.dirname(out), mode=0o700, exist_ok=True)
    with open(out, "w") as fh:
        fh.write("\n".join(unique) + "\n")
    return len(unique), dropped


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=os.path.expanduser("~/.tableau-genai/ca-bundle.pem"))
    ap.add_argument("--add", nargs="*", default=[],
                    help="extra PEM roots (e.g. DoD PKI); copied to <out dir>/ca-extra/ and included "
                         "in every later rebuild")
    ap.add_argument("--test", help="URL to probe with verification on")
    a = ap.parse_args(argv)
    out = os.path.expanduser(a.out)
    n, dropped = export(out, a.add)
    print("wrote %s (%d certificates%s)" % (out, n, "; skipped %d System-keychain certs macOS does "
                                            "not trust" % dropped if dropped else ""))
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
