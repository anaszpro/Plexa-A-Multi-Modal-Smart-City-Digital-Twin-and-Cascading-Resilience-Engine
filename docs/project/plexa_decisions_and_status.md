# Plexa decisions and status

**Edition:** knowledge package v0.1 baseline, 2026-10-18; safety review updated 2026-10-25; layout updated 2026-10-27.  
This is the decision index for the [handbook](plexa_project_handbook.md) and
[contract](../integration/integration_contract_v0.1.md). It distinguishes Anas's
explicit choices from draft interface recommendations and uncompleted work.
It does not record teammate acceptance that has not been supplied.

## Current mobility workspace — 2026-10-28

- Working checkout: the cloned repository, branch `feature/mobility-transit-module`.
- Source is under `backend/src/plexa/mobility`; notebooks/data/outputs use their
  root-level mobility directories. No compatibility junctions are required.
- One isolated Python 3.13 environment and one mobility requirements file replace
  the overlapping requirements/environment arrangement.
- All frozen datasets, twelve model fits and both evaluation bundles are preserved.
  MLflow's twelve runs were relocated by changing location fields only.
- Original baseline source is preserved in local Git commit
  `e4fcf2f4889c9c0ac08103623b19c32cec1b5d90`. Existing local relocation metadata
  records the exact path-only source updates. Frozen run manifests are unchanged.
- The module README is the current run guide. Dated organization/teammate evidence
  below remains historical. The old workspace remains intact as historical material.
- Migration verification: 1,544 saved-result checks and 20 behavior tests pass.
  All 434 copied data/output files other than relocated tracking metadata are
  byte-identical; all twelve MLflow runs retain their non-location data.
  The clone contains 481 project files, excluding Git and the local environment.
- A serving fit/prediction adapter and backend/fusion implementation remain open.
  No teammate branch, remote branch or integration schema was merged or pushed.

## Settled context from Anas

| ID | Decision or instruction | Meaning for the team |
| --- | --- | --- |
| D01 | This is the Plexa Planning chat | Overall coordination happens here; module/cascade implementation can use separate chats |
| D02 | Anas owns mobility, all backend, cascading and integration | Shared boundaries are coordinated by Anas |
| D03 | Kaleem owns climate and begins energy | Climate inference/preparation remains his module responsibility |
| D04 | Arbaz owns safety/governance | Safety inference/rules and enrichment remain his module responsibility |
| D05 | Kaleem and Arbaz share frontend ownership | They consume the shared backend's meanings and agree component ownership |
| D06 | Initially retain the organized workspace | Superseded for layout by Anas's explicit reorganization request on 2026-10-27; see D16 |
| D07 | Use a bounded static demonstration | Frozen data and repeatability define the immediate integration setting |
| D08 | Hurricane Ida 2021 is the selected scenario | Exact road/hour coverage still needs to be frozen |
| D09 | Immediate emphasis is fixing and aligning teammate handovers for integration | Avoid making every future model or technology a prerequisite for the first coherent prototype |
| D10 | Mobility modeling/split datasets stay in Parquet | Copying them to PostgreSQL needs a concrete reason |
| D11 | Preserve versioned datasets, models and evaluation evidence | Changed inputs/models produce new versions |
| D12 | Do not reconstruct missing safety implementation from prose | Actual application code has since been supplied; inspect and adapt that code |
| D13 | Use local resources for this work | GitHub retrieval is unnecessary for this package and current contract |
| D14 | Resume contract creation after inspecting additional resources | Contract v0.1 and schemas/examples were created and shared for review |
| D15 | Provide deeper project knowledge alongside the contract | This handbook/package is the requested context for teammates and new chats |
| D16 | Adapt the supplied application layout to Plexa using existing content | Parent folders may be created; skip absent features. Current module code lives under `backend/src/plexa/`; canonical data, notebooks, artifacts and outputs are separated where compatible. Links preserve existing release paths; see the organization notes |

Anas responded positively to the contract and requested a shareable project
knowledge package. That supports distribution of the draft. It does not identify
an approved road subset, rule parameters or recorded agreement by Kaleem/Arbaz.

## Recommendations currently written in contract v0.1

| ID | Recommendation | Acceptance still needed |
| --- | --- | --- |
| P01 | Static JSON exchange between separate module environments | Team review of entry-point and delivery convention |
| P02 | Shared outer fields with domain-specific typed results | Review exact schemas before implementing adapters |
| P03 | Focus window 2021-09-01 to 2021-09-03 in New York, end exclusive | Freeze according to actual data coverage; choose roads/directions |
| P04 | Prefer the structured 311 path for initial safety integration | Verify package/assets and the selected frozen input export |
| P05 | Use transparent graph/rules for the first cascade | Anas selects graph representation, predicates, effects and expiry/combination policy |
| P06 | Keep energy, advanced routing and larger models as later work where they lack data | Confirm scope when scheduling the next development iteration |

These proposals should not be silently converted into claims that the corresponding
code is implemented. Operation names and numeric examples in the schema package
describe a draft boundary. Most non-climate examples are explicitly synthetic.

## Open decisions and dependencies

