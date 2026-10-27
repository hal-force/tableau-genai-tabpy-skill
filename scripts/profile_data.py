#!/usr/bin/env python3
"""Assess a Tableau workbook and/or its data for GenAI analysis design.

  python3 profile_data.py WORKBOOK.twb[x] [--out DIR]
  python3 profile_data.py DATA.hyper | DATA.csv | DATA.tdsx [--out DIR]

Writes DIR/profile.json and DIR/profile.md:
  * workbook structure: datasources, logical tables, columns (caption <->
    internal name), parameters, worksheets, dashboards, parameter actions
  * existing GenAI scaffold (Generative AI Response / Payload / Grounding /
    token count / Prompt / Select LLM) and risks such as a plaintext API key
    (detected, never printed) or a bloated saved Grounding value
  * per-table column statistics from the Hyper/CSV data (rows, nulls,
    distinct, text length, min/max, short samples) and a role guess
    (id, date, category, title, long_text, url, measure, text)
  * a recommended Payload field list per logical table with a token
    estimate per record and how many records fit the grounding budget

Needs tableauhyperapi for .hyper/.tdsx/.twbx extracts (run with the TabPy
interpreter or any Python that has it).
"""
from __future__ import annotations

import argparse
import atexit
import csv
import json
import os
import re
import shutil
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

LONG_TEXT_CHARS = 300
PLACEHOLDER = "PASTE_YOUR_API_KEY_HERE"

# Credential detection. Quote-agnostic on purpose: inside a Tableau formula a
# Python string may be written '...', "..." or ""..."" (Tableau doubles " in
# string literals), and keys also turn up in header dicts and parameter values.
_Q = r"""(?P<q>""|"|')"""
CRED_RX = [
    # api_key = '...', 'x-api-key': "...", ""password"": ""..."", client_secret=...
    re.compile(r"(?i)(?:api[_-]?key|access[_-]?token|auth[_-]?token|bearer[_-]?token|"
               r"secret[_-]?access[_-]?key|client[_-]?secret|pat[_-]?secret|password)"
               r"[\"']*\s*[:=]\s*" + _Q + r"(?P<v>[^\"'\s]{6,})(?P=q)"),
    re.compile(r"(?i)\bbearer\s+(?P<v>[A-Za-z0-9._~+/=-]{12,})"),
    re.compile(r"(?P<v>\bsk-[A-Za-z0-9_-]{20,})"),                    # OpenAI / Anthropic style
    re.compile(r"(?P<v>\b(?:AKIA|ASIA)[0-9A-Z]{16})\b"),              # AWS access key id
    re.compile(r"(?P<v>\bAIza[0-9A-Za-z_-]{35})"),                     # Google API key
    re.compile(r"(?P<v>\bgh[pousr]_[A-Za-z0-9]{36})"),                 # GitHub token
    re.compile(r"(?P<v>-----BEGIN [A-Z ]*PRIVATE KEY-----)"),
]
_DEMO_DICT = re.compile(r"DEMO_SECRETS\s*=\s*\{(?P<body>.*?)\n?\}", re.S)
_DEMO_VAL = re.compile(r":\s*" + _Q + r"(?P<v>(?:(?!(?P=q)).){6,}?)(?P=q)", re.S)


def credential_spans(text: str):
    """(start, end) spans of credential-looking values in text. Never returns values."""
    text = text or ""
    spans = []
    for rx in CRED_RX:
        for m in rx.finditer(text):
            if m.group("v") != PLACEHOLDER:
                spans.append(m.span("v"))
    for d in _DEMO_DICT.finditer(text):
        off = d.start("body")
        for m in _DEMO_VAL.finditer(d.group("body")):
            if m.group("v") != PLACEHOLDER:
                spans.append((off + m.start("v"), off + m.end("v")))
    merged = []
    for a, b in sorted(spans):
        if merged and a <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(b, merged[-1][1]))
        else:
            merged.append((a, b))
    return merged


def credential_values(text: str) -> list:
    """The literal values (for must-not-survive checks; never print these)."""
    return [text[a:b] for a, b in credential_spans(text)]


def has_plaintext_key(text: str) -> bool:
    return bool(credential_spans(text))


