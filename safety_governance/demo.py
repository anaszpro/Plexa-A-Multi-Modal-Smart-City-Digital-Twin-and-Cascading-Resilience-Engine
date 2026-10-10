"""Run from the repo root:  python -m safety_governance.demo"""
import json

import pandas as pd

from .pipeline import DATA_DIR, NODE_KEYS, SafetyGovernancePipeline

SENTENCES = [
    "water is rushing down 5th avenue in Manhattan, cars are stuck up to the doors",
    "huge tree fell across the road on Queens Blvd, nobody can pass",
    "my neighbor is playing loud music again",
    "basement flooded with sewage after the storm",
    "the street is a river near the subway entrance",
    "power is out on Atlantic Ave in Brooklyn, transformer sparking",
    "nice weather today, going for a walk",
    "big branch came down on Atlantic Ave and blocks the lane",
]

pipe = SafetyGovernancePipeline()
print(f"Safety threshold: {pipe.threshold}\n")

print("=== free text ===")
for s in SENTENCES:
    node = pipe.process_text(s, source="DEMO", timestamp="2021-09-01T22:00:00")
    print("\n>", s)
    if node is None:
        print("  DISCARDED by Safety gate")
    else:
        assert set(node) == NODE_KEYS, set(node) ^ NODE_KEYS
        print(json.dumps(node, indent=2))

csv = DATA_DIR / "processed" / "nyc311_ida_labeled.csv"
if csv.exists():
    print("\n=== NYC 311 batch (first 3000 reports) ===")
    df = pd.read_csv(csv).head(3000)
    nodes = pipe.process_311_batch(df)
    print(len(nodes), "nodes from", len(df), "reports")
    for n in nodes[:2]:
        assert set(n) == NODE_KEYS, set(n) ^ NODE_KEYS
        print(json.dumps(n, indent=2, default=str))
else:
    print("\n(311 csv not found, skipping batch part)")
