# Plexa integration contract v0.1

**Status:** proposed for team review, 2026-10-16. **Schema version:** `0.1.0`.
**Integration owner:** Anas. This document specifies the module boundary; it is
not the final project planning report or evidence that the adapters already exist.

In plain language: everyone produces the same outer message, while each module
keeps its own meaningful inputs and results. The backend can then identify the
source, align the place and time, and explain every cascade effect.

## 1. Scope and ownership

The selected scenario is **Hurricane Ida, 2021**, using frozen local data.
The initial boundary is JSON files exchanged between independently runnable
module packages and Anas's backend. Each package may keep its own environment.
A future API can carry these same messages; no shared Python environment, database
rebuild, web service or citywide model retraining is required by this contract.

Proposed focus window: **2021-09-01 00:00 through 2021-09-03 00:00, America/New_York**,
equivalent to **2021-09-01T04:00:00Z through 2021-09-03T04:00:00Z**, end exclusive.
Freeze the actual road subset and available hours after inspecting supplied data.
Additional rainfall history needed for feature construction sits outside this
display window. A scenario name alone does not establish shared data coverage.

| Owner | Decides and delivers |
| --- | --- |
| Anas | Shared schema, source/time normalization, road crosswalk, backend, mobility serving policy, cascade rules and integration acceptance |
| Kaleem | Climate preprocessing, truthful target/feature definitions, inference package, prepared scenario rows, native road geometry and provenance; energy later |
| Arbaz | Safety preprocessing, classifier/rules, stable report identity, deterministic outputs, package/assets and optional guidance retrieval |
| Kaleem + Arbaz | Frontend consuming backend-owned data, including units, provenance, unresolved states and assumption labels |

Owners may change module internals while honoring the contract. Changes to target
meaning, required inputs, units, IDs, time semantics or output interpretation need
Anas's agreement with the affected owner. Supplying an adapter does not transfer
ownership of all backend work to the module owner: the adapter is the module's
local inference/export entry point; shared mapping and API work stay with Anas.

## 2. What can enter v0.1

| Operation | Meaning | Readiness and restrictions |
| --- | --- | --- |
| `climate.legacy_classify` | Same-stored-hour Sewer-report proxy from four legacy numeric features | Existing model/wrapper; valid for an explicitly labeled retrospective demonstration. Prepared Ida rows and time/geometry provenance still needed |
| `safety.classify_311` | One structured report accepted/rejected by versioned rules | Preferred first safety path for Ida. Code is supplied; package, ID preservation and frozen input export need work |
| `safety.classify_text` | Text classifier gate, then heuristic type/severity and optional retrieval | Optional until encoder/assets, package and fixed request/output pair are verified |
| `mobility.typical_volume` | Typical vehicles per hour for a road/direction/calendar hour | Interface reserved here; Anas must select/export a serving package and context before claiming availability |

The corrected climate model is **preparation only**. Its future target is a
qualifying street/highway flooding report in `[t, t+1h)`, using eight different
features and OSM-derived physical IDs. It requires a new operation/package and
contract revision; it cannot replace the legacy model's weights silently.
Energy has no supplied model and is outside this version's executable operations.

Ida is a demonstration scenario, not an established untouched evaluation event.
The climate legacy split is random, and the corrected configuration includes Ida
in training. Mobility output describes typical volume, not observed Ida traffic,
speed, congestion, road capacity or a response to flooding.

## 3. Exchange files and common identity

Each frozen run contains `request.json`, `result.json`, and the referenced module
`release_manifest.json`. Proposed command convention, to be supplied by the owner:

```text
python <module_entrypoint> --input request.json --output result.json
```

This is a required future adapter convention, not a command that works everywhere
today. The existing climate wrapper accepts its own smaller `records` format;
the adapter translates the common request into that format.

