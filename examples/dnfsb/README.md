# Worked example: DNFSB weekly site reports

Source dashboard: `dnfsb_full.twb` — Defense Nuclear Facilities Safety Board
resident-inspector weekly reports (Hanford, Los Alamos, Oak Ridge, Pantex,
Savannah River; 2020–2024), built from public DNFSB PDFs with the
[tableau-prep-etl-skill](https://github.com/hal-force/tableau-prep-etl-skill)
into four Hyper extracts (Reports, Entities, IssueTypes, Trends). The
workbook and extracts are **not** in this repo; the files here show what each
step produced. Paths and the gateway hostname have been replaced with
placeholders.

| File | Step | What it shows |
|---|---|---|
| `profile.md` / `profile.json` | `profile_data.py dnfsb_full.twb` | scaffold found (Response, Payload, Grounding, Prompt, Select LLM, token count); risks: `PLAINTEXT_API_KEY`, `GROUNDING_SAVED_VALUE` (62 777 chars of report text saved in the .twb), `TLS_VERIFY_DISABLED`; Reports table 1 298 rows; recommended Payload ≈ 900 tokens/record → ~100 records per 90K budget |
| `prompts_spec.json` | written by Claude after asking about audience / workflow | 6 prompts + shared constraints (grounding only, cite Report ID + week-ending date, no speculation) |
| `prompts.json` / `prompts.md` | `compose_prompts.py prompts_spec.json` | the 7 Prompts members (6 + free text) with full text, Display As, token estimate |
| `patch_report.keystore.json` | `patch_workbook.py … -- --provider gateway --mode keystore --tls …` | output path, `embedded_secret: false`, size |
| `patch_report.demo.json` | same with `--mode demo` | placeholder build |

## Prompts

| Display As | Persona → audience |
|---|---|
| Executive Safety Brief - BLUF of top safety issues for leadership | senior DNFSB safety analyst → Board members / DOE-NNSA leaders |
| Recurring Issues and Trends - Themes that repeat across sites and time | trend analyst → technical staff choosing deep-dive topics |
| Root Cause and Contributing Factors - Apparent causes of events, grouped by type | DOE event investigator (HPI) → contractor safety managers |
| Corrective Action Tracker - Open commitments, owners and due dates | compliance analyst → field-office oversight staff |
| Site Risk Prioritization - Rank sites/facilities for oversight focus | risk analyst → review planners |
| Questions for the Contractor Meeting - Probing questions to ask site management | resident inspector → the inspector going into the weekly meeting |
| Free text (use the Prompt box) | — |

## Commands used

```sh
python3 scripts/ca_bundle.py --test https://llm-gateway.example.com/chat/completions
python3 scripts/profile_data.py dnfsb_full.twb --out examples/dnfsb
python3 scripts/compose_prompts.py examples/dnfsb/prompts_spec.json --out examples/dnfsb
for MODE in keystore demo; do
  SUFFIX="GenAI - Gateway"; [ $MODE = demo ] && SUFFIX="GenAI - Gateway DEMO"
  python3 scripts/patch_workbook.py dnfsb_full.twb \
    --prompts examples/dnfsb/prompts.json \
    --payload "Report ID=Report Id" "Week Ending=Week Ending Date" "Site=Site" \
              "Title=Report Title" "Report Content=Full Text" \
    --suffix "$SUFFIX" --report examples/dnfsb/patch_report.$MODE.json \
    -- --provider gateway --url https://llm-gateway.example.com/chat/completions \
       --mode $MODE --secret-name gateway --tls ~/.tableau-genai/ca-bundle.pem
done
python3 tests/test_workbook_calc.py "dnfsb_full (GenAI - Gateway).twb" --grounding-hyper Reports.hyper
```

The sample values in `profile.json` are excerpts of public DNFSB reports.
