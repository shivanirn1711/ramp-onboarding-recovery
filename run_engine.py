"""Run the RAMP engine from the command line (no UI).

    python run_engine.py <applications.xlsx> [catalog.xlsx]

If the catalog is in the same workbook, give one file. Writes
output/ramp_recovery_plan.xlsx and appends to logs/ramp_run_log.csv.
"""
import sys

from ramp.export import export_excel
from ramp.pipeline import run


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    apps = sys.argv[1]
    cat = sys.argv[2] if len(sys.argv) > 2 else None
    r = run(apps, cat)
    s = r.summary
    print(f"Applications: {s['total_applications']}  activated: {s['activated']}  non-activated: {s['non_activated']}")
    print(f"  with code {s['with_code']} | note only {s['note_only']} | neither {s['no_code_no_note']}")
    print(f"Recoverable {s['recoverable']} ({s['recoverable_tpv_lakh']} lakh) | closed {s['closed']} | "
          f"compliance {s['compliance']} | undiagnosed {s['undiagnosed']}")
    print(f"Human review {s['needs_human_review']} | agent calls {s['agent_calls']} | "
          f"WhatsApp {s['whatsapp']} / Email {s['email']}")
    print("Top 3 causes:", "; ".join(f"{c['code']} ({c['merchants']})" for c in s["top_3_causes"]))
    print(f"Safety checker: {s['safety_blocked']} blocked, {s['safety_warn']} warned | "
          f"high-priority reviews {s['high_priority_reviews']}")
    for k in ["accuracy_Seen in tuning half", "accuracy_New phrasing", "accuracy_All test rows",
              "checker_redteam", "checker_false_alarms"]:
        if k in s:
            print(f"  {k}: {s[k]}")
    print("Log:", s["log_counts"])
    out = "output/ramp_recovery_plan.xlsx"
    export_excel(r, out)
    print("Wrote", out)


if __name__ == "__main__":
    main()