Machine-readable definitions and worked examples are in
[contracts/v0.1](contracts/v0.1):
[request schema](contracts/v0.1/module_request.schema.json),
[result schema](contracts/v0.1/module_result.schema.json), and
[example notes](contracts/v0.1/examples/README.md).
UTF-8 JSON only: finite numbers, explicit `null`, no NaN/Infinity or numeric strings.
Object field order is irrelevant. IDs are nonempty strings and retain leading zeros.

| Request field | Requirement |
| --- | --- |
| `schema_version` | Exactly `0.1.0` |
| `scenario_id` | Frozen scenario identifier, initially `ida-2021-static-v1` |
| `run_id` | Unique identifier for this input/package combination; unchanged on an exact replay |
| `operation` | One operation from section 2 per file |
| `package_id` | Exact immutable package release, not a floating `latest` |
| `records` | Nonempty array; every record has a unique `request_id` within the run |

Every request record has `request_id`, `source`, `time`, `location`, and `input`.

| Context | Fields and meaning |
| --- | --- |
| `source` | `dataset_id`, `snapshot_id`, `record_id`, `kind` (`historical` or `synthetic`). All are required; snapshot files and selection rules must be listed in the release/scenario manifests |
| `time` | `raw` (original timestamp or null), `source_timezone` (IANA zone, UTC or `unverified_legacy`), `utc` (ISO 8601 ending Z or null), `resolution` (`documented`, `assumed`, `unresolved`) |
| `location` | `namespace` (`legacy_osmid`, `climate_physical`, `lion`, `report_location`), `native_id`, `latitude`, `longitude`, `location_text`, `borough`; unavailable values are null |
| `input` | Exactly the operation-specific fields in section 4 |

Source identity is the **pair `(dataset_id, record_id)`**. A snapshot identifies
the version of that source record, not a new incident. For 311, use dataset
`nyc_311` and its original `unique_key` converted losslessly to a string. For text,
use the original message ID; manually written examples get explicit synthetic IDs.
For prepared climate rows, provide a stable row key within the frozen feature
snapshot and a separate record-level lineage file if needed. Never infer a 311
unique key from a climate feature row or use a short `INC-...` hash as sole identity.

The result repeats the five header values and adds `request_sha256`, the SHA-256
of the exact request file bytes, and `records`. Each output record has
`request_id`, `status`, `reason_code`, `warnings`, and `result`. Context is recovered
by joining to the immutable request, avoiding conflicting copies of time/location.
Result references are the pair `(run_id, request_id)`.

There must be exactly one result per request, with no unknown, missing or repeated
IDs. Reordering is allowed. A replay has the same substantive outputs; changing
inputs, models, preprocessing or rules creates a new run/package as appropriate.

## 4. Module-specific input and output

### Climate legacy

`location.namespace=legacy_osmid`; `native_id` is the OSM ID without a prefixed
namespace. Its wrapper receives `location_id="legacy_osmid:" + native_id`.
`input` contains only `features`, with exactly these values in this model order:

| Feature | Type / unit | Interpretation |
| --- | --- | --- |
| `elevation_m` | finite number, metres | Legacy DEM summary; may be below zero |
| `slope` | nonnegative number, dimensionless | Legacy approximate terrain gradient |
| `rain_mm` | nonnegative number, millimetres | Legacy stored hourly radar accumulation, with source convention recorded |
| `rain_24hr_mm` | nonnegative number, millimetres | Legacy sum of up to 25 stored observations; retain this name for estimator compatibility |

No missing feature is replaced with zero. Kaleem supplies feature generation
provenance and verifies inputs match this model; the backend does not derive these
features ad hoc. Corrected slope or elapsed-hour windows need a compatible model.
For unresolved legacy time, pass the original naive timestamp and
`timestamp_timezone=unverified_legacy` to the wrapper. For documented UTC, pass
`time.utc` and `timestamp_timezone=UTC`. Assumed conversions are not substituted
into the legacy wrapper; preserve its original timestamp and record any scenario
placement separately. The estimator consumes only the four numeric features.

