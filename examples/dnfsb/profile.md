# GenAI data assessment

## Workbook: `dnfsb_full.twb`

- Data source **Reports+ (Multiple Connections)**: 55 columns across logical tables Entities, IssueTypes, Reports, Trends; connections: hyper:DNFSB Reports 2020-2024.hyper, hyper:DNFSB Report Entities (LLM) 2020-2024.hyper, hyper:DNFSB Issue Types 2020-2024.hyper, hyper:DNFSB Issue Type Trends 2020-2024.hyper
- Dashboards: Report Analysis (sheets: AI Response, Agentic AI Button, Big Number, Extracted Entities, Issue, Report Date, Report by Site, Reports)
- Parameters: Grounding [string, any], Prompt [string, any], Select LLM [string, list]
- Parameter actions: Dynamic Grounding: [federated.03lxilz0vx55r91dkm5vk1yd1r0l].[none:Calculation_3083839871128268802:nk] -> [Parameters].[Gen AI Prompt (copy)_2308939257662406656] (concatenate)

### Existing GenAI scaffold (complete)
- response_calc: Generative AI Response  (url: https://llm-gateway.example.com/chat/completions)
- payload_calc: Payload  -- " Start of Record " + " Report ID: " + IFNULL([report_id], "N/A") + " Report Content: " + IFNULL([full_text], "N/A") + " End of Record "
- grounding_param: Grounding
- token_count_calc: Grounding Token Count
- prompt_param: Prompt
- model_param: Select LLM
- prompts_param: **missing**
- button_calc: Analyze Using Agentic AI
- RISK PLAINTEXT_API_KEY: 'Generative AI Response' embeds a live API key. Anyone with the workbook can read it. Rotate the key and switch to keystore mode.
- RISK GROUNDING_SAVED_VALUE: the 'Grounding' parameter was saved holding 62777 chars of record text (data at rest in the workbook, bigger file). The patcher resets it to empty.
- RISK TLS_VERIFY_DISABLED: the current calc calls the LLM with verify=False.

## Table `Reports` (1298 rows) - DNFSB Reports 2020-2024.hyper

| column | caption | type | role | nulls | distinct | avg len | sample |
|---|---|---|---|---|---|---|---|
| report_id | Report Id | text | id | 0 | 1298 | 12.0 | 003b243aa88b |
| report_title | Report Title | text | title | 0 | 1298 | 40.3 | Hanford - Week Ending July 19 2024.pdf |
| site | Site | text | category | 0 | 5 | 9.2 | Hanford |
| week_ending_date | Week Ending Date | date | date | 0 | 261 |  | 2023-02-24 |
| source_url | Source Url | text | url | 0 | 1298 | 108.6 | https://www.dnfsb.gov/sites/default/files/2024-12/Hanford%20 |
| file_name | File Name | text | constant | 0 | 1 | 0.0 |  |
| local_path | Local Path | text | url | 0 | 1298 | 110.2 | ~/dnfsb_flow/outputs/pdfs_ |
| page_count | Page Count | big_int | measure | 0 | 2 |  | 1 |
| size_bytes | Size Bytes | big_int | constant | 1298 | 0 |  |  |
| retrieved_at | Retrieved At | text | constant | 0 | 1 | 0.0 |  |
| full_text | Full Text | text | long_text | 0 | 1297 | 3423.5 | August 23, 2024 DEFENSE NUCLEAR FACILITIES SAFETY BOARD TO:  |

## Table `Entities` (40318 rows) - DNFSB Report Entities (LLM) 2020-2024.hyper

| column | caption | type | role | nulls | distinct | avg len | sample |
|---|---|---|---|---|---|---|---|
| report_id | report id (Entities) | text | category | 0 | 1296 | 12.0 | 4237c2ba1fe9 |
| source_url | source url (Entities) | text | url | 0 | 1296 | 108.7 | https://www.dnfsb.gov/sites/default/files/document/22451/Sav |
| report_title | report title (Entities) | text | category | 0 | 1296 | 40.4 | Hanford Week Ending May 15 2020.pdf |
| site | site (Entities) | text | category | 0 | 5 | 9.2 | Hanford |
| week_ending_date | week ending date (Entities) | date | date | 0 | 261 |  | 2023-02-24 |
| extractor | Extractor | text | constant | 0 | 1 | 3.0 | LLM |
| category | Category | text | category | 0 | 11 | 6.5 | Organization |
| entity_type | Entity Type | text | category | 0 | 13 | 4.6 | PRODUCT |
| entity_text | Entity Text | text | text | 0 | 15886 | 25.0 | 231-Z facility |
| canonical_entity | Canonical Entity | text | text | 0 | 14483 | 30.4 | Hazard Review Board Q&A — REDOX fissile material sampling wo |
| normalized_value | Normalized Value | text | category | 0 | 1003 | 1.1 | 1972-01-01 |
| mention_count | Mention Count | double | measure | 0 | 18 |  | 4.0 |

## Table `IssueTypes` (5981 rows) - DNFSB Issue Types 2020-2024.hyper

| column | caption | type | role | nulls | distinct | avg len | sample |
|---|---|---|---|---|---|---|---|
| report_id | report id (IssueTypes) | text | text | 0 | 1280 | 12.0 | 003b243aa88b |
| source_url | source url (IssueTypes) | text | url | 0 | 1280 | 108.6 | https://www.dnfsb.gov/sites/default/files/2024-12/Hanford%20 |
| report_title | report title (IssueTypes) | text | text | 0 | 1280 | 40.3 | Hanford - Week Ending July 19 2024.pdf |
| site | site (IssueTypes) | text | category | 0 | 5 | 9.2 | Hanford |
| week_ending_date | week ending date (IssueTypes) | date | date | 0 | 261 |  | 2023-02-24 |
| extractor | extractor (IssueTypes) | text | constant | 0 | 1 | 3.0 | LLM |
| category | category (IssueTypes) | text | constant | 0 | 1 | 5.0 | Theme |
| entity_type | entity type (IssueTypes) | text | constant | 0 | 1 | 5.0 | THEME |
| entity_text | entity text (IssueTypes) | text | text | 0 | 2602 | 27.3 | Electrostatic Discharge hazard evaluation |
| canonical_entity | canonical entity (IssueTypes) | text | text | 0 | 1098 | 22.0 | Chemical Hazards — Spontaneous Exothermic Reactions |
| normalized_value | normalized value (IssueTypes) | text | constant | 0 | 1 | 0.0 |  |
| mention_count | mention count (IssueTypes) | double | measure | 0 | 9 |  | 4.0 |
| issue_type | Issue Type | text | category | 0 | 28 | 21.2 | Emergency Management |
| issue_directive_id | Issue Directive Id | text | category | 0 | 20 | 14.4 | DOE O 474.2 |
| issue_directive_title | Issue Directive Title | text | category | 0 | 28 | 43.6 | Facility Safety — Deflagration / Combustible Gas Controls |
| signal_level | Signal Level | text | constant | 0 | 1 | 8.0 | category |
| year | Year | big_int | measure | 0 | 5 |  | 2024 |

## Table `Trends` (694 rows) - DNFSB Issue Type Trends 2020-2024.hyper

| column | caption | type | role | nulls | distinct | avg len | sample |
|---|---|---|---|---|---|---|---|
| site | site (Trends) | text | category | 0 | 6 | 9.6 | Hanford |
| issue_type | issue type (Trends) | text | category | 0 | 28 | 24.8 | Emergency Management |
| issue_directive_id | issue directive id (Trends) | text | category | 30 | 19 | 15.0 | DOE O 433.1B |
| year | year (Trends) | big_int | measure | 0 | 5 |  | 2024 |
| n_reports | N Reports | big_int | measure | 0 | 77 |  | 5 |
| n_theme_rows | N Theme Rows | big_int | measure | 0 | 88 |  | 5 |
| mentions | Mentions | big_int | measure | 0 | 123 |  | 69 |

## Recommended Payload (per logical table - a row-level calc cannot mix tables)
- **Reports**: Report Id(id), Week Ending Date(date), Site(category), Report Title(title), Full Text(long_text)  ->  ~900 tokens/record, ~100 records fit a 90000-token budget (all 1298 records = ~1168200 tokens)
