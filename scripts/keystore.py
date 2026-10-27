"""Shared secret store for the tableau-genai-tabpy skill.

The generated TabPy calculation (keystore mode) resolves secrets with the
same lookup chain implemented here, so anything stored with
``set_secret.py`` is visible to the calculation at call time:

  1. Environment variable  TABLEAU_GENAI_<NAME>   (NAME upper-cased, non
     alphanumerics -> '_', e.g. asksage.email -> TABLEAU_GENAI_ASKSAGE_EMAIL)
  2. macOS Keychain         service 'tableau-genai', account '<name>'
  3. OS keyring             (python 'keyring' package: Windows Credential
                             Manager, Linux Secret Service) if installed
  4. JSON file              $TABLEAU_GENAI_SECRETS_FILE or
                             ~/.tableau-genai/secrets.json (chmod 600)

Values that are not plain single-line ASCII (service-account JSON, keys
with quotes) are stored as 'b64:<base64>' so they survive the Keychain
CLI; ``get_secret`` decodes them transparently.

Nothing in this module ever prints a secret value.
"""
from __future__ import annotations

import base64
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path
from typing import Optional

SERVICE = "tableau-genai"
SECURITY = "/usr/bin/security"
# Characters that pass through `security -i` quoting untouched. Anything
# else (quotes, spaces, backslashes, newlines, JSON) is stored base64.
_SAFE = re.compile(r"^[A-Za-z0-9._~+/=:@%-]+$")


def env_name(name: str) -> str:
    return "TABLEAU_GENAI_" + "".join(c if c.isalnum() else "_" for c in name).upper()


def secrets_file() -> Path:
    return Path(os.environ.get("TABLEAU_GENAI_SECRETS_FILE")
                or os.path.expanduser("~/.tableau-genai/secrets.json"))


def _decode(v: Optional[str]) -> str:
    v = (v or "").strip()
    if v.startswith("b64:"):
        try:
            return base64.b64decode(v[4:]).decode("utf-8").strip()
        except Exception:
            return ""
    return v


def _encode(v: str) -> str:
    """Keychain-CLI-safe representation of a value."""
    if _SAFE.match(v) and '"' not in v and "\\" not in v:
        return v
    return "b64:" + base64.b64encode(v.encode("utf-8")).decode("ascii")


def is_macos_keychain() -> bool:
    return sys.platform == "darwin" and os.path.exists(SECURITY)


# --------------------------------------------------------------------- read
def get_secret(name: str, service: str = SERVICE) -> tuple[str, str]:
    """Return (value, source). value == '' when not found."""
    v = _decode(os.environ.get(env_name(name), ""))
    if v:
        return v, "env:" + env_name(name)
    if is_macos_keychain():
        try:
            r = subprocess.run([SECURITY, "find-generic-password", "-s", service,
                                "-a", name, "-w"],
                               capture_output=True, text=True, timeout=15)
            if r.returncode == 0:
                v = _decode(r.stdout)
                if v:
                    return v, "macos-keychain"
        except Exception:
            pass
    try:
        import keyring  # type: ignore
        v = _decode(keyring.get_password(service, name))
        if v:
            return v, "keyring"
    except Exception:
        pass
    p = secrets_file()
    try:
        data = json.loads(p.read_text())
        v = _decode(str(data.get(name, "") or ""))
        if v:
            return v, "file:" + str(p)
    except Exception:
        pass
    return "", ""


# -------------------------------------------------------------------- write
def store_secret(name: str, value: str, backend: str = "auto",
                 service: str = SERVICE) -> str:
    """Persist a secret. Returns the backend used. Never echoes the value."""
    value = value.strip()
    if not value:
        raise ValueError("empty value - nothing stored")
    if backend == "auto":
        backend = "macos-keychain" if is_macos_keychain() else (
            "keyring" if _has_keyring() else "file")

    if backend == "macos-keychain":
        enc = _encode(value)
        # Feed the command through `security -i` on stdin so the secret
        # never appears in argv (ps / shell history).
        cmd = ('add-generic-password -U -s "%s" -a "%s" -l "%s" -w "%s"\n'
               % (service, name, "Tableau GenAI: " + name, enc))
        r = subprocess.run([SECURITY, "-i"], input=cmd, capture_output=True,
                           text=True, timeout=30)
        got, _ = get_secret_from_keychain(name, service)
        if got != value:
            raise RuntimeError("Keychain write did not verify (security rc=%s). "
                               "Is the login keychain unlocked?" % r.returncode)
        return backend

    if backend == "keyring":
        import keyring  # type: ignore
        keyring.set_password(service, name, _encode(value))
        return backend

    if backend == "file":
        p = secrets_file()
        p.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(p.parent, 0o700)
        data = {}
        if p.exists():
            try:
                data = json.loads(p.read_text())
            except Exception:
                data = {}
        data[name] = _encode(value)
        fd = os.open(str(p), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            json.dump(data, fh, indent=2)
        os.chmod(p, stat.S_IRUSR | stat.S_IWUSR)
        return "file:" + str(p)

    raise ValueError("unknown backend " + backend)


def get_secret_from_keychain(name: str, service: str = SERVICE) -> tuple[str, str]:
    if not is_macos_keychain():
        return "", ""
    r = subprocess.run([SECURITY, "find-generic-password", "-s", service, "-a",
                        name, "-w"], capture_output=True, text=True, timeout=15)
    return (_decode(r.stdout), "macos-keychain") if r.returncode == 0 else ("", "")


def delete_secret(name: str, service: str = SERVICE) -> list[str]:
    removed = []
    if is_macos_keychain():
        r = subprocess.run([SECURITY, "delete-generic-password", "-s", service,
                            "-a", name], capture_output=True, text=True)
        if r.returncode == 0:
            removed.append("macos-keychain")
    if _has_keyring():
        try:
            import keyring  # type: ignore
            keyring.delete_password(service, name)
            removed.append("keyring")
        except Exception:
            pass
    p = secrets_file()
    if p.exists():
        try:
            data = json.loads(p.read_text())
            if name in data:
                del data[name]
                p.write_text(json.dumps(data, indent=2))
                removed.append("file")
        except Exception:
            pass
    return removed


def _has_keyring() -> bool:
    try:
        import keyring  # type: ignore  # noqa: F401
        return True
    except Exception:
        return False


def describe(value: str) -> str:
    """Non-revealing description of a secret, safe to print."""
    if not value:
        return "absent"
    return "present (%d chars)" % len(value)
