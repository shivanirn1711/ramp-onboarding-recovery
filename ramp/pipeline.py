"""Run RAMP end to end: load -> diagnose -> decide -> draft -> rank -> summarise."""
from dataclasses import dataclass, field

import pandas as pd

from .catalog import CLOSED, COMPLIANCE, HUMAN, RECOVERABLE, Catalog
from .checker import SafetyChecker
from .checker import summarise as summarise_issues
from .decide import decide
from .diagnose import Diagnoser
from .loader import check_activity_dates, load_inputs
from .messages import DEFAULT_LIBRARY, MessageLibrary
from .runlog import RunLog

KNOWN_STATUSES = {"Activated", "Stalled", "Rejected"}
OUTCOME_ORDER = {RECOVERABLE: 0, COMPLIANCE: 1, CLOSED: 2, HUMAN: 3}

RESULT_COLUMNS = [
    "priority_rank", "application_id", "outcome", "diagnosed_code", "cause", "plain_meaning", "fixable",
    "diagnosis_source", "confidence", "evidence", "runner_up", "needs_human_review", "review_priority",
    "review_reasons", "safety_check", "safety_issues",
    "owner", "next_action", "channel", "agent_call", "repeat_trier", "urgency", "high_value",
    "days_since_last_activity", "attempts", "support_contacts", "expected_monthly_tpv_inr_lakh",
    "preferred_language", "message_language", "native_check", "email_subject", "message",
    "approval_status", "approver_notes",
    "business_type", "industry", "city", "step_reached", "application_status", "whatsapp_opted_in",
    "rejection_code", "rejection_message", "signup_date",
]


@dataclass
class RunResult:
    results: pd.DataFrame
    summary: dict
    causes: pd.DataFrame
    log: RunLog
    applications: pd.DataFrame
    catalog: Catalog
    snapshot_date: object = None
    extras: dict = field(default_factory=dict)
    diagnoser: object = None      # kept so the UI can show score breakdowns
    library: object = None        # ... and re-draft the library text
    checker: object = None        # ... and re-check edited messages