Successful `result`: `target="same_stored_hour_sewer_report_proxy"`,
`predicted_class` 0 or 1, `class_0_score`, `class_1_score`,
`score_calibrated=false`, `prediction_horizon_hours=null`, `severity=null`,
`water_depth_m=null`. Scores lie in [0,1] and sum to 1 within 1e-6. Use the
estimator's class mapping and label, not a separately invented flood threshold.
Class 0 means the classifier selected its negative proxy class; it does not
establish a dry or safe street.

### Safety structured 311

`source.dataset_id=nyc_311`; `source.record_id` supplies `unique_key` to the native
pipeline. `time.raw` supplies `created_date`. `location` supplies the raw report
coordinates, address and borough. `input` contains exactly `complaint_type` and
`descriptor`, both nonempty strings. Preserve the original strings: current rules
use exact complaint-type matching and case-insensitive descriptor substrings.

Accepted `result`: `hazard_type`, `severity="medium"`,
`severity_basis="fixed_rule_default"`, `score` (`kind="rule_estimate"`, `value`,
`calibrated=false`), `rule_id`, `threshold=null`, `verification="unverified"`,
and `guidance` (null or the object below). Current types are `flood` or
`road_blockage`. Rule IDs `S311-01` through `S311-10` refer to the ordered
`RULES_311` entries in the supplied pipeline; publish that table in the release.
Any change to membership/order/meaning requires a new rules release.

An unmatched category returns `status=skipped`, `reason_code=NOT_SELECTED_BY_RULE`,
`result=null`. This is an unselected report, not a verified absence of hazards.
No source rows disappear from run accounting. A missing source key is an error.

### Safety free text

`input` contains exactly `text`, a nonempty string after whitespace trimming.
Pass a fixed report timestamp and source explicitly. Empty text is an error.
Text shorter than the training preparation's 16-character minimum should retain
a `SHORT_TEXT_OUTSIDE_TRAINING_FILTER` warning if processed. Original text stays
in the module input bundle; the frontend does not require raw report text.

The selected package defines cleaning, TF-IDF vocabulary, embedding revision,
classifier, class order and threshold. For the supplied combined model the
reported threshold is .45; the TF-IDF reference is .40 and is a different package.
Do not silently fall back between them. The supplied pipeline expects the
combined bundle and cannot use the reference bundle unchanged.

Accepted `result`: `hazard_type` (`flood`, `road_blockage`, `power_outage`,
`unspecified`), `severity` (`medium` or `high`),
`severity_basis="keyword_heuristic"`, `score` (`kind="model_score"`, unrounded
`value`, `calibrated=false`), `rule_id=null`, package `threshold`,
`verification="unverified"`, `guidance`. Classification acceptance uses the
unrounded score >= threshold. Below threshold returns `skipped` /
`BELOW_TEXT_THRESHOLD` / null. Type and severity are heuristic enrichments of
an accepted report. A `power_outage` text label is not an energy-network model.

Both safety paths also return `extracted_location`, containing `location_text`,
`borough`, `latitude`, `longitude`. This preserves text extraction while keeping
the original request context immutable. Text coordinates remain null unless a
separately documented resolver supplies them; a place name is not a road mapping.

Optional `guidance`: `passage_id`, `similarity` in [-1,1], `corpus_id`,
`embedding_id`. This is retrieval similarity, not hazard confidence. Use the
versioned corpus/chunker and .50 cutoff for the supplied implementation. Only
flood guidance is covered. Missing corpus/retrieval may yield null with a warning
if classification is otherwise runnable. An unavailable embedding encoder blocks
combined text inference. For 311, a separately verified rule-only adapter can run
without NLP/retrieval; the current constructor does not yet provide that mode.

### Mobility

`location.namespace=lion`; `native_id` is the lossless LION segment ID string.
`time.utc` is an exact hour start. `input` contains `direction` (`EB/NB/SB/WB`),
`road_type` and `serving_context_id`. Retain segment/direction/road-type identity;
do not combine directions or sum repeated road types inadvertently.