| ID | Item | Responsible owner | Evidence required to resolve it |
| --- | --- | --- | --- |
| O01 | Actual Ida road/hour subset | Anas with Kaleem and Arbaz | Prepared inputs and coverage by location/time |
| O02 | Legacy climate timestamp convention for chosen rows | Kaleem, normalized with Anas | Trace to extraction/join path; retain unresolved status where evidence is insufficient |
| O03 | OSM/report-to-LION crosswalk | Anas; native inputs from module owners | Geometry versions, candidate/review evidence and supported directions |
| O04 | Mobility serving fit and context | Anas in mobility chat | Model choice, feature/history/source policy, versioned export and real inference example |
| O05 | Safety 311 baseline handover | Arbaz; shared integration remains Anas's responsibility | Completed for this stage in v1.0.1: missing-time correction and valid input batch verified. Source timezone remains unverified and is documented; shared time/mapping acceptance remains separate |
| O06 | Optional combined text inference provenance/assets | Arbaz | Encoder/spaCy revisions and assets; missing training/export source or an explicit provenance limitation |
| O07 | Climate corrected model release | Kaleem | Prepared artifacts, agreed training/evaluation scope and a trained/reviewed package |
| O08 | Cascade graph and effects | Anas | Versioned rules, explanation, deduplication and assumptions; no unlabeled numeric multipliers |
| O09 | Frontend implementation choices and division of work | Kaleem and Arbaz with Anas | Backend view agreement and explicit component ownership |
| O10 | Energy target/data scope | Kaleem with Anas | Observation unit, target, geography, input availability and evaluation question |
| O11 | Current deadline and available hours | Team | A new date and availability when planning is scheduled |

## Evidence status at the review boundary

| Capability | Evidence status |
| --- | --- |
| Mobility benchmark models/results | Saved, inspected evaluation artifacts; no final serving package declared |
| Climate legacy example predictions | Two examples reproduced in earlier local smoke check with package-version warnings |
| Climate corrected model | No trained artifact supplied; preparation/source only |
| Safety application source | Supplied in additional resources; earlier absence statements are superseded |
| Safety structured 311 runtime | v1.0.1 correction verified: original schemas accept the 25,512-record valid batch; outputs reproduce exactly; all 74 integration handover member hashes match. Canonical package is now `backend/src/plexa/safety/`; `testing/safety/` remains a compatibility link |
| Safety optional text/retrieval runtime | Full pipeline remains unverified in this integration review |
| Safety combined evaluation metrics | Teammate-reported; corresponding training/export cells missing from supplied notebook 13 |
| Safety guidance passage count | Actual chunker reproduced 315 passages; retrieval rankings/embedding inference not reproduced |
| Scenario data/crosswalk | Safety candidate request/result supplied in `testing/safety`; time unresolved and road subset unselected; shared reviewed crosswalk still absent |
| Shared backend/cascade/frontend | No implementation supplied in the reviewed workspace |
| Contract structure | Four example pairs validated and 25 negative schema cases rejected during contract preparation |

File presence, code inspection, reported metrics, reproduced inference and system
acceptance are different evidence levels. Use the level supported by the record.

### Safety review update, 2026-10-25

See Safety 311 review and next steps (historical reference: `docs/integration/safety_311_review_and_next_steps.md`)
for the response to Arbaz's alignment request and the original shared schemas.
The candidate has 5,763 OK results, 19,749 skips and 252 explicit input errors.
Its results pass the shared schema; the 252 empty descriptors prevent the full
request from being strictly schema-valid. A separate missing-raw-time probe found
one adapter/schema mismatch to correct in a new package release.

The existing proposed Ida window is carried forward as the working alignment
target. This does not establish the source CSV's timezone, an accepted road list,
reviewed mappings or completed cascade integration. Anas owns those shared
dependencies. The safety rule labels require category-specific cascade review:
only 694 of the 4,827 flood selections match the Street Flooding rule.
The prior distribution ZIP remains a frozen snapshot and does not include this update.

The subsequent v1.0.1 handover corrected the missing-raw-time behavior and supplied
a schema-valid 25,512-record batch with a separate ledger for the 252 excluded
records. Those schemas, hashes and replay were checked locally. Arbaz's structured
311 baseline delivery is complete for this stage. The source-time provenance note
documents unresolved evidence; no verified timezone or normalized historical run
is claimed. An explicitly labeled New York assumption was recommended for a
separate static replay, but no such replay was created in this planning chat.
The layout change on 2026-10-27 preserved the current safety release byte for byte.

## Historical timing

The planning discussion originally identified 2026-10-16 as the showcase deadline
and four immediate hours per person. The package is assembled on 2026-10-18.
It does not assume that deadline was extended, that the demonstration occurred,
or that the same availability still applies. Current scheduling is open.

## Updating this record

For a new decision, record: ID/date, question, choice, reason, owner approval,
affected contract/package versions and which teammates/chats need the update.
For a completed dependency, link actual outputs and describe the check performed.
Replace a current status with evidence rather than retaining an endless chat log.

Any change to a required field, unit, target, time interpretation or identity rule
needs an agreed contract revision. A model-only change still needs a new package
version and fresh evidence. Distribution ZIPs are dated snapshots; create a new
edition when the current documents change.
