"""Safety & Governance pipeline (baseline v1).

Turns one citizen report (free text, or a NYC 311 row) into an incident node (a dict),
or returns None when the Safety gate decides the report is not a hazard.

    from safety_governance import SafetyGovernancePipeline
    pipe = SafetyGovernancePipeline()
    node = pipe.process_text("huge tree fell across the road on Queens Blvd")
    node = pipe.process_311({"unique_key": 1, "created_date": "2021-09-01", ...})

Safety, text path : TF-IDF + MiniLM embeddings -> logistic regression (saved in notebook 13).
Safety, 311 path  : category rules on complaint_type + descriptor.
Governance        : street regex + spaCy places, keyword hazard type, embedding lookup of related guidance.

Known limits (see the notebooks' Findings cells): weak transfer across datasets, many false alarms
at high recall, nothing is verified (status stays "unverified"), guidance covers flooding only,
text locations are mostly area-level.
"""
import hashlib
import os
import re
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from scipy.sparse import csr_matrix, hstack

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("PLEXA_DATA_DIR", REPO_ROOT / "notebooks" / "data"))
MODEL_PATH = DATA_DIR / "models" / "safety_combined_v1.joblib"
PROTOCOL_DIR = DATA_DIR / "raw" / "protocols"

# ---------------------------------------------------------------- 311 rules (draft, see notebook 10)
RULES_311 = [
    ("Sewer", "Sewer Backup", "flood"),
    ("Sewer", "Street Flooding", "flood"),
    ("Sewer", "Catch Basin", "flood"),
    ("Sewer", "Manhole Overflow", "flood"),
    ("Water System", "Excessive Water In Basement", "flood"),
    ("Damaged Tree", "Entire Tree Has Fallen", "road_blockage"),
    ("Damaged Tree", "Branch or Limb Has Fallen", "road_blockage"),
    ("Damaged Tree", "Tree Leaning/Uprooted", "road_blockage"),
    ("Street Condition", "Cave-in", "road_blockage"),
    ("Sewer", "Manhole Cover Missing", "road_blockage"),
]
CONF_311 = {"Sewer Backup": 0.7}   # ~66% confirmed in a 29-row manual check (rough)
DEFAULT_CONF_311 = 0.9             # assumption, not measured

# ---------------------------------------------------------------- Governance helpers
STREET_WORDS = r"(?:street|st|avenue|ave|boulevard|blvd|road|rd|drive|dr|parkway|pkwy|place|pl)"
ADDRESS_RE = re.compile(rf"\b\d{{1,5}}(?:-\d{{1,5}})?\s+(?:[A-Za-z0-9]+\s+){{0,3}}{STREET_WORDS}\b", re.I)
STREET_RE = re.compile(rf"\b(?:[A-Za-z]+|\d{{1,3}}(?:st|nd|rd|th)?)\s+{STREET_WORDS}\b", re.I)
SKIP_FIRST = {"the", "a", "an", "my", "our", "this", "that", "every", "same", "main", "next"}
BOROUGHS = ["manhattan", "brooklyn", "queens", "bronx", "staten island"]
SEVERE = ["waist", "submerged", "stuck", "trapped", "emergency", "rescue", "cars", "river"]
NOT_PLACES = ("hurricane", "harvey", "irma", "maria", "matthew", "idai", "storm", "youtube", "twitter")
TYPE_KW = [
    ("flood", r"flood|water|submerged|inundat|river|sewer|basement|drain"),
    ("power_outage", r"power|electric|outage|blackout|grid|transformer|substation"),
    ("road_blockage", r"road|bridge|highway|tree|branch|blocked|debris|lane"),
]
COVERED = {"flood"}   # hazard types the saved guidance documents cover
QUERY_BY_TYPE = {"flood": "flooding in the street or basement, sewer backup, catch basin"}

NODE_KEYS = {
    "incident_id", "source", "method", "timestamp", "hazard_type", "severity", "confidence",
    "score_type", "location_text", "borough", "lat", "lon", "guidance", "status",
}


def clean_text(t):
    """Same cleaning the training tweets went through."""
    t = str(t)
    t = re.sub(r"https?://\S+", " ", t)
    t = re.sub(r"@\w+", " ", t)
    t = re.sub(r"^rt\s+", "", t, flags=re.I)
    t = t.replace("#", " ")
    return re.sub(r"\s+", " ", t).strip()


def keyword_type(text):
    for label, pat in TYPE_KW:
        if re.search(pat, text, flags=re.I):
            return label
    return "unspecified"


def chunk_file(path, min_words=60, max_words=200):
    lines = [l.strip() for l in open(path, encoding="utf-8", errors="ignore").read().split("\n") if l.strip()]
    chunks, cur = [], []
    for line in lines:
        cur.append(line)
        if len(" ".join(cur).split()) >= min_words:
            chunks.append(" ".join(cur))
            cur = []
    if cur and len(" ".join(cur).split()) >= 20:
        chunks.append(" ".join(cur))
    out = []
    for c in chunks:
        w = c.split()
        for i in range(0, len(w), max_words):
            out.append(" ".join(w[i:i + max_words]))
    return out


def _none_if_missing(v):
    return None if v is None or (not isinstance(v, str) and pd.isna(v)) else v


def _node_id(*parts):
    return "INC-" + hashlib.md5("|".join(str(p) for p in parts).encode()).hexdigest()[:8].upper()