def redact(s: str) -> str:
    s = s or ""
    out, last = [], 0
    for a, b in credential_spans(s):
        out.append(s[last:a] + "<redacted %d chars>" % (b - a))
        last = b
    return "".join(out) + s[last:]


# ======================================================================
# Workbook structure
# ======================================================================
def load_twb(path: Path):
    """Return (ElementTree root, twb_path, extracted_dir or None)."""
    if path.suffix.lower() == ".twbx":
        tmp = Path(tempfile.mkdtemp(prefix="genai_twbx_"))
        atexit.register(shutil.rmtree, tmp, True)
        with zipfile.ZipFile(path) as z:
            z.extractall(tmp)
        twbs = [p for p in tmp.rglob("*.twb")]
        if not twbs:
            raise SystemExit("no .twb inside " + str(path))
        return ET.parse(twbs[0]).getroot(), twbs[0], tmp
    return ET.parse(path).getroot(), path, None


def param_ref_name(col) -> str:
    return col.get("name", "")


def workbook_structure(root) -> dict:
    out = {"datasources": [], "parameters": [], "worksheets": [], "dashboards": [],
           "parameter_actions": [], "filter_actions": 0}
    ds_root = root.find("datasources")
    for ds in (ds_root if ds_root is not None else []):
        if ds.tag != "datasource":
            continue
        if ds.get("name") == "Parameters":
            for c in ds.findall("column"):
                calc = c.find("calculation")
                value = c.get("value", "")
                members = [m.get("value", "") for m in c.findall("members/member")]
                out["parameters"].append({
                    "internal": c.get("name"), "caption": c.get("caption") or c.get("name"),
                    "datatype": c.get("datatype"), "domain": c.get("param-domain-type"),
                    "value_preview": value[:80] + ("..." if len(value) > 80 else ""),
                    "value_chars": len(value),
                    "members": [m[:80] for m in members][:20],
                    "member_aliases": [m.get("alias") for m in c.findall("members/member")][:20],
                    "has_formula": calc is not None,
                })
            continue
        info = {"name": ds.get("name"), "caption": ds.get("caption") or ds.get("name"),
                "connections": [], "relations": [], "columns": []}
        for nc in ds.iter("connection"):
            if nc.get("class") in (None, "federated"):
                continue
            info["connections"].append({k: nc.get(k) for k in
                                        ("class", "dbname", "filename", "server", "directory", "dbname")
                                        if nc.get(k)})
        for rel in ds.iter("relation"):
            if rel.get("type") == "table":
                info["relations"].append({"name": rel.get("name"), "table": rel.get("table"),
                                          "connection": rel.get("connection")})
        table_of, remote_of = {}, {}
        for m in ds.iter("metadata-record"):
            if m.get("class") != "column":
                continue
            ln = m.findtext("local-name")
            if ln:
                table_of[ln] = (m.findtext("parent-name") or "").strip("[]")
                remote_of[ln] = m.findtext("remote-name")
        seen = set()
        for c in ds.findall("column"):
            name = c.get("name")
            if name in seen:
                continue
            seen.add(name)
            calc = c.find("calculation")
            formula = calc.get("formula") if calc is not None else None
            info["columns"].append({
                "internal": name, "caption": c.get("caption") or name.strip("[]"),
                "datatype": c.get("datatype"), "role": c.get("role"),
                "table": table_of.get(name), "remote": remote_of.get(name),
                "is_calc": formula is not None,
                "formula": redact(formula)[:400] if formula and "SCRIPT_" not in formula else
                           ("<SCRIPT_ calc, %d chars>" % len(formula) if formula else None),
            })
        # columns only known from metadata (no <column> element)
        for ln, tbl in table_of.items():
            if ln not in seen:
                info["columns"].append({"internal": ln, "caption": ln.strip("[]"), "datatype": None,
                                        "role": None, "table": tbl, "remote": remote_of.get(ln),
                                        "is_calc": False, "formula": None})
        out["datasources"].append(info)

    ws_root = root.find("worksheets")
    for ws in (ws_root if ws_root is not None else []):
        used = set()
        for dep in ws.iter("datasource-dependencies"):
            for c in dep.findall("column"):
                used.add(c.get("caption") or c.get("name"))
        out["worksheets"].append({"name": ws.get("name"), "fields": sorted(u for u in used if u)})
    db_root = root.find("dashboards")
    for db in (db_root if db_root is not None else []):
        zones = list(db.iter("zone"))
        out["dashboards"].append({
            "name": db.get("name"),
            "sheets": sorted({z.get("name") for z in zones if z.get("name") and not z.get("type-v2")}),
            "parameter_controls": sorted({z.get("param") for z in zones if z.get("type-v2") == "paramctrl"}),
        })
    for act in root.iter("edit-parameter-action"):
        params = {p.get("name"): p.get("value") for p in act.iter("param")}
        out["parameter_actions"].append({
            "caption": act.get("caption"), "source_field": params.get("source-field"),
            "target_parameter": params.get("target-parameter"),
            "aggregation": (act.find("agg-type").get("type") if act.find("agg-type") is not None else None),
            "source_dashboard": (act.find("source").get("dashboard") if act.find("source") is not None else None),
        })
    out["filter_actions"] = sum(1 for _ in root.iter("action"))
    return out


