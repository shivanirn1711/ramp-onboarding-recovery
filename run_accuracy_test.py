"""Run the accuracy test and keep the FIRST result frozen.

    python run_accuracy_test.py <data_pack.xlsx>

The first run writes output/accuracy_first_result.json and that file is never
overwritten. Later runs write output/accuracy_latest.json and say whether the
rule code changed since the first run (fingerprint of the files that decide
the diagnosis).
"""
import hashlib
import json
import os
import sys
from datetime import datetime

from ramp.catalog import Catalog
from ramp.evaluate import accuracy_test
from ramp.loader import load_inputs
from ramp.runlog import RunLog

DECIDING_FILES = ["ramp/rules.py", "ramp/config.py", "ramp/textnorm.py", "ramp/diagnose.py",
                  "ramp/catalog.py", "ramp/decide.py", "ramp/split.py"]
FIRST = "output/accuracy_first_result.json"
LATEST = "output/accuracy_latest.json"


def fingerprint(paths) -> str:
    h = hashlib.sha256()
    for p in paths:
        with open(p, "rb") as f:
            h.update(f.read())
    return h.hexdigest()[:16]


def main():
    data = sys.argv[1]
    log = RunLog()
    apps, cat_df, _ = load_inputs(data, None, log)
    res = accuracy_test(apps, Catalog(cat_df, log))
    with open(data, "rb") as f:
        data_hash = hashlib.sha256(f.read()).hexdigest()[:16]
    record = {
        "run_at": datetime.now().isoformat(timespec="seconds"),
        "rule_code_fingerprint": fingerprint(DECIDING_FILES),
        "data_fingerprint": data_hash,
        "n_tune": res["n_tune"], "n_test": res["n_test"],
        "rule_grounding_problems": res["rule_problems"],
        "summary": res["summary"].to_dict("records"),
        "per_row": res["per_row"].astype(object).where(res["per_row"].notna(), None).to_dict("records"),
    }
    os.makedirs("output", exist_ok=True)
    if not os.path.exists(FIRST):
        with open(FIRST, "w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False, indent=1, default=str)
        print(f"FIRST RESULT recorded in {FIRST} (this file is never overwritten)")
    else:
        with open(FIRST, encoding="utf-8") as f:
            first = json.load(f)
        with open(LATEST, "w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False, indent=1, default=str)
        same = first["rule_code_fingerprint"] == record["rule_code_fingerprint"]
        print(f"First result was recorded {first['run_at']}. Rule code "
              f"{'UNCHANGED' if same else 'CHANGED'} since then. Latest run saved to {LATEST}.")
    print(f"tune rows {res['n_tune']}, test rows {res['n_test']}, rule grounding problems: "
          f"{res['rule_problems'] or 'none'}")
    print(res["summary"].to_string(index=False))


if __name__ == "__main__":
    main()
