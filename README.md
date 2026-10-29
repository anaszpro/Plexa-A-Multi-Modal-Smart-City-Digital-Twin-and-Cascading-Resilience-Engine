# Plexa

Plexa combines urban domain signals for a bounded Hurricane Ida 2021 demonstration.
Anas owns mobility, the shared backend, cascading/fusion and integration. Kaleem
owns climate and starts energy; Arbaz owns safety/governance. Kaleem and Arbaz
share the frontend.

This checkout currently contains the migrated mobility baseline. The shared API,
prediction adapter, cascading/fusion implementation and frontend remain future work.
Other module owners maintain their code on their respective branches.

## Start here

- [Mobility setup and workflow](backend/src/plexa/mobility/README.md)
- [Project handbook](docs/project/plexa_project_handbook.md)
- [Current decisions](docs/project/plexa_decisions_and_status.md)
- [Draft integration contract](docs/integration/integration_contract_v0.1.md)

## Working layout

```text
backend/src/plexa/mobility/   model code, runners, tests and database utilities
notebooks/mobility/          four preparation notebooks
data/mobility/               local source, GIS and prepared datasets
outputs/mobility/            versioned fits, evaluations and local MLflow
docs/project/               project context and decisions
docs/integration/           contract, schemas and examples
```

Work from this cloned folder. All mobility paths resolve inside it; no directory
junctions or old workspace paths are needed to run the saved-model checks.

## Environment and verification

Use Python 3.13. From the repository root in PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend/src/plexa/mobility/modeling/requirements.txt
.\.venv\Scripts\python.exe -B backend/src/plexa/mobility/modeling/verify_gradient_boosted_results.py
```

On this machine the isolated environment and local assets have already been
prepared. See the module README for tests, training and database setup details.

## What Git shares

Source, notebooks, dependencies, essential documents, fitted evaluation models,
metrics and manifests are intended to be shared. The full traffic/GIS datasets,
row-level Parquet outputs, MLflow database/artifact copies, database dump and
environment stay local under the same project.

A new clone also needs the matching local asset bundle: `data/mobility/`,
the ignored Parquet files under both evaluation directories, and
`outputs/mobility/mlflow.db`, `mlartifacts/` and `mlflow_relocation.json`.
The dataset snapshots and run manifests identify the exact expected versions.
A shared download location has not yet been established; coordinate that bundle
with Anas. Copying the SQLite file to another path also requires relocating its
artifact URIs and updating the existing relocation record.

## Scientific scope

The models estimate typical vehicles/hour and empirical quantiles. Existing
fits are four independent evaluations, not a final serving fit or observed Ida
traffic. B3 improves unseen-road point predictions, but interval coverage misses
the agreed target. Preserve those limitations when connecting mobility to fusion.