def detect_scaffold(root, struct: dict) -> dict:
    """Locate the GenAI pieces by role, not by hard-coded internal names."""
    sc = {"response_calc": None, "payload_calc": None, "grounding_param": None,
          "token_count_calc": None, "prompt_param": None, "model_param": None,
          "prompts_param": None, "button_calc": None, "risks": []}
    params = {p["internal"]: p for p in struct["parameters"]}
    by_caption = {p["caption"].lower(): p for p in struct["parameters"]}

    for act in struct["parameter_actions"]:
        tgt = (act["target_parameter"] or "")
        m = re.match(r"\[Parameters\]\.(\[.+\])$", tgt)
        if m and m.group(1) in params:
            sc["grounding_param"] = params[m.group(1)]["caption"]
            sm = re.match(r"\[(.+?)\]\.\[[a-z]+:(.+?):[a-z]+\]$", act["source_field"] or "")
            if sm:
                sc["payload_internal"] = "[" + sm.group(2) + "]"
    if not sc["grounding_param"] and "grounding" in by_caption:
        sc["grounding_param"] = by_caption["grounding"]["caption"]
    for key, cap in (("prompt_param", "prompt"), ("model_param", "select llm"), ("prompts_param", "prompts")):
        if cap in by_caption:
            sc[key] = by_caption[cap]["caption"]

    for ds in root.iter("datasource"):
        if ds.get("name") == "Parameters":
            continue
        for c in ds.findall("column"):
            calc = c.find("calculation")
            if calc is None:
                continue
            f = calc.get("formula") or ""
            cap = c.get("caption") or c.get("name")
            prev = sc["response_calc"]
            if "SCRIPT_" in f and (prev is None or ((cap or "").lower() == "generative ai response"
                                                    and (prev["caption"] or "").lower() != "generative ai response")):
                url = re.search(r"API_URL\s*=\s*'([^']+)'", f)
                sc["response_calc"] = {"caption": cap, "internal": c.get("name"),
                                       "datasource": ds.get("name"),
                                       "api_url": url.group(1) if url else None,
                                       "tls_verify_disabled": "verify=False" in f or "TLS_VERIFY = False" in f,
                                       "formula_chars": len(f)}
                if has_plaintext_key(f):
                    sc["risks"].append("PLAINTEXT_API_KEY: '%s' embeds a live API key. Anyone with the "
                                       "workbook can read it. Rotate the key and switch to keystore mode." % cap)
            if c.get("name") == sc.get("payload_internal") or (cap or "").lower() == "payload":
                sc["payload_calc"] = {"caption": cap, "internal": c.get("name"), "formula": f,
                                      "datasource": ds.get("name")}
            if "LEN(" in f.upper() and sc["grounding_param"] and "/ 4" in f.replace("/4", "/ 4"):
                sc["token_count_calc"] = {"caption": cap, "internal": c.get("name")}
            if f.strip().startswith('"') and f.strip().endswith('"') and "analyze" in (cap or "").lower():
                sc["button_calc"] = {"caption": cap, "internal": c.get("name")}
        if sc["response_calc"]:
            break
    sc.pop("payload_internal", None)
    if sc["grounding_param"]:
        p = by_caption.get(sc["grounding_param"].lower())
        if p and p["value_chars"] > 2000:
            sc["risks"].append("GROUNDING_SAVED_VALUE: the '%s' parameter was saved holding %d chars of "
                               "record text (data at rest in the workbook, bigger file). The patcher "
                               "resets it to empty." % (p["caption"], p["value_chars"]))
    if sc["response_calc"] and sc["response_calc"]["tls_verify_disabled"]:
        sc["risks"].append("TLS_VERIFY_DISABLED: the current calc calls the LLM with verify=False.")
    sc["complete"] = all(sc[k] for k in ("response_calc", "payload_calc", "grounding_param",
                                         "token_count_calc", "prompt_param", "model_param"))
    return sc