def run(apps_source, catalog_source=None, library_path: str = DEFAULT_LIBRARY,
        snapshot_date="auto", reference_ids=None, log_file: str | None = "logs/ramp_run_log.csv",
        log: RunLog | None = None, with_tests: bool = True) -> RunResult:
    """
    apps_source / catalog_source: file paths or uploaded file objects (catalog_source=None
        means the catalog tab is in the same workbook).
    reference_ids: limit which coded rows the diagnoser may learn templates/steps from
        (used by the accuracy test). None = all coded rows in the batch.
    """
    log = log or RunLog()
    apps, cat_df, snap = load_inputs(apps_source, catalog_source, log)
    if snapshot_date != "auto":
        snap = snapshot_date
    cat = Catalog(cat_df, log)
    log.info("load", f"{len(apps)} applications loaded; snapshot date = {snap or 'unknown'}")
    check_activity_dates(apps, snap, log)

    for app_id, s in apps.loc[~apps["application_status"].isin(KNOWN_STATUSES),
                              ["application_id", "application_status"]].itertuples(index=False):
        log.warn("load", f"Unknown application_status '{s}', treated as not activated", app_id)
    activated = apps["application_status"] == "Activated"
    for app_id in apps.loc[activated & (apps["rejection_code"].notna() | apps["rejection_message"].notna()),
                           "application_id"]:
        log.warn("load", "Activated but has a rejection code/note; skipped as activated", app_id)
    todo = apps[~activated]

    reference = apps if reference_ids is None else apps[apps["application_id"].isin(set(reference_ids))]
    diagnoser = Diagnoser(cat, reference, log)
    library = MessageLibrary(cat, log, library_path)
    checker = SafetyChecker(library, cat)

    rows = []
    for _, row in todo.iterrows():
        d = diagnoser.diagnose(row)
        dec = decide(row, d, cat, log)
        code = d["diagnosed_code"]
        msg = library.compose(code, dec["outcome"], row["preferred_language"], dec["channel"],
                              row["application_id"], row["step_reached"])
        issues = checker.check({
            "application_id": row["application_id"], "diagnosed_code": code, "outcome": dec["outcome"],
            "channel": dec["channel"], "whatsapp_opted_in": row["whatsapp_opted_in"],
            "preferred_language": row["preferred_language"], "message_language": msg["message_language"],
            "step_reached": row["step_reached"], "rejection_message": row["rejection_message"],
            "message": msg["message"], "email_subject": msg["email_subject"]})
        safety, safety_text = summarise_issues(issues)
        reasons = dec["review_reasons"] + msg["problems"]
        if safety == "Blocked":
            reasons.append("Safety checker blocked the message")
            for i in issues:
                if i.severity == "BLOCK":
                    log.error("checker", f"[{i.check}] {i.detail}", row["application_id"])
        rows.append({
            **{c: row[c] for c in ["application_id", "business_type", "industry", "city",
                                   "preferred_language", "signup_date", "expected_monthly_tpv_inr_lakh",
                                   "whatsapp_opted_in", "step_reached", "application_status",
                                   "days_since_last_activity", "attempts", "support_contacts",
                                   "rejection_code", "rejection_message"]},
            "diagnosed_code": code,
            "cause": cat.field(code, "reviewer_reason") if code else "",
            "plain_meaning": cat.field(code, "plain_language_meaning") if code else "",
            "diagnosis_source": d["diagnosis_source"],
            "confidence": d["confidence"],
            "evidence": d["evidence"],
            "runner_up": d["runner_up"],
            **{k: dec[k] for k in ["outcome", "fixable", "owner", "channel", "urgency",
                                   "repeat_trier", "agent_call", "next_action", "high_value"]},
            "needs_human_review": bool(reasons),
            "review_priority": ("High" if dec["high_value"] else "Normal") if reasons else "",
            "review_reasons": " | ".join(reasons),
            "safety_check": safety,
            "safety_issues": safety_text,
            "message_language": msg["message_language"],
            "native_check": msg.get("native_check", ""),
            "email_subject": msg["email_subject"],
            "message": msg["message"],
            "approval_status": "Pending",
            "approver_notes": "",
        })
    res = pd.DataFrame(rows)
    if res.empty:
        res = pd.DataFrame(columns=RESULT_COLUMNS)
        log.warn("pipeline", "No non-activated applications found")

    # Priority: recoverable only, by expected TPV (high first); ties -> fewer idle days, then id.
    rec = res[res["outcome"] == RECOVERABLE].copy()
    rec["_tpv"] = pd.to_numeric(rec["expected_monthly_tpv_inr_lakh"], errors="coerce").fillna(-1)
    rec = rec.sort_values(["_tpv", "days_since_last_activity", "application_id"],
                          ascending=[False, True, True])
    res["priority_rank"] = pd.NA
    res.loc[rec.index, "priority_rank"] = range(1, len(rec) + 1)
    res["priority_rank"] = res["priority_rank"].astype("Int64")
    res["_o"] = res["outcome"].map(OUTCOME_ORDER)
    res["_r"] = pd.to_numeric(res["priority_rank"], errors="coerce")
    res = res.sort_values(["_o", "_r", "application_id"]).drop(columns=["_o", "_r"])
    res = res[RESULT_COLUMNS].reset_index(drop=True)

    extras = {}
    if with_tests:
        from .checker_tests import false_alarm_test, redteam_test
        from .evaluate import accuracy_test
        try:
            acc = accuracy_test(apps, cat)
            extras["accuracy"] = acc
            for p in acc["rule_problems"]:
                log.warn("accuracy", p)
        except Exception as e:      # the plan must still be produced if the test cannot run
            log.error("accuracy", f"Accuracy test could not run: {e}")
        extras["false_alarms"] = false_alarm_test(library, cat)
        extras["redteam"] = redteam_test(library, cat)
        n_fa = int((extras["false_alarms"]["issues"] != "").sum())
        if n_fa:
            log.error("checker", f"{n_fa} library templates trigger the checker (false alarms)")

    causes = build_causes(res, cat)
    summary = build_summary(apps, res, causes, log, extras)
    log.info("pipeline", f"Done: {summary['recoverable']} recoverable, {summary['closed']} closed, "
                         f"{summary['compliance']} compliance, {summary['needs_human_review']} need human review")
    summary["log_counts"] = log.counts()
    if log_file:
        log.append_to_file(log_file)
    return RunResult(res, summary, causes, log, apps, cat, snap, extras, diagnoser, library, checker)


