"""What happens to each diagnosed application.

outcome   : from the catalog's `fixable` column (Recoverable / Closed / Compliance),
            or "Needs human review" if we could not diagnose.
owner     : who acts on it.
channel   : WhatsApp only if the merchant opted in, otherwise Email.
urgency   : bucket of days since last activity (config.URGENCY_BUCKETS).
repeat    : 3+ attempts or 2+ support contacts.
agent call: repeat trier AND recoverable (closed/compliance stay with their team).
Priority rank (by expected TPV) is set later in pipeline.py, across all rows.
"""
import pandas as pd

from . import config
from .catalog import CLOSED, COMPLIANCE, HUMAN, RECOVERABLE, Catalog
from .diagnose import SRC_CODE, SRC_KEYWORDS
from .runlog import RunLog

OWNER = {
    RECOVERABLE: "Onboarding recovery team",
    CLOSED: "Onboarding ops (polite closure)",
    COMPLIANCE: "Compliance team",
    HUMAN: "Human review queue",
}


def urgency(days) -> str:
    if pd.isna(days):
        return "Unknown (no activity data)"
    for upper, label in config.URGENCY_BUCKETS:
        if upper is None or days <= upper:
            return label
    return config.URGENCY_BUCKETS[-1][1]


def decide(row, diag: dict, cat: Catalog, log: RunLog) -> dict:
    app_id = row["application_id"]
    code = diag["diagnosed_code"]
    reasons = list(diag["review_reasons"])
    outcome = cat.outcome(code) if code else HUMAN

    # Closing or routing to compliance is hard to undo: if we only INFERRED it
    # from note text (no reviewer code), a person must confirm it.
    if outcome in (CLOSED, COMPLIANCE) and diag["diagnosis_source"] != SRC_CODE:
        reasons.append(f"{outcome} was inferred from the note, not given as a code: a person must confirm")

    # High-value merchants: a wrong message costs the most, so a diagnosis that
    # rests only on keyword rules always gets a person's check.
    tpv = row["expected_monthly_tpv_inr_lakh"]
    high_value = bool(pd.notna(tpv) and tpv >= config.HIGH_TPV_LAKH)
    if high_value and diag["diagnosis_source"] == SRC_KEYWORDS:
        reasons.append(f"High-value merchant ({tpv:g} lakh >= {config.HIGH_TPV_LAKH:g}) and the cause "
                       "was inferred from note text only")

    # Status should agree with the outcome (Rejected <-> not fixable)
    status = row["application_status"]
    if outcome == RECOVERABLE and status == "Rejected":
        reasons.append("Status is Rejected but the cause looks fixable")
        log.warn("decide", f"Status Rejected but diagnosed {code} (fixable)", app_id)
    if outcome in (CLOSED, COMPLIANCE) and status == "Stalled":
        reasons.append(f"Status is Stalled but the cause is {outcome.lower()}")
        log.warn("decide", f"Status Stalled but diagnosed {code} ({outcome})", app_id)

    attempts, contacts = row["attempts"], row["support_contacts"]
    repeat = bool((pd.notna(attempts) and attempts >= config.REPEAT_ATTEMPTS) or
                  (pd.notna(contacts) and contacts >= config.REPEAT_CONTACTS))
    agent_call = repeat and outcome == RECOVERABLE
    channel = "WhatsApp" if row["whatsapp_opted_in"] == "Yes" else "Email"

    if outcome == RECOVERABLE:
        action = f"Send {channel} nudge" + (" + agent call (repeat trier)" if agent_call else "")
    elif outcome == CLOSED:
        action = f"Close politely by {channel} after approval. No workarounds."
    elif outcome == COMPLIANCE:
        action = f"Hand to Compliance. Merchant only gets an 'under review' {channel} message."
    else:
        action = "Human review before any message"

    return {
        "outcome": outcome,
        "fixable": cat.field(code, "fixable") if code else "",
        "owner": OWNER[outcome],
        "channel": channel,
        "urgency": urgency(row["days_since_last_activity"]),
        "repeat_trier": repeat,
        "agent_call": agent_call,
        "next_action": action,
        "high_value": high_value,
        "review_reasons": reasons,
    }