# ======================================================================
# Data statistics
# ======================================================================
def classify(name: str, dtype: str, st: dict) -> str:
    n = name.lower()
    rows = max(st.get("non_null", 0), 1)
    distinct = st.get("distinct") or 0
    ratio = distinct / rows
    is_text = dtype in ("text", "string", "varchar", "object")
    samples = [str(s) for s in st.get("samples", [])]
    if distinct <= 1 or (is_text and (st.get("max_len") or 0) == 0):
        return "constant"
    if dtype in ("date", "timestamp", "timestamp_tz", "datetime", "datetime64[ns]") or \
            re.search(r"(date|_at$|time)", n) and not is_text:
        return "date"
    if is_text and samples and all(s.startswith(("http://", "https://", "/")) for s in samples[:3]):
        return "url"
    if is_text and (st.get("avg_len") or 0) >= LONG_TEXT_CHARS:
        return "long_text"
    if re.search(r"(^id$|_id$|^id_|uuid|_key$|^key$)", n) and ratio > 0.9:
        return "id"
    if is_text and re.search(r"(title|subject|headline|name|summary)", n) and ratio > 0.3:
        return "title"
    if is_text and re.search(r"date", n):
        return "date"
    if is_text and (distinct <= 60 or ratio < 0.05):
        return "category"
    if not is_text:
        return "id" if ratio > 0.98 and re.search(r"(id|key|num)", n) else "measure"
    return "text"


def profile_hyper(path: Path, max_rows: int) -> list:
    try:
        from tableauhyperapi import HyperProcess, Telemetry, Connection, CreateMode, TableName, escape_name
    except ImportError:
        return [{"error": "tableauhyperapi not installed in %s - rerun with the TabPy interpreter "
                          "(/Library/Frameworks/Python.framework/Versions/3.13/bin/python3) or "
                          "pip install tableauhyperapi" % sys.executable}]
    tmp = Path(tempfile.mkdtemp(prefix="genai_hyper_"))
    local = tmp / path.name
    shutil.copy2(path, local)  # never open the user's file directly
    tables = []
    try:
        with HyperProcess(Telemetry.DO_NOT_SEND_USAGE_DATA_TO_TABLEAU,
                          parameters={"log_dir": str(tmp)}) as hp:
            with Connection(hp.endpoint, str(local), CreateMode.NONE) as cx:
                for schema in cx.catalog.get_schema_names():
                    for tn in cx.catalog.get_table_names(schema):
                        tdef = cx.catalog.get_table_definition(tn)
                        n_rows = cx.execute_scalar_query("SELECT COUNT(*) FROM %s" % tn)
                        src = str(tn) if n_rows <= max_rows else "(SELECT * FROM %s LIMIT %d) s" % (tn, max_rows)
                        cols = []
                        for col in tdef.columns:
                            q = escape_name(col.name.unescaped)
                            dtype = str(col.type).lower().split("(")[0]
                            is_text = dtype in ("text", "varchar", "char")
                            st = {}
                            agg = ["COUNT(%s)" % q, "COUNT(DISTINCT %s)" % q]
                            if is_text:
                                agg += ["AVG(LENGTH(%s))" % q, "MAX(LENGTH(%s))" % q,
                                        "SUM(CASE WHEN %s = '' THEN 1 ELSE 0 END)" % q]
                            elif dtype not in ("bool",):
                                agg += ["MIN(%s)" % q, "MAX(%s)" % q]
                            r = cx.execute_list_query("SELECT %s FROM %s" % (", ".join(agg), src))[0]
                            st["non_null"], st["distinct"] = int(r[0]), int(r[1])
                            st["nulls"] = (min(n_rows, max_rows) - st["non_null"])
                            if is_text:
                                st["avg_len"] = round(float(r[2] or 0), 1)
                                st["max_len"] = int(r[3] or 0)
                                st["empty"] = int(r[4] or 0)
                            elif len(r) > 2:
                                st["min"], st["max"] = str(r[2]), str(r[3])
                            lim = 1 if (st.get("avg_len") or 0) >= LONG_TEXT_CHARS else 5
                            srows = cx.execute_list_query(
                                "SELECT DISTINCT %s FROM %s WHERE %s IS NOT NULL%s LIMIT %d"
                                % (q, src, q, (" AND %s <> ''" % q) if is_text else "", lim))
                            cut = 240 if lim == 1 else 80
                            st["samples"] = [str(s[0])[:cut] for s in srows]
                            st["role"] = classify(col.name.unescaped, "text" if is_text else dtype, st)
                            cols.append({"name": col.name.unescaped, "type": dtype, **st})
                        tables.append({"source": str(path), "table": str(tn),
                                       "table_name": tn.name.unescaped, "rows": int(n_rows),
                                       "profiled_rows": int(min(n_rows, max_rows)), "columns": cols})
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return tables


