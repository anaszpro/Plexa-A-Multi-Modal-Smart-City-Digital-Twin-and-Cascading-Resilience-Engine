# safety_governance

Baseline Safety & Governance pipeline: citizen report -> incident node (or discarded).

## Setup
1. Create the model file by running the last cell of `notebooks/13_embeddings_vs_tfidf.ipynb`
   (writes `notebooks/data/models/safety_combined_v1.joblib`; git-ignored, contains tweet vocabulary).
2. Save the guidance pages as `.txt` in `notebooks/data/raw/protocols/` (see notebook 12; git-ignored, City content).
3. `pip install scikit-learn sentence-transformers spacy joblib pandas scipy numpy`
   and `python -m spacy download en_core_web_sm`
4. Use the same scikit-learn version that saved the model (a different version can break loading).

## Use
```python
from safety_governance import SafetyGovernancePipeline
pipe = SafetyGovernancePipeline()
pipe.process_text("huge tree fell across the road on Queens Blvd", source="DEMO")
pipe.process_311(row_dict)
pipe.process_311_batch(df)
```
Demo: `python -m safety_governance.demo` (run from the repo root).

## Incident node fields
incident_id, source, method, timestamp, hazard_type, severity, confidence, score_type
(`model_probability` for text, `rule_estimate` for 311), location_text, borough, lat, lon,
guidance ({guidance_source, score} or null), status ("unverified").

## Limits
Weak transfer across datasets; many false alarms at high recall; reports are not verified;
guidance covers flooding only; text locations are mostly area-level; 311 flood rules (Sewer Backup)
are only ~66% confirmed in a small manual check.
