"""Honest accuracy test of the diagnosis rules.

1. Split the coded rows (code + note) into tune / test with the fixed seed.
2. Build the diagnoser from the TUNE half only (templates + usual steps).
3. For every TEST row, hide the reviewer code and diagnose from the note alone,
   exactly as the app does for a note-only application.
4. Compare with the hidden code. Report separately for
     "Seen"  : the note is word-for-word a tuning-half note (template match does the work)
     "New"   : the note is a phrasing the tuning half never had (keyword rules do the work)
   and also score the keyword rules ALONE on every test row.
5. Count how many would have gone to human review, and the dangerous case:
   wrong AND auto-accepted (no human review).
"""
import pandas as pd

from . import config
from .catalog import Catalog
from .decide import decide
from .diagnose import Diagnoser
from .rules import TUNED_RULES, check_rules
from .runlog import RunLog
from .split import split_coded
from .textnorm import template_key

SEEN, NEW = "Seen in tuning half", "New phrasing"


def accuracy_test(apps: pd.DataFrame, cat: Catalog, seed: int = config.SPLIT_SEED) -> dict:
    quiet = RunLog()
    split = split_coded(apps, seed)
    tune_ids = set(split.loc[split["split"] == "tune", "application_id"])
    test_ids = set(split.loc[split["split"] == "test", "application_id"])
    tune = apps[apps["application_id"].isin(tune_ids)]
    test = apps[apps["application_id"].isin(test_ids)].sort_values(["rejection_code", "application_id"])

    rule_problems = check_rules(TUNED_RULES, tune[["application_id", "rejection_code", "rejection_message"]]
                                .itertuples(index=False))
    diag = Diagnoser(cat, tune, quiet)
    tune_keys = {template_key(m) for m in tune["rejection_message"].dropna()}

    rows = []
    for _, row in test.iterrows():
        true = row["rejection_code"]
        hidden = row.copy()
        hidden["rejection_code"] = pd.NA
        d = diag.diagnose(hidden)
        dec = decide(hidden, d, cat, quiet)
        kw = diag.keyword_guess(row["rejection_message"])
        pred = d["diagnosed_code"]
        rows.append({
            "application_id": row["application_id"],
            "true_code": true,
            "reviewer_note": row["rejection_message"],
            "phrasing": SEEN if template_key(row["rejection_message"]) in tune_keys else NEW,
            "predicted_code": pred,
            "method": d["diagnosis_source"],
            "confidence": d["confidence"],
            "correct": pred == true,
            "keyword_only_code": kw["code"],
            "keyword_only_confidence": kw["confidence"],
            "keyword_only_correct": kw["code"] == true,
            "true_outcome": cat.outcome(true),
            "predicted_outcome": dec["outcome"],
            "outcome_correct": dec["outcome"] == cat.outcome(true),
            "sent_to_human_review": bool(dec["review_reasons"]),
            "review_reasons": " | ".join(dec["review_reasons"]),
            "evidence": d["evidence"],
            "runner_up": d["runner_up"],
            "expected_tpv_lakh": row["expected_monthly_tpv_inr_lakh"],
        })
    per_row = pd.DataFrame(rows)
    return {"per_row": per_row, "summary": summarise(per_row), "split": split,
            "rule_problems": rule_problems, "n_tune": len(tune), "n_test": len(test)}


def summarise(per_row: pd.DataFrame) -> pd.DataFrame:
    out = []
    for name, g in [(SEEN, per_row[per_row["phrasing"] == SEEN]),
                    (NEW, per_row[per_row["phrasing"] == NEW]),
                    ("All test rows", per_row)]:
        n = len(g)
        auto = g[~g["sent_to_human_review"]]
        pct = lambda k: round(100 * k / n, 1) if n else None
        out.append({
            "group": name,
            "rows": n,
            "correct_code": int(g["correct"].sum()),
            "accuracy_pct": pct(g["correct"].sum()),
            "keyword_rules_alone_correct": int(g["keyword_only_correct"].sum()),
            "keyword_rules_alone_pct": pct(g["keyword_only_correct"].sum()),
            "correct_outcome": int(g["outcome_correct"].sum()),
            "sent_to_human_review": int(g["sent_to_human_review"].sum()),
            "auto_accepted": len(auto),
            "auto_accepted_correct": int(auto["correct"].sum()),
            "wrong_and_auto_accepted": int((~auto["correct"]).sum()),
        })
    return pd.DataFrame(out)