def profile_csv(path: Path, max_rows: int) -> list:
    with open(path, newline="", encoding="utf-8-sig", errors="replace") as fh:
        rdr = csv.DictReader(fh)
        rows = []
        for i, r in enumerate(rdr):
            if i >= max_rows:
                break
            rows.append(r)
        fields = rdr.fieldnames or []
    cols = []
    for f in fields:
        vals = [r.get(f) for r in rows]
        nn = [v for v in vals if v not in (None, "")]
        numeric = bool(nn) and all(re.fullmatch(r"-?\d+(\.\d+)?", v.strip() or "x") for v in nn[:500])
        datey = bool(nn) and all(re.fullmatch(r"\d{4}-\d{2}-\d{2}.*|\d{1,2}/\d{1,2}/\d{2,4}.*", v.strip())
                                 for v in nn[:200])
        dtype = "numeric" if numeric else ("date" if datey else "text")
        st = {"non_null": len(nn), "nulls": len(vals) - len(nn), "distinct": len(set(nn))}
        if dtype == "text":
            lens = [len(v) for v in nn] or [0]
            st["avg_len"], st["max_len"] = round(sum(lens) / len(lens), 1), max(lens)
        elif nn:
            st["min"], st["max"] = min(nn), max(nn)
        lim = 1 if (st.get("avg_len") or 0) >= LONG_TEXT_CHARS else 5
        cut = 240 if lim == 1 else 80
        st["samples"] = [v[:cut] for v in list(dict.fromkeys(nn))[:lim]]
        st["role"] = classify(f, dtype, st)
        cols.append({"name": f, "type": dtype, **st})
    return [{"source": str(path), "table": path.stem, "table_name": path.stem,
             "rows": len(rows), "profiled_rows": len(rows), "columns": cols,
             "note": "row count capped at --max-rows" if len(rows) >= max_rows else ""}]


# ======================================================================
# Payload recommendation
# ======================================================================
ROLE_ORDER = ["id", "date", "category", "title", "long_text"]