class SafetyGovernancePipeline:
    def __init__(self, model_path=MODEL_PATH, protocol_dir=PROTOCOL_DIR, guidance_min_score=0.50):
        # Load spaCy BEFORE torch/sentence-transformers: the other order crashed (segfault) on Windows.
        import spacy
        self.nlp = spacy.load("en_core_web_sm")
        from sentence_transformers import SentenceTransformer

        if not Path(model_path).exists():
            raise FileNotFoundError(
                f"{model_path} not found. Run the last cell of notebooks/13_embeddings_vs_tfidf.ipynb "
                "to create it (the file is git-ignored)."
            )
        bundle = joblib.load(model_path)
        self.tfidf = bundle["tfidf"]
        self.clf = bundle["clf"]
        self.threshold = float(bundle["threshold"])
        self.embedder = SentenceTransformer(bundle["embed_model"])
        self.hz = list(self.clf.classes_).index("hazard")
        self.guidance_min_score = guidance_min_score
        self._load_guidance(Path(protocol_dir))
        self._guidance_cache = {}

    # ------------------------------------------------------------ guidance lookup
    def _load_guidance(self, protocol_dir):
        passages = []
        for f in sorted(protocol_dir.glob("*.txt")):
            for n, c in enumerate(chunk_file(f)):
                passages.append((f"{f.name}#passage-{n}", c))
        self.kb_ids = [p[0] for p in passages]
        if passages:
            self.kb_emb = self.embedder.encode([p[1] for p in passages], normalize_embeddings=True, batch_size=32)
        else:
            self.kb_emb = None
            print(f"WARNING: no guidance documents found in {protocol_dir}; guidance will be None.")

    def guidance(self, hazard_type, text=""):
        """Return {"guidance_source": id, "score": s} or None. Stores ids only, never the text."""
        if hazard_type not in COVERED or self.kb_emb is None:
            return None
        key = (hazard_type, text)
        if key not in self._guidance_cache:
            q = text if text else QUERY_BY_TYPE[hazard_type]
            s = self.kb_emb @ self.embedder.encode([q], normalize_embeddings=True)[0]
            i = int(np.argmax(s))
            self._guidance_cache[key] = (
                {"guidance_source": self.kb_ids[i], "score": round(float(s[i]), 3)}
                if s[i] >= self.guidance_min_score else None
            )
        return self._guidance_cache[key]

    # ------------------------------------------------------------ Safety
    def hazard_probability(self, text):
        t = clean_text(text)
        xs = self.tfidf.transform([t])
        e = self.embedder.encode([t], normalize_embeddings=True)
        x = hstack([xs, csr_matrix(e)]).tocsr()
        return float(self.clf.predict_proba(x)[0][self.hz])

    @staticmethod
    def safety_311(complaint_type, descriptor):
        for rule_type, keyword, label in RULES_311:
            if complaint_type == rule_type and keyword.lower() in str(descriptor).lower():
                return label
        return "non_hazard"

    # ------------------------------------------------------------ Governance
    def extract_entities(self, text):
        places = [e.text for e in self.nlp(text).ents
                  if e.label_ in ("GPE", "LOC", "FAC")
                  and not any(w in e.text.lower() for w in NOT_PLACES)]
        street = None
        m = ADDRESS_RE.search(text)
        if m:
            street = m.group(0)
        else:
            for m in STREET_RE.finditer(text):
                if m.group(0).split()[0].lower() not in SKIP_FIRST:
                    street = m.group(0)
                    break
        parts = ([street] if street else []) + places
        location = ", ".join(dict.fromkeys(parts)) if parts else None
        borough = next((b.title() for b in BOROUGHS if b in text.lower()), None)
        severity = "high" if any(w in text.lower() for w in SEVERE) else "medium"
        return location, borough, severity

    # ------------------------------------------------------------ public API
    def process_text(self, text, source="TEXT", timestamp=None):
        """Free-text report -> incident node, or None if the Safety gate discards it."""
        p = self.hazard_probability(text)
        if p < self.threshold:
            return None
        clean = clean_text(text)
        location, borough, severity = self.extract_entities(clean)
        htype = keyword_type(clean)
        return {
            "incident_id": _node_id(source, text),
            "source": source,
            "method": "text_model",
            "timestamp": timestamp or datetime.now(timezone.utc).isoformat(),
            "hazard_type": htype,
            "severity": severity,
            "confidence": round(p, 2),
            "score_type": "model_probability",
            "location_text": location,
            "borough": borough,
            "lat": None,
            "lon": None,
            "guidance": self.guidance(htype, clean),
            "status": "unverified",
        }

    def process_311(self, row):
        """NYC 311 row (dict-like) -> incident node, or None if the rules say non-hazard."""
        label = self.safety_311(row.get("complaint_type"), row.get("descriptor"))
        if label == "non_hazard":
            return None
        conf = next((c for k, c in CONF_311.items() if k.lower() in str(row.get("descriptor")).lower()),
                    DEFAULT_CONF_311)
        lat, lon = _none_if_missing(row.get("latitude")), _none_if_missing(row.get("longitude"))
        borough = _none_if_missing(row.get("borough"))
        return {
            "incident_id": _node_id("NYC_311", row.get("unique_key")),
            "source": "NYC_311",
            "method": "category_rule",
            "timestamp": str(row.get("created_date")),
            "hazard_type": label,
            "severity": "medium",
            "confidence": conf,
            "score_type": "rule_estimate",
            "location_text": _none_if_missing(row.get("incident_address")),
            "borough": str(borough).title() if borough else None,
            "lat": float(lat) if lat is not None else None,
            "lon": float(lon) if lon is not None else None,
            "guidance": self.guidance(label),
            "status": "unverified",
        }

    def process_311_batch(self, df):
        nodes = (self.process_311(r) for r in df.to_dict("records"))
        return [n for n in nodes if n]
