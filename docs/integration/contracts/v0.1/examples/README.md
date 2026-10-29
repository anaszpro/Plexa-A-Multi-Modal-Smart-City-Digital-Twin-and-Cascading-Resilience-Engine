# Contract examples

These files illustrate `0.1.0`, not completed integration adapters. Package IDs are
proposed identifiers; no release is declared runnable by their appearance here.
Validate each `*.request.json` against `../module_request.schema.json` and each
`*.result.json` against `../module_result.schema.json` using JSON Schema 2020-12
with date-time format checking enabled. A result's `request_sha256` hashes the
exact companion request file bytes, including its final newline.

| Pair | Basis |
| --- | --- |
| `climate_legacy` | Copies the Ida row and expected prediction from the existing climate examples, wrapped in the proposed interface. Inference was not rerun for this document. Its time remains unresolved and its road has no shared mapping; it is not eligible for a historical cascade |
| `safety_311` | Synthetic request/response illustration of the supplied street-flooding rule and a skipped noise report. Coordinates and report keys are fixtures. No pipeline execution or spatial match is claimed |
| `safety_text` | Synthetic schema illustration. The score is invented to illustrate its type; it is not a measured model prediction. A second record illustrates a per-record inference error |
| `mobility` | Synthetic quantiles and road ID illustrating the future interface. No serving model, road mapping or Ida traffic prediction is claimed |

Only the climate example is copied from a supplied historical feature row. All
other inputs explicitly say `source.kind=synthetic`. The documented local-to-UTC
times in synthetic fixtures define their artificial scenario clock, not evidence
about actual historical reports. A data-kind label does not make fixtures usable
as historical observations.

The schemas check fields, types, allowed operations/statuses, coordinate bounds
and pairs, UTC formatting, and typed scores. Runtime acceptance must additionally
check header/input-hash matches, exact request/result ID sets, uniqueness, class
score sums, quantile ordering, threshold decisions, package hashes, source lineage,
mapping, time-window membership and cross-module evidence dependence. Examples
passing a schema check do not establish these real-world dependencies.

Local validation on 2026-10-16: both schemas pass their 2020-12 meta-schema check;
all four request/result pairs pass validation, byte-hash/header/ID checks and
applicable score-sum, threshold and quantile checks. Twenty-five negative schema
cases were rejected. Date-time checking used Python's datetime parser with an
explicit UTC check because the installed JSON Schema library had no optional
date-time checker registered. All local document links resolve. These checks ran
without loading module models, installing dependencies or contacting services.

See the [contract](../../../integration_contract_v0.1.md) and
local evidence review (historical reference: `docs/integration/additional_resources_review.md`).
