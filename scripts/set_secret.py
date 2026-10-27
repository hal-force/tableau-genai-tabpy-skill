#!/usr/bin/env python3
"""Store an LLM API key (or other secret) without exposing it to the LLM.

The value is read from a hidden macOS dialog, a no-echo terminal prompt,
the clipboard, or a file -- and written straight into the keystore. This
script never prints the value; it reports only "stored (N chars)".

Usage
-----
  # Claude can launch this for you: a hidden-input macOS dialog pops up,
  # you paste the key, the transcript only ever sees "stored (N chars)".
  python3 set_secret.py asksage

  # In your own terminal (no-echo prompt):
  python3 set_secret.py asksage --from-terminal

  # Copy the key first, then (clipboard is cleared afterwards):
  python3 set_secret.py openai --from-clipboard

  # Multi-line secrets such as a Google service-account JSON:
  python3 set_secret.py vertex.service_account --from-file ~/Downloads/sa.json

  python3 set_secret.py asksage --check     # present/absent + where
  python3 set_secret.py asksage --delete

Zero-dependency alternative (macOS, in your own terminal) -- prompts with
no echo and writes the same Keychain item the TabPy calculation reads:

  security add-generic-password -U -s tableau-genai -a asksage -w
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import keystore  # noqa: E402

LABELS = {
    "openai": "OpenAI API key",
    "gateway": "LLM gateway API key",
    "asksage": "Ask Sage API key",
    "asksage.email": "Ask Sage account email",
    "bedrock": "Amazon Bedrock API key",
    "bedrock.access_key_id": "AWS access key ID",
    "bedrock.secret_access_key": "AWS secret access key",
    "bedrock.session_token": "AWS session token",
    "vertex": "Google Vertex AI API key",
    "vertex.service_account": "Google service-account JSON",
    "palantir": "Palantir Foundry token",
    "anthropic": "Anthropic API key",
    "azure-openai": "Azure OpenAI API key",
    "server.pat_secret": "Tableau Server PAT secret",
}


def _from_dialog(name: str) -> str:
    label = LABELS.get(name, name)
    msg = ("Paste your %s for the Tableau GenAI TabPy calculation.\n\n"
           "It goes straight into your macOS Keychain (service 'tableau-genai', "
           "account '%s') and is never displayed." % (label, name))
    script = [
        "on run argv",
        'set r to display dialog (item 1 of argv) default answer "" with hidden answer '
        'with title "Tableau GenAI - store secret" buttons {"Cancel", "Save"} '
        'default button "Save" with icon note',
        "return text returned of r",
        "end run",
    ]
    cmd = ["/usr/bin/osascript"]
    for line in script:
        cmd += ["-e", line]
    r = subprocess.run(cmd + [msg], capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit("Cancelled - nothing stored.")
    return r.stdout.rstrip("\n")


def _from_terminal(name: str) -> str:
    label = LABELS.get(name, name)
    if name.endswith(".email"):
        return input("%s: " % label)
    v1 = getpass.getpass("Paste %s (input hidden): " % label)
    v2 = getpass.getpass("Paste again to confirm: ")
    if v1 != v2:
        raise SystemExit("Values did not match - nothing stored.")
    return v1


def _from_clipboard(keep: bool) -> str:
    r = subprocess.run(["/usr/bin/pbpaste"], capture_output=True, text=True)
    v = r.stdout
    if not keep:
        subprocess.run(["/usr/bin/pbcopy"], input="", text=True)
    return v


def _from_file(path: str) -> str:
    raw = open(os.path.expanduser(path)).read()
    try:  # normalise JSON (service accounts) to one compact line
        return json.dumps(json.loads(raw), separators=(",", ":"))
    except ValueError:
        return raw.strip()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("name", help="secret name, e.g. asksage, openai, bedrock, "
                                 "vertex.service_account, asksage.email")
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--from-dialog", action="store_true",
                     help="hidden-input macOS dialog (default when no TTY on macOS)")
    src.add_argument("--from-terminal", action="store_true",
                     help="no-echo terminal prompt (default when a TTY is attached)")
    src.add_argument("--from-clipboard", action="store_true",
                     help="read the clipboard, then clear it")
    src.add_argument("--from-file", metavar="PATH")
    src.add_argument("--check", action="store_true", help="report present/absent only")
    src.add_argument("--delete", action="store_true")
    ap.add_argument("--keep-clipboard", action="store_true")
    ap.add_argument("--backend", default="auto",
                    choices=["auto", "macos-keychain", "keyring", "file"],
                    help="file = ~/.tableau-genai/secrets.json (chmod 600); use for "
                         "Linux/Docker TabPy hosts with no OS keystore")
    a = ap.parse_args(argv)

    if a.check:
        v, source = keystore.get_secret(a.name)
        print("%s: %s%s" % (a.name, keystore.describe(v), (" via " + source) if v else ""))
        return 0 if v else 1
    if a.delete:
        removed = keystore.delete_secret(a.name)
        print("%s: removed from %s" % (a.name, ", ".join(removed) or "nowhere (not found)"))
        return 0

    if a.from_file:
        value = _from_file(a.from_file)
    elif a.from_clipboard:
        value = _from_clipboard(a.keep_clipboard)
    elif a.from_terminal or (not a.from_dialog and sys.stdin.isatty()):
        value = _from_terminal(a.name)
    elif sys.platform == "darwin":
        value = _from_dialog(a.name)
    else:
        print("No TTY and no macOS dialog available. Run this in a terminal, "
              "or use --from-file / env var %s." % keystore.env_name(a.name),
              file=sys.stderr)
        return 2

    if not value.strip():
        print("Empty value - nothing stored.", file=sys.stderr)
        return 1
    backend = keystore.store_secret(a.name, value, backend=a.backend)
    print("%s: stored (%d chars) in %s" % (a.name, len(value.strip()), backend))
    del value
    return 0


if __name__ == "__main__":
    sys.exit(main())