def recommend_payload(table: dict, captions: dict, budget: int) -> dict:
    cols = table["columns"]
    pick = []

    def first(role, pref=None, n=1):
        cands = [c for c in cols if c["role"] == role and c not in pick]
        if pref:
            cands.sort(key=lambda c: (0 if re.search(pref, c["name"].lower()) else 1, c.get("distinct", 0)))
        elif role == "long_text":
            cands.sort(key=lambda c: -(c.get("avg_len") or 0))
        elif role == "category":
            cands.sort(key=lambda c: (c.get("nulls", 0), c.get("distinct", 0)))
        return cands[:n]

    pick += first("id", r"(report|record|case|incident|ticket)")
    pick += first("date", r"(week|report|event|incident|created|date)")
    pick += first("category", r"(site|location|region|facility|type|category|status|unit)", n=3)
    pick += first("title")
    pick += first("long_text")
    fields = []
    chars = len(" Start of Record ") + len(" End of Record ")
    for c in pick:
        cap = captions.get(c["name"], c["name"].replace("_", " ").title())
        label = cap if c["role"] != "id" else cap.replace(" Id", " ID")
        if c["role"] == "long_text":
            label = cap.replace(" Text", " Content") if "text" in cap.lower() else cap
        avg = c.get("avg_len") or (10 if c["role"] in ("date", "measure") else 12)
        chars += len(label) + 4 + avg
        fields.append({"label": label, "field": cap, "column": c["name"], "role": c["role"],
                       "type": "string" if c["type"] in ("text", "string") else c["type"]})
    tpr = max(1, int(round(chars / 4.0)))
    return {"table": table["table_name"], "fields": fields, "est_tokens_per_record": tpr,
            "records_fit_budget": int(budget // tpr), "budget_tokens": budget,
            "total_records": table["rows"],
            "est_tokens_all_records": tpr * table["rows"]}


# ======================================================================
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("sources", nargs="+")
    ap.add_argument("--out", default=".")
    ap.add_argument("--max-rows", type=int, default=200000)
    ap.add_argument("--grounding-budget", type=int, default=90000)
    a = ap.parse_args(argv)

    prof = {"workbook": None, "scaffold": None, "tables": [], "payload_recommendations": [], "notes": []}
    data_files = []
    caption_maps = {}  # table_name -> {remote column -> caption}
    for s in a.sources:
        p = Path(os.path.expanduser(s))
        if not p.exists():
            raise SystemExit("not found: %s" % p)
        suf = p.suffix.lower()
        if suf in (".twb", ".twbx"):
            root, twb_path, tmpdir = load_twb(p)
            struct = workbook_structure(root)
            prof["workbook"] = {"path": str(p), "twb": str(twb_path), **struct}
            prof["scaffold"] = detect_scaffold(root, struct)
            for ds in struct["datasources"]:
                for c in ds["columns"]:
                    if c["table"] and c["remote"]:
                        caption_maps.setdefault(c["table"], {})[c["remote"]] = c["caption"]
                for conn in ds["connections"]:
                    db = conn.get("dbname") or ""
                    if conn.get("class") == "hyper" and db:
                        cand = Path(db)
                        if not cand.exists() and tmpdir:
                            hits = list(tmpdir.rglob(Path(db).name))
                            cand = hits[0] if hits else cand
                        if cand.exists():
                            data_files.append(cand)
                        else:
                            prof["notes"].append("extract not found locally: %s" % db)
                    elif conn.get("class") in ("textscan", "excel-direct") and conn.get("directory"):
                        cand = Path(conn["directory"]) / (conn.get("filename") or "")
                        if cand.suffix.lower() == ".csv" and cand.exists():
                            data_files.append(cand)
                    elif conn.get("class") in ("sqlproxy",):
                        prof["notes"].append("published data source on %s - download it with "
                                             "server_io.py download-datasource to profile rows"
                                             % conn.get("server"))
            if tmpdir:
                for h in tmpdir.rglob("*.hyper"):
                    if h not in data_files:
                        data_files.append(h)
        elif suf == ".tdsx":
            tmp = Path(tempfile.mkdtemp(prefix="genai_tdsx_"))
            atexit.register(shutil.rmtree, tmp, True)
            with zipfile.ZipFile(p) as z:
                z.extractall(tmp)
            data_files += list(tmp.rglob("*.hyper"))
        elif suf in (".hyper", ".csv"):
            data_files.append(p)
        elif p.is_dir():
            data_files += sorted(list(p.glob("*.hyper")) + list(p.glob("*.csv")))
        else:
            raise SystemExit("unsupported source type: %s" % p)

    seen = set()
    for f in data_files:
        if str(f) in seen:
            continue
        seen.add(str(f))
        prof["tables"] += profile_hyper(f, a.max_rows) if f.suffix.lower() == ".hyper" else profile_csv(f, a.max_rows)

    for t in prof["tables"]:
        if "error" in t:
            continue
        caps = caption_maps.get(t["table_name"], {})
        for c in t["columns"]:
            if c["name"] in caps:
                c["caption"] = caps[c["name"]]
        if any(c["role"] == "long_text" for c in t["columns"]) or any(c["role"] == "id" for c in t["columns"]):
            prof["payload_recommendations"].append(recommend_payload(t, caps, a.grounding_budget))
    prof["payload_recommendations"].sort(key=lambda r: -sum(1 for f in r["fields"] if f["role"] == "long_text"))

    os.makedirs(a.out, exist_ok=True)
    jp = os.path.join(a.out, "profile.json")
    with open(jp, "w") as fh:
        json.dump(prof, fh, indent=2, default=str)
    mp = os.path.join(a.out, "profile.md")
    with open(mp, "w") as fh:
        fh.write(render_md(prof))
    print(render_md(prof))
    print("\nwrote %s and %s" % (jp, mp))
    return 0


def render_md(p: dict) -> str:
    L = ["# GenAI data assessment", ""]
    wb = p.get("workbook")
    if wb:
        L += ["## Workbook: `%s`" % os.path.basename(wb["path"]), ""]
        for ds in wb["datasources"]:
            tables = sorted({c["table"] for c in ds["columns"] if c["table"]})
            L.append("- Data source **%s**: %d columns across logical tables %s; connections: %s" % (
                ds["caption"], len(ds["columns"]), ", ".join(tables) or "-",
                ", ".join("%s:%s" % (c.get("class"), os.path.basename(c.get("dbname") or c.get("filename") or c.get("server") or ""))
                          for c in ds["connections"])))
        L.append("- Dashboards: " + ", ".join("%s (sheets: %s)" % (d["name"], ", ".join(d["sheets"])) for d in wb["dashboards"]))
        L.append("- Parameters: " + ", ".join("%s [%s, %s]" % (x["caption"], x["datatype"], x["domain"]) for x in wb["parameters"]))
        L.append("- Parameter actions: " + ", ".join("%s: %s -> %s (%s)" % (x["caption"], x["source_field"], x["target_parameter"], x["aggregation"]) for x in wb["parameter_actions"]))
        sc = p.get("scaffold") or {}
        L += ["", "### Existing GenAI scaffold (%s)" % ("complete" if sc.get("complete") else "partial / absent")]
        for k in ("response_calc", "payload_calc", "grounding_param", "token_count_calc", "prompt_param",
                  "model_param", "prompts_param", "button_calc"):
            v = sc.get(k)
            if isinstance(v, dict):
                v = v.get("caption") + ("  -- " + v["formula"].replace("\n", " ") if v.get("formula") else "") + \
                    ("  (url: %s)" % v["api_url"] if v.get("api_url") else "")
            L.append("- %s: %s" % (k, v or "**missing**"))
        for r in sc.get("risks", []):
            L.append("- RISK %s" % r)
    for t in p["tables"]:
        if "error" in t:
            L += ["", "ERROR: " + t["error"]]
            continue
        L += ["", "## Table `%s` (%s rows) - %s" % (t["table_name"], t["rows"], os.path.basename(t["source"])), "",
              "| column | caption | type | role | nulls | distinct | avg len | sample |", "|---|---|---|---|---|---|---|---|"]
        for c in t["columns"]:
            s = (c.get("samples") or [""])[0].replace("|", "/").replace("\n", " ")[:60]
            L.append("| %s | %s | %s | %s | %s | %s | %s | %s |" % (
                c["name"], c.get("caption", ""), c["type"], c["role"], c.get("nulls"), c.get("distinct"),
                c.get("avg_len", ""), s))
    if p["payload_recommendations"]:
        L += ["", "## Recommended Payload (per logical table - a row-level calc cannot mix tables)"]
        for r in p["payload_recommendations"]:
            L.append("- **%s**: %s  ->  ~%d tokens/record, ~%d records fit a %d-token budget (all %d records = ~%d tokens)" % (
                r["table"], ", ".join("%s(%s)" % (f["field"], f["role"]) for f in r["fields"]),
                r["est_tokens_per_record"], r["records_fit_budget"], r["budget_tokens"],
                r["total_records"], r["est_tokens_all_records"]))
    for n in p["notes"]:
        L.append("- NOTE " + n)
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    sys.exit(main())