Anas's serving manifest resolves the context to the chosen model, road attributes,
training-only history, source category, COVID flag, feature encoder, and calendar
policy. Calendar fields use America/New_York. Missing attributes follow the fitted
model's documented policy; missing required context is an error. Source type and
history cannot be guessed by frontend callers. Existing evaluation fits do not
establish this serving policy, and a request must not trigger training.

Successful `result`: `target="typical_hourly_vehicle_volume"`,
`unit="vehicles_per_hour"`, `direction`, `road_type`, `serving_context_id`,
nonnegative `p10 <= p50 <= p90`, `nominal_interval_coverage=0.8`,
`coverage_guaranteed=false`. The release links to the relevant evaluation coverage;
80% is nominal and must not be presented as achieved for every split or road.
If a model fitted on later data supplies 2021 context, label the run retrospective.
Never derive congestion, speed or flood-related demand changes from this output
without a separately declared rule/model.

## 5. Time and spatial alignment owned by the backend

Keep original timestamps. A documented conversion has a source convention and
audit reference in the snapshot manifest; an assumed conversion states its reason.
`resolution=unresolved` requires `utc=null`. No machine-local timezone, current
time, zero timestamp, or arbitrary hour may fill missing historical time.
UTC hours use `[start, end)`; a boundary report belongs to the later hour.
311 creation time is the report time, not proven onset of flooding. Real-time
availability is unverified for retrospective snapshots and must not be claimed.

The climate example at `2021-09-02T07:00:00` remains unresolved until the source row
is traced. Interpreting it as UTC or New York would place it in different hours.
311 source conventions can be documented from the owner's extraction evidence;
the contract does not convert every naive timestamp automatically. Ambiguous or
nonexistent local times stay unresolved until explicitly reviewed.

Coordinates are WGS84 / EPSG:4326: `latitude` [-90,90], `longitude` [-180,180],
both present or both null. GeoJSON uses [longitude, latitude]. Missing coordinates
do not become (0,0). Borough or street text alone does not identify a LION segment.

Anas maintains a versioned crosswalk. Each mapping row includes `mapping_id`,
native namespace/ID or report identity, source geometry snapshot/hash, LION
snapshot/hash and segment ID, relation (`same_road_extent` or `partial_overlap`),
method, distance/overlap evidence where applicable, reviewer and review status.
Keep candidate matches when ambiguous; accept only reviewed mappings for the demo.
OSM IDs may span multiple edges. A verified many-to-many crosswalk may associate
one signal with several LION segments, but does not create independent evidence
or justify dividing/summing scores. Current network geometry used for 2021 is
explicitly a retrospective spatial approximation unless historic validity is shown.

The backend enriches each result with `time_bucket_utc`, `mapping_status`
(`mapped`, `ambiguous`, `unresolved`, `out_of_scope`), `canonical_targets` (LION
IDs plus applicable directions), `mapping_version`, `cascade_eligible`, and
`blocked_reasons`. Producers do not invent these values. Eligibility requires an
OK result, in-scope data, a reviewed spatial mapping and documented time. The
historical path additionally requires `source.kind=historical`; synthetic fixtures
belong to a separately labeled illustration. Direction
effects belong to cascade rules; a physical road match alone does not prove which
travel directions were disrupted.

Unknown/unresolved records remain visible in the backend's review output and
cannot silently enter the historical cascade. A deliberate synthetic illustration
uses separate synthetic inputs and an assumption record, rather than relabeling
an uncertain historical row as verified. Assumed timestamps can be displayed with
their qualification but are excluded from the historical-alignment path.

## 6. Cascading and frontend boundary

The engine consumes validated, normalized outputs. Initial relationships are
explicit demonstration rules; there is no labeled cascade dataset here.
Climate scores, safety scores, severity labels and retrieval similarity are never
averaged or multiplied into a joint failure probability.