def build_causes(res: pd.DataFrame, cat: Catalog) -> pd.DataFrame:
    """Fixable causes ranked by how many merchants they block (ties broken by TPV)."""
    rec = res[res["outcome"] == RECOVERABLE]
    if rec.empty:
        return pd.DataFrame(columns=["rank", "code", "cause", "merchants", "share_of_recoverable_pct",
                                     "expected_tpv_lakh", "is_top_3"])
    g = (rec.groupby("diagnosed_code")
         .agg(merchants=("application_id", "count"),
              expected_tpv_lakh=("expected_monthly_tpv_inr_lakh", "sum"))
         .reset_index().rename(columns={"diagnosed_code": "code"}))
    g["cause"] = g["code"].map(lambda c: cat.field(c, "reviewer_reason"))
    g["share_of_recoverable_pct"] = (100 * g["merchants"] / len(rec)).round(1)
    g["expected_tpv_lakh"] = g["expected_tpv_lakh"].round(1)
    g = g.sort_values(["merchants", "expected_tpv_lakh"], ascending=[False, False]).reset_index(drop=True)
    g["rank"] = range(1, len(g) + 1)
    g["is_top_3"] = g["rank"] <= 3
    return g[["rank", "code", "cause", "merchants", "share_of_recoverable_pct", "expected_tpv_lakh", "is_top_3"]]


def build_summary(apps: pd.DataFrame, res: pd.DataFrame, causes: pd.DataFrame, log: RunLog,
                  extras: dict | None = None) -> dict:
    tpv = pd.to_numeric(res["expected_monthly_tpv_inr_lakh"], errors="coerce").fillna(0)
    by = lambda o: res["outcome"] == o
    has_code, has_note = res["rejection_code"].notna(), res["rejection_message"].notna()
    rec = res[by(RECOVERABLE)]
    extras = extras or {}
    tests = {}
    if "accuracy" in extras:
        s = extras["accuracy"]["summary"].set_index("group")
        tests = {f"accuracy_{k}": f"{int(r['correct_code'])}/{int(r['rows'])} correct, "
                                  f"{int(r['sent_to_human_review'])} to review, "
                                  f"{int(r['wrong_and_auto_accepted'])} wrong & auto-accepted"
                 for k, r in s.iterrows()}
    if "redteam" in extras:
        rt = extras["redteam"]
        plain, tricky = rt[~rt["tricky"]], rt[rt["tricky"]]
        tests["checker_redteam"] = (f"plain {int(plain['caught_by_expected_check'].sum())}/{len(plain)} caught by "
                                    f"the right check; tricky {int(tricky['blocked_by_any_check'].sum())}/{len(tricky)} "
                                    f"blocked, {int(tricky['flagged_at_all'].sum())}/{len(tricky)} flagged")
    if "false_alarms" in extras:
        fa = extras["false_alarms"]
        tests["checker_false_alarms"] = f"{int((fa['issues'] != '').sum())}/{len(fa)} library templates flagged"
    return {
        **tests,
        "safety_blocked": int((res["safety_check"] == "Blocked").sum()),
        "safety_warn": int((res["safety_check"] == "Warn").sum()),
        "high_priority_reviews": int((res["review_priority"] == "High").sum()),
        "total_applications": len(apps),
        "activated": int((apps["application_status"] == "Activated").sum()),
        "non_activated": len(res),
        "with_code": int(has_code.sum()),
        "note_only": int((~has_code & has_note).sum()),
        "no_code_no_note": int((~has_code & ~has_note).sum()),
        "recoverable": int(by(RECOVERABLE).sum()),
        "recoverable_tpv_lakh": round(float(tpv[by(RECOVERABLE)].sum()), 1),
        "closed": int(by(CLOSED).sum()),
        "closed_tpv_lakh": round(float(tpv[by(CLOSED)].sum()), 1),
        "compliance": int(by(COMPLIANCE).sum()),
        "compliance_tpv_lakh": round(float(tpv[by(COMPLIANCE)].sum()), 1),
        "undiagnosed": int(by(HUMAN).sum()),
        "needs_human_review": int(res["needs_human_review"].sum()),
        "agent_calls": int(res["agent_call"].sum()),
        "whatsapp": int((res["channel"] == "WhatsApp").sum()),
        "email": int((res["channel"] == "Email").sum()),
        "urgency_recoverable": rec["urgency"].value_counts().to_dict(),
        "languages": res["preferred_language"].value_counts().to_dict(),
        "top_3_causes": causes.head(3)[["code", "cause", "merchants", "expected_tpv_lakh"]].to_dict("records"),
        "log_counts": log.counts(),
    }
