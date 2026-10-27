#!/usr/bin/env python3
"""Tableau Server / Cloud I/O for the GenAI skill (download to profile, publish result).

  python3 server_io.py list-workbooks   [--search TEXT] [--limit 25]
  python3 server_io.py list-datasources [--search TEXT] [--limit 25]
  python3 server_io.py download-workbook   NAME_OR_LUID --out DIR [--no-extract]
  python3 server_io.py download-datasource NAME_OR_LUID --out DIR
  python3 server_io.py publish FILE.twb[x] --project NAME [--name NAME] [--overwrite]

Credentials (never printed), first match wins:
  1. env TABLEAU_SERVER_URL / _SITE / _PAT_NAME / _PAT_SECRET
  2. the tableau-prep-etl skill's store (Keychain service 'tableau-prep-etl',
     accounts url, site, pat-name, pat-secret) -- set up once by that skill
  3. this skill's keystore names server.url / server.site / server.pat_name /
     server.pat_secret (python3 set_secret.py server.pat_secret)

publish refuses any workbook whose GenAI calc embeds a credential (demo
builds). Published workbooks call the TabPy configured on the SERVER
(Analytics Extensions); the keystore lookup then runs on that host -- see
reference/security.md.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import zipfile
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import keystore  # noqa: E402

KEYS = ("TABLEAU_SERVER_URL", "TABLEAU_SERVER_SITE", "TABLEAU_SERVER_PAT_NAME", "TABLEAU_SERVER_PAT_SECRET")


def load_creds() -> dict:
    env = {k: os.environ.get(k, "") for k in KEYS}
    src = "env"
    if not all(env[k] for k in KEYS if k != "TABLEAU_SERVER_SITE"):
        # same Keychain items the tableau-prep-etl skill's server_creds.py writes
        prep = dict(zip(KEYS, ("url", "site", "pat-name", "pat-secret")))
        got = {k: keystore.get_secret(acct, "tableau-prep-etl")[0] for k, acct in prep.items() if not env[k]}
        if any(got.values()):
            env.update({k: v for k, v in got.items() if v})
            src = "tableau-prep-etl Keychain"
    if not all(env[k] for k in KEYS if k != "TABLEAU_SERVER_SITE"):
        for k, nm in zip(KEYS, ("server.url", "server.site", "server.pat_name", "server.pat_secret")):
            if not env[k]:
                env[k] = keystore.get_secret(nm)[0]
        src = "tableau-genai keystore"
    missing = [k for k in KEYS if k != "TABLEAU_SERVER_SITE" and not env[k]]
    if missing:
        raise SystemExit("Tableau Server credentials missing: %s. Store them with set_secret.py "
                         "server.url / server.pat_name / server.pat_secret (or the prep skill's "
                         "server_creds.py)." % ", ".join(missing))
    env["_source"] = src
    return env


def signin():
    import tableauserverclient as TSC
    c = load_creds()
    srv = TSC.Server(c["TABLEAU_SERVER_URL"], use_server_version=True)
    auth = TSC.PersonalAccessTokenAuth(c["TABLEAU_SERVER_PAT_NAME"], c["TABLEAU_SERVER_PAT_SECRET"],
                                       site_id=c["TABLEAU_SERVER_SITE"] or "")
    srv.auth.sign_in(auth)
    print("signed in to %s (site %r) using %s" % (c["TABLEAU_SERVER_URL"], c["TABLEAU_SERVER_SITE"], c["_source"]),
          file=sys.stderr)
    return srv, TSC


def _find(items, key):
    key_l = key.lower()
    exact = [i for i in items if i.id == key or (i.name or "").lower() == key_l]
    if exact:
        return exact[0]
    part = [i for i in items if key_l in (i.name or "").lower()]
    if len(part) == 1:
        return part[0]
    if not part:
        raise SystemExit("nothing named %r" % key)
    raise SystemExit("ambiguous %r: %s" % (key, ", ".join("%s (%s)" % (i.name, i.id) for i in part[:10])))


def _list(endpoint, TSC, search, limit):
    opts = TSC.RequestOptions(pagesize=min(max(limit, 1), 1000))
    if search:
        opts.filter.add(TSC.Filter(TSC.RequestOptions.Field.Name, TSC.RequestOptions.Operator.Has, search))
    items, _ = endpoint.get(opts)
    return items[:limit]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for n in ("list-workbooks", "list-datasources"):
        s = sub.add_parser(n)
        s.add_argument("--search")
        s.add_argument("--limit", type=int, default=25)
    for n in ("download-workbook", "download-datasource"):
        s = sub.add_parser(n)
        s.add_argument("key")
        s.add_argument("--out", default=".")
        s.add_argument("--no-extract", action="store_true")
    s = sub.add_parser("publish")
    s.add_argument("file")
    s.add_argument("--project", required=True)
    s.add_argument("--name")
    s.add_argument("--overwrite", action="store_true")
    a = ap.parse_args(argv)

    if a.cmd == "publish":
        from profile_data import has_plaintext_key
        p = Path(os.path.expanduser(a.file))
        if p.suffix.lower() in (".twbx", ".tdsx"):
            with zipfile.ZipFile(p) as z:   # scan every workbook / data source member
                raw = "\n".join(z.read(n).decode("utf-8", "replace") for n in z.namelist()
                                 if n.lower().endswith((".twb", ".tds")))
        else:
            raw = p.read_text(encoding="utf-8")
        import html
        if has_plaintext_key(html.unescape(raw)):
            raise SystemExit("refusing to publish: this workbook embeds a credential-looking literal "
                             "(a demo build with an injected key, or a hand-written key). Publish the "
                             "keystore build instead.")
        if re.search(r"class='hyper'[^>]*dbname='/", raw) and p.suffix.lower() == ".twb":
            print("WARNING: .twb points at local .hyper files the server cannot read; package as "
                  ".twbx in Tableau Desktop (File > Export Packaged Workbook) or publish the data "
                  "sources first.", file=sys.stderr)

    srv, TSC = signin()
    try:
        if a.cmd == "list-workbooks":
            for w in _list(srv.workbooks, TSC, a.search, a.limit):
                print("%s  %-50s  project=%s  updated=%s" % (w.id, w.name, w.project_name, w.updated_at))
        elif a.cmd == "list-datasources":
            for d in _list(srv.datasources, TSC, a.search, a.limit):
                print("%s  %-50s  project=%s  type=%s" % (d.id, d.name, d.project_name, d.datasource_type))
        elif a.cmd == "download-workbook":
            items = list(TSC.Pager(srv.workbooks))
            w = _find(items, a.key)
            os.makedirs(a.out, exist_ok=True)
            path = srv.workbooks.download(w.id, filepath=a.out, include_extract=not a.no_extract)
            print(path)
        elif a.cmd == "download-datasource":
            items = list(TSC.Pager(srv.datasources))
            d = _find(items, a.key)
            os.makedirs(a.out, exist_ok=True)
            path = srv.datasources.download(d.id, filepath=a.out, include_extract=not a.no_extract)
            print(path)
        elif a.cmd == "publish":
            projects = list(TSC.Pager(srv.projects))
            proj = next((p for p in projects if p.name.lower() == a.project.lower()), None)
            if proj is None:
                raise SystemExit("project %r not found" % a.project)
            name = a.name or Path(a.file).stem
            wb = TSC.WorkbookItem(project_id=proj.id, name=name)
            mode = TSC.Server.PublishMode.Overwrite if a.overwrite else TSC.Server.PublishMode.CreateNew
            res = srv.workbooks.publish(wb, os.path.expanduser(a.file), mode)
            print("published %s (%s) -> %s" % (res.name, res.id, res.webpage_url))
    finally:
        srv.auth.sign_out()
    return 0


if __name__ == "__main__":
    sys.exit(main())