311 evidence may occur in both climate label provenance and safety reports.
Deduplicate exact source records by `(dataset_id, record_id)` across snapshots;
conflicting versions are reviewed. Distinct reports are still reports, not
necessarily distinct incidents. Track input evidence separately from training
label provenance. Climate's 311-supervised prediction must not be described as
independent confirmation of a safety 311 report. Where exact overlap is unknown,
record `dependence=possible_shared_311` and apply no corroboration bonus.

Each cascade effect contains: `effect_id`, `rule_id`, `rule_version`, upstream
result references, source evidence references, target LION IDs/directions,
`start_utc`, `end_utc`, `effect_type`, `parameters` with units,
`basis="scenario_assumption"`, and a plain-language explanation. The rule's
predicate, thresholds, effect duration, combination/expiry policy and assumptions
must be written down. Values such as closure duration or traffic multipliers are
not supplied by the reviewed models and are not chosen implicitly in this contract.

Trigger at most one instance of a given rule/target/hour for the same underlying
evidence group. Combine repeated triggers with an explicit precedence policy;
do not compound multipliers by report count. Preserve unmodified domain results.
Prefer qualitative `assumed_disruption` until a numeric effect rule is agreed.
Energy failures cannot be inferred from a text keyword alone.

The backend's frontend view contains `schema_version`, `scenario_id`, `run_id`,
scenario window, `module_status` (`available`, `partial`, `unavailable`),
normalized `signals`, `effects`, and warnings. Each signal carries its result
reference, typed result, source kind, time, location/mapping and eligibility.
The exact HTTP routes remain Anas's backend decision; this is the required content.

Kaleem and Arbaz render these outputs without loading model files or independently
joining source datasets. Labels distinguish report proxy, unverified report,
typical volume and assumed effect. Display unresolved locations separately from
map features, show missing/unavailable states, retain units and distinguish
nominal prediction intervals from probability scores. An empty hazard list does
not mean all roads are safe. Guidance references resolve through the versioned
backend corpus; retrieval scores are not dispatch instructions.

## 7. Failures, packages and acceptance

| Status | `result` | `reason_code` |
| --- | --- | --- |
| `ok` | Operation-specific object | null |
| `skipped` | null | `NOT_SELECTED_BY_RULE` or `BELOW_TEXT_THRESHOLD` as appropriate |
| `error` | null | `INVALID_INPUT`, `MISSING_REQUIRED_FEATURE`, `ASSET_UNAVAILABLE`, `PACKAGE_MISMATCH`, `UNSUPPORTED_CONTEXT`, or `INFERENCE_FAILED` |

Warnings describe limitations without changing a failed record into success.
Per-record failures retain request IDs. Malformed JSON, missing/duplicate request
IDs, wrong schema/operation or an unusable envelope fail the whole run without a
success file. For parseable identified records, report input/inference errors
explicitly. A successful empty selection is represented by skipped results for
all inputs, not an absent file. No partial write is treated as a completed run.
Proposed CLI exits: 0 complete with OK/skipped records; 1 complete error results;
2 invalid batch/no result file. A failed write is nonzero and never leaves a final
result filename. Output filenames are immutable per run; use a new run for changes.

Keep the organized workspace. Do not move supplied additional-resource folders
or overwrite versioned datasets, models or evaluation outputs. Working copies
may differ internally; the handover must use module-relative paths. Minimum
delivery under each existing module, with existing files referenced where useful:

- One runnable inference/export entry point and required preprocessing.
- Pinned environment file, tested Python/platform, offline assets and one command.
- `release_manifest.json`: `package_id`, operations/schema versions, code/model
   and preprocessing hashes, feature/class order, thresholds/rule definitions,
  input snapshot hashes, paths, embedding/corpus revisions where used, training
  date range/Ida overlap (unknown if not established), evaluation evidence and limits.
- A frozen small scenario input, actual output, and repeatability check with fixed
  timestamps. Hash every referenced data/model file; no credentials in the package.
- Native geometry/record lineage needed by Anas's crosswalk, with explicit gaps.

