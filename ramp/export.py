"""Write everything to one Excel workbook (a path or an in-memory buffer)."""
import pandas as pd

from . import config

ABOUT = [
    ("What this is", "RAMP recovery plan for non-activated onboarding applications. Rules and a "
                     "pre-written message library only: no live AI, no API keys."),
    ("Diagnosis", "Reviewer code if present. Otherwise: same note as a coded case (template match), "
                  "else keyword rules (catalog words + hand-written rules tuned on half the coded rows). "
                  "No code and no note = abandoned, message names the step they stopped at."),
    ("Confidence", f"Keyword guess is High only if it scores >= {config.MIN_SCORE} and beats the "
                   f"runner-up by >= {config.MIN_MARGIN}; else Low -> human review. Within "
                   f"{config.BORDERLINE_BAND} of either bar = Borderline -> human review."),
    ("High-value guard", f"Merchants with expected TPV >= {config.HIGH_TPV_LAKH:g} lakh: any diagnosis that rests "
                         "only on keyword rules goes to human review, marked High priority."),
    ("Safety checker", "Scans every message for promises, dates/timelines, 'nothing is wrong', workarounds "
                       "in closures, compliance words, copied reviewer notes, wrong channel, wrong "
                       "language/script and broken structure. Closure/compliance messages edited away from "
                       "the library text are blocked; other edits are warned."),
    ("Accuracy test", "Coded rows split 50/50 (seed fixed before rules were written). Rules may only learn "
                      "from the tune half; codes are hidden on the test half and predicted from the note."),
    ("Outcome", "From the catalog 'fixable' column: Yes = Recoverable, No = Closed, "
                "compliance = Compliance. Inferred closures/compliance always need a person to confirm."),
    ("Channel", "WhatsApp only if whatsapp_opted_in = Yes, otherwise Email."),
    ("Priority", "Recoverable only, ranked by expected monthly TPV (high first); ties -> fewer idle days."),
    ("Urgency", "Days since last activity: " + ", ".join(lbl for _, lbl in config.URGENCY_BUCKETS)),
    ("Agent call", f"{config.REPEAT_ATTEMPTS}+ attempts or {config.REPEAT_CONTACTS}+ support contacts, "
                   "recoverable cases only."),
    ("Messages", f"Written in preferred_language. {config.RESUME_LINK_TOKEN} is filled by the sending "
                 "system. Non-English text is pending a native-speaker check."),
    ("Nothing is sent", "Every message needs approval_status = Approved by a person before sending."),
]


def summary_frame(summary: dict) -> pd.DataFrame:
    rows = [
        ("Applications in file", summary["total_applications"]),
        ("Activated (out of scope)", summary["activated"]),
        ("Non-activated", summary["non_activated"]),
        ("  with reviewer code", summary["with_code"]),
        ("  reviewer note only", summary["note_only"]),
        ("  no code, no note (abandoned)", summary["no_code_no_note"]),
        ("Recoverable", summary["recoverable"]),
        ("Recoverable expected TPV (Rs lakh / month)", summary["recoverable_tpv_lakh"]),
        ("Closed (restricted category)", summary["closed"]),
        ("Closed expected TPV (Rs lakh / month)", summary["closed_tpv_lakh"]),
        ("Compliance review", summary["compliance"]),
        ("Compliance expected TPV (Rs lakh / month)", summary["compliance_tpv_lakh"]),
        ("Could not diagnose", summary["undiagnosed"]),
        ("Flagged for human review", summary["needs_human_review"]),
        ("  of which High priority (high-value merchant)", summary["high_priority_reviews"]),
        ("Safety checker: blocked / warned messages", f"{summary['safety_blocked']} / {summary['safety_warn']}"),
        ("Agent calls (recoverable repeat triers)", summary["agent_calls"]),
        ("WhatsApp / Email", f"{summary['whatsapp']} / {summary['email']}"),
    ]
    for i, c in enumerate(summary["top_3_causes"], 1):
        rows.append((f"Top cause {i}", f"{c['code']} {c['cause']}: {c['merchants']} merchants, "
                                       f"{c['expected_tpv_lakh']} lakh"))
    if "approvals" in summary:
        a = summary["approvals"]
        rows.append(("Messages approved / edited / rejected / pending",
                     f"{a.get('Approved', 0)} / {a.get('edited', 0)} / {a.get('Rejected', 0)} / {a.get('Pending', 0)}"))
    for k, v in summary["urgency_recoverable"].items():
        rows.append((f"Urgency (recoverable): {k}", v))
    labels = {"accuracy_Seen in tuning half": "Accuracy test: seen phrasings",
              "accuracy_New phrasing": "Accuracy test: new phrasings",
              "accuracy_All test rows": "Accuracy test: all test rows",
              "checker_redteam": "Checker red team", "checker_false_alarms": "Checker false alarms"}
    for k, lbl in labels.items():
        if k in summary:
            rows.append((lbl, summary[k]))
    lc = summary["log_counts"]
    rows.append(("Log entries (info / warning / error)", f"{lc['INFO']} / {lc['WARNING']} / {lc['ERROR']}"))
    return pd.DataFrame(rows, columns=["metric", "value"])


def test_sheets(result) -> dict:
    ex = result.extras or {}
    out = {}
    res = result.results
    flagged = res[res["safety_check"] != "Pass"][["application_id", "outcome", "preferred_language",
                                                   "channel", "safety_check", "safety_issues"]]
    out["Safety check"] = flagged if len(flagged) else pd.DataFrame(
        [{"application_id": "", "safety_check": f"All {len(res)} messages passed",
          "safety_issues": ""}])
    if "accuracy" in ex:
        out["Accuracy summary"] = ex["accuracy"]["summary"]
        out["Accuracy test rows"] = ex["accuracy"]["per_row"]
    if "redteam" in ex:
        out["Checker red team"] = ex["redteam"]
    if "false_alarms" in ex:
        out["Checker false alarms"] = ex["false_alarms"]
    return out


def export_excel(result, target, extra_sheets: dict | None = None):
    """target: file path or BytesIO. extra_sheets: {name: DataFrame} added after the standard ones."""
    sheets = {
        "Recovery plan": result.results,
        "Summary": summary_frame(result.summary),
        "Top causes": result.causes,
        **test_sheets(result),
        **(extra_sheets or {}),
        "Run log": result.log.to_frame(),
        "How it works": pd.DataFrame(ABOUT, columns=["topic", "rule"]),
    }
    with pd.ExcelWriter(target, engine="openpyxl") as xw:
        for name, df in sheets.items():
            df.to_excel(xw, sheet_name=name[:31], index=False)
            ws = xw.sheets[name[:31]]
            ws.freeze_panes = "A2"
            for i, col in enumerate(df.columns, 1):
                width = min(max([len(str(col))] + [len(str(v)) for v in df[col].head(200)]) + 2, 60)
                if col in ("message", "evidence", "review_reasons", "rule", "plain_meaning", "safety_issues",
                           "unsafe_text", "issues"):
                    width = 70
                ws.column_dimensions[ws.cell(1, i).column_letter].width = width
            if "message" in df.columns:
                from openpyxl.styles import Alignment
                mcol = list(df.columns).index("message") + 1
                for r in range(2, len(df) + 2):
                    ws.cell(r, mcol).alignment = Alignment(wrap_text=True, vertical="top")
    return target