The scenario manifest records its exact UTC window, selected roads/directions,
input file hashes, coverage/exclusions, source-time evidence, mapping version,
selected package IDs, retrospective/illustration mode and rule versions. A package
manifest describes the reusable module release; the scenario manifest selects the
data and assumptions for this one demonstration.

Mobility Parquet datasets remain in place. The contract does not require copying
them into PostgreSQL. Separate environments are the default: sklearn 1.8.0 in
mobility and the handover's 1.9.1 are not presumed interchangeable.

Acceptance checks (schemas cover structure; owners/backend also check semantics):

1. Clean documented launch with local assets; model/package hashes match. For a
   static showcase, inference must not depend on a download at demonstration time.
2. Requests/results validate; input hash, package/operation, counts and all IDs
   match. Test missing fields, null coordinates, invalid numbers, dependency failure
   and an input selected by neither safety path. Preserve errors and skips.
3. A repeated frozen input gives the same decisions. Float tolerance is declared
   per package; no hidden wall-clock timestamp enters a report.
4. Module semantics pass: climate class/score checks; safety score/threshold/rule
   identity and source keys; mobility quantile order, units and serving context.
5. Mapping and time boundary checks pass on the selected roads/hours; unresolved
   records are counted and excluded rather than discarded or forced into a road.
6. The same 311 report arriving twice is not counted twice; multiple reports on
   the same road do not multiply the same effect. Shared climate/311 provenance
   does not add an independence bonus.
7. A complete static demonstration can trace a displayed effect to its input,
   mapping, time bucket and stated rule. No eligible input produces an explicit
   unavailable/no-trigger state. Schema-example validation alone is not integration
   acceptance or model-quality validation.

## 8. Concrete remaining dependencies

| Owner | Before integration acceptance |
| --- | --- |
| Kaleem | Export a bounded legacy-compatible Ida feature set with row lineage, time evidence and OSM geometry; record its weaknesses; provide the contract adapter. Corrected preparation remains a separate future model |
| Arbaz | Repair package/import and asset paths; retain `unique_key`; use fixed timestamps and unrounded text score; export one shape; freeze `start,end)` data with coverage status; produce a runnable 311 package and examples |
| Arbaz, for optional text | Supply/locate combined training-export provenance and exact encoder assets, or explicitly document remaining provenance limits; verify the selected saved model and output. Retrieval chunking is now available |
| Anas | Freeze road/hour subset, crosswalk and scenario manifest; select mobility serving context; validate adapters; specify demonstration rules and backend view |

The prepared Ida data and crosswalk are not in the reviewed climate/safety folders.
They are the main data dependencies. Packaging the existing model is a different
task from improving its scientific quality; the latter requires new versioned
training/evaluation and must not silently change a working handover.

## 9. Changes and use across AI chats

This file and its schemas are the common briefing for implementation chats, with
the [resource review (historical reference: `docs/integration/additional_resources_review.md`) as evidence. Each owner gives
their assistant the exact contract version, module responsibility, package path
and accepted scenario decisions. A chat's memory does not update another chat.

Before the first implementation handover, Anas and the owners mark acceptance
here and resolve the scenario subset and selected packages. This draft has no
recorded teammate acceptance. No exact work schedule is assumed from earlier
availability messages.

Record interface changes in a short entry: date, proposer, reason, affected fields,
new schema/package version, compatibility impact, agreed owner and affected chats.
Required-field, unit, target, time, score or identity changes need a new contract
version. Model-only updates still need a new package and refreshed evidence.
Keep previous versions usable. The owner informs Anas; Anas distributes the
accepted version to affected teammates/chats using explicit handover messages.

Decision log for this draft: Ida and a bounded static demonstration were selected
by Anas. File exchange, the proposed focus window, operation names and schemas are
recommendations for review. Road subset, runnable package selection, crosswalk and
numerical cascade assumptions remain open; they are not disguised as agreed facts.
