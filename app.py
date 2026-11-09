"""RAMP: onboarding and KYC drop-off recovery (Streamlit app).

Rules + a pre-written message library. No live AI, no API keys.
Nothing is sent: every message needs a person's approval.

Run locally:  streamlit run app.py
All paths are relative to this file, so it runs the same on Streamlit Cloud.
"""
import dataclasses
import io
import json
import re
from datetime import datetime
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

from ramp import config
from ramp.catalog import CLOSED, COMPLIANCE, HUMAN, RECOVERABLE
from ramp.checker import summarise as summarise_issues
from ramp.diagnose import SRC_ABANDONED, SRC_CODE, SRC_KEYWORDS, SRC_TEMPLATE
from ramp.export import ABOUT, export_excel
from ramp.pipeline import run
from ramp.textnorm import stem, stems

APP_DIR = Path(__file__).resolve().parent
DEMO_FILE = APP_DIR / "demo_data" / "RAMP_demo_P3.xlsx"
LOG_FILE = APP_DIR / "logs" / "ramp_run_log.csv"
FIRST_RESULT = APP_DIR / "output" / "accuracy_first_result.json"

BLUE = {"light": "#2a78d6", "dark": "#3987e5"}   # emphasis hue (validated, see README)
GRAY = "#898781"                                   # de-emphasis, same in both modes
INK = {"light": "#52514e", "dark": "#c3c2b7"}
OUTCOME_ORDER = {RECOVERABLE: 0, COMPLIANCE: 1, CLOSED: 2, HUMAN: 3}
OUTCOME_COLOR = {RECOVERABLE: "green", CLOSED: "gray", COMPLIANCE: "violet", HUMAN: "orange"}

st.set_page_config(page_title="RAMP · onboarding recovery", page_icon=":material/restart_alt:",
                   layout="wide")


# ----------------------------------------------------------------- helpers
def nn(v):
    """NaN / NA -> None, everything else unchanged."""
    try:
        return None if pd.isna(v) else v
    except (TypeError, ValueError):
        return v


def theme() -> str:
    try:
        return "dark" if st.context.theme.type == "dark" else "light"
    except Exception:
        return "light"


def load(apps_src, cat_src, label, snapshot="auto"):
    """Run the engine and reset the review state."""
    try:
        with st.spinner("Running RAMP: diagnosing, drafting and checking every message..."):
            r = run(apps_src, cat_src, log_file=str(LOG_FILE), snapshot_date=snapshot)
    except ValueError as e:
        st.session_state.load_error = str(e)
        return
    except Exception as e:  # unreadable file etc.
        st.session_state.load_error = f"Could not read the file(s): {e}"
        return
    for k in [k for k in st.session_state if k.startswith(("msg_", "note_", "confirm_", "cases_"))]:
        del st.session_state[k]
    plan = r.results.copy()
    plan["edited"] = False
    plan["reviewed_at"] = ""
    st.session_state.update(run=r, plan=plan, source=label, load_error=None, flash=None)


def plan_row(app_id):
    plan = st.session_state.plan
    i = plan.index[plan["application_id"] == app_id][0]
    return i, plan.loc[i]


def library_text(row) -> str:
    r = st.session_state.run
    if not nn(row["diagnosed_code"]):
        return ""
    return r.library.compose(row["diagnosed_code"], row["outcome"], row["preferred_language"], row["channel"],
                             row["application_id"], row["step_reached"], quiet=True)["message"]


def check_text(row, text):
    rec = {"application_id": row["application_id"], "diagnosed_code": nn(row["diagnosed_code"]),
           "outcome": row["outcome"], "channel": row["channel"], "whatsapp_opted_in": row["whatsapp_opted_in"],
           "preferred_language": row["preferred_language"], "message_language": row["message_language"],
           "step_reached": row["step_reached"], "rejection_message": nn(row["rejection_message"]),
           "message": text, "email_subject": nn(row["email_subject"]) or ""}
    return summarise_issues(st.session_state.run.checker.check(rec))


def wilson_low(k, n, z=1.96) -> float:
    """95% lower bound for a success rate k/n (Wilson score interval)."""
    if not n:
        return 0.0
    p = k / n
    return (p + z * z / (2 * n) - z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5)) / (1 + z * z / n)


def matched_words(note: str, code: str) -> dict:
    """The note's own words that scored for `code` (readable, unlike word stems), and the rules that fired."""
    d = st.session_state.run.diagnoser
    weights = d.cat.keyword_weights.get(code, {})
    seen, words = set(), []
    for tok in re.findall(r"\w+", str(note)):
        s = stem(tok.lower())
        if s in weights and s not in seen:
            seen.add(s)
            words.append(f"{tok} ({weights[s]:.2f})")
    note_stems = stems(note)
    rules = [r.label().replace("NEG", "not/nahi") for r in d.rules if r.code == code and r.matches(note_stems)]
    return {f"Rules (+{config.TUNED_RULE_WEIGHT:g} each)": ", ".join(rules) or "-",
            "Catalog words (weight)": ", ".join(words) or "-"}


def log_event(level, message, app_id=""):
    st.session_state.run.log.add_and_save(level, "review", message, app_id, path=str(LOG_FILE))


# ------------------------------------------------------- review callbacks
def save_message(app_id, approve=False):
    plan = st.session_state.plan
    i, row = plan_row(app_id)
    text = st.session_state.get(f"msg_{app_id}", row["message"]) or ""
    changed = text != (row["message"] or "")
    status, issues = check_text(row, text)
    lib = library_text(row)
    plan.at[i, "message"] = text
    plan.at[i, "edited"] = bool(lib) and text.strip() != lib.strip()
    plan.at[i, "safety_check"] = status
    plan.at[i, "safety_issues"] = issues
    plan.at[i, "approver_notes"] = st.session_state.get(f"note_{app_id}", "")
    if not approve:
        if changed:
            if row["approval_status"] == "Approved":
                plan.at[i, "approval_status"] = "Pending"
            log_event("INFO", f"Message edited by reviewer; safety check: {status}", app_id)
        st.session_state.flash = ("info", f"Saved. Safety check: {status}.", app_id)
        return
    if status == "Blocked":
        plan.at[i, "approval_status"] = "Pending"
        log_event("WARNING", f"Approval refused by the safety checker: {issues}", app_id)
        st.session_state.flash = ("error", "Not approved: the safety checker blocked this message.", app_id)
        return
    if row["needs_human_review"] and not st.session_state.get(f"confirm_{app_id}"):
        st.session_state.flash = ("error", "Tick the confirmation box first: this case needs a human check.", app_id)
        return
    plan.at[i, "approval_status"] = "Approved"
    plan.at[i, "reviewed_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    log_event("INFO", "Approved" + (" (edited)" if plan.at[i, "edited"] else "")
              + (" after human review" if row["needs_human_review"] else ""), app_id)
    st.session_state.flash = ("success", "Approved.", app_id)


def reject_message(app_id):
    plan = st.session_state.plan
    i, _ = plan_row(app_id)
    note = st.session_state.get(f"note_{app_id}", "")
    plan.at[i, "approval_status"] = "Rejected"
    plan.at[i, "approver_notes"] = note
    plan.at[i, "reviewed_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    log_event("WARNING", f"Rejected by reviewer. Notes: {note or '(none)'}", app_id)
    st.session_state.flash = ("warning", "Rejected. It will not be sent.", app_id)


def reset_message(app_id):
    plan = st.session_state.plan
    i, row = plan_row(app_id)
    lib = library_text(row)
    st.session_state[f"msg_{app_id}"] = lib
    status, issues = check_text(row, lib)
    plan.at[i, "message"], plan.at[i, "edited"] = lib, False
    plan.at[i, "safety_check"], plan.at[i, "safety_issues"] = status, issues
    if row["approval_status"] == "Approved":
        plan.at[i, "approval_status"] = "Pending"
    st.session_state.flash = ("info", "Back to the library text.", app_id)


def bulk_approve(ids):
    plan = st.session_state.plan
    mask = plan["application_id"].isin(ids)
    plan.loc[mask, "approval_status"] = "Approved"
    plan.loc[mask, "reviewed_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    log_event("INFO", f"Bulk-approved {len(ids)} messages that passed the checker and needed no review")
    st.session_state.flash = ("success", f"Approved {len(ids)} messages.", None)


# ---------------------------------------------------------------- export
def export_bytes() -> bytes:
    r, plan = st.session_state.run, st.session_state.plan
    summary = dict(r.summary)
    counts = plan["approval_status"].value_counts().to_dict()
    counts["edited"] = int(plan["edited"].sum())
    summary["approvals"] = counts
    summary["log_counts"] = r.log.counts()
    buf = io.BytesIO()
    export_excel(dataclasses.replace(r, results=plan, summary=summary), buf)
    return buf.getvalue()


# ------------------------------------------------------------------ charts
def cause_chart(causes: pd.DataFrame, measure: str, axis_title: str, fmt: str):
    mode = theme()
    df = causes.copy()
    df["cause_label"] = df["code"] + "  " + df["cause"]
    df["group"] = df["is_top_3"].map({True: "Top 3 cause (by merchants)", False: "Other cause"})
    order = df.sort_values([measure, "merchants"], ascending=False)["cause_label"].tolist()
    base = alt.Chart(df).encode(
        y=alt.Y("cause_label:N", sort=order, title=None, axis=alt.Axis(labelLimit=280, ticks=False, domain=False)),
        x=alt.X(f"{measure}:Q", title=axis_title,
                scale=alt.Scale(domainMax=float(df[measure].max()) * 1.15, nice=False),  # room for the tip label
                axis=alt.Axis(grid=True, gridColor="#e1e0d9" if mode == "light" else "#2c2c2a", tickCount=5)),
    )
    bars = base.mark_bar(size=16, cornerRadiusEnd=4).encode(
        color=alt.Color("group:N", title=None,
                        scale=alt.Scale(domain=["Top 3 cause (by merchants)", "Other cause"],
                                        range=[BLUE[mode], GRAY]),
                        legend=None),   # one shared legend is drawn above both charts
        tooltip=[alt.Tooltip("code:N", title="Code"), alt.Tooltip("cause:N", title="Cause"),
                 alt.Tooltip("merchants:Q", title="Merchants"),
                 alt.Tooltip("expected_tpv_lakh:Q", title="Expected TPV (lakh/month)", format=".1f"),
                 alt.Tooltip("share_of_recoverable_pct:Q", title="Share of recoverable (%)", format=".1f")],
    )
    labels = base.mark_text(align="left", dx=4, color=INK[mode]).encode(text=alt.Text(f"{measure}:Q", format=fmt))
    return (bars + labels).properties(height=max(200, 28 * len(df) + 40))


# ================================================================= sidebar
with st.sidebar:
    st.header("Load data")
    if st.button("Load demo data", type="primary", width="stretch", icon=":material/play_arrow:"):
        load(str(DEMO_FILE), None, "Demo data (synthetic Data Pack, Problem 3)")
    st.caption("The build-a-thon's synthetic Data Pack, Problem 3 tabs only.")
    st.divider()
    st.subheader("Or upload your own")
    apps_up = st.file_uploader("Applications (.xlsx or .csv)", type=["xlsx", "csv"])
    cat_up = st.file_uploader("Rejection catalog (.xlsx or .csv)", type=["xlsx", "csv"],
                              help="Leave empty if the catalog is a tab in the same workbook.")
    with st.expander("Snapshot date (optional)"):
        st.caption("Used only for the 'idle longer than signed up' data check. Read from a README tab when present.")
        use_snap = st.checkbox("Set it manually")
        snap = st.date_input("Snapshot date", value=datetime(2026, 9, 30), disabled=not use_snap)
    if st.button("Run on uploaded files", width="stretch", disabled=apps_up is None):
        label = apps_up.name + (f" + {cat_up.name}" if cat_up else "")
        load(apps_up, cat_up, label, snap if use_snap else "auto")
    if st.session_state.get("load_error"):
        st.error(st.session_state.load_error)
    if "plan" in st.session_state:
        st.divider()
        p = st.session_state.plan
        done = int((p["approval_status"] != "Pending").sum())
        st.caption(f"Loaded: **{st.session_state.source}**")
        st.progress(done / max(len(p), 1), text=f"Reviewed {done} of {len(p)} messages")


# ================================================================== header
head, dl = st.columns([4, 1], vertical_alignment="bottom")
with head:
    st.title("RAMP")
    st.caption("Onboarding and KYC drop-off recovery. Rules and a pre-written message library: no live AI, "
               "no API keys. Nothing is sent without a person's approval.")

if "plan" not in st.session_state:
    st.info("Load the demo data (or upload an applications file and rejection catalog) to start.",
            icon=":material/info:")
    if st.button("Load demo data", type="primary", key="demo_main", icon=":material/play_arrow:"):
        load(str(DEMO_FILE), None, "Demo data (synthetic Data Pack, Problem 3)")
        st.rerun()
    st.markdown("""
**What RAMP does for each application that has not activated**
1. Diagnoses why it stalled: reviewer code, else the reviewer's note, else "abandoned at step X".
2. Decides: recoverable, closed (restricted category) or sent to compliance, using the uploaded catalog.
3. Picks the channel (WhatsApp only if opted in), ranks by expected TPV and tags urgency by days idle.
4. Drafts the message in the merchant's preferred language from a pre-written library.
5. Runs a safety checker on every message, and waits for a person to approve, edit or reject.
""")
    st.stop()

R = st.session_state.run
PLAN = st.session_state.plan
S = R.summary
with dl:
    st.download_button("Download Excel", data=export_bytes(), type="primary", width="stretch",
                       file_name=f"RAMP_recovery_plan_{datetime.now():%Y%m%d_%H%M}.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                       icon=":material/download:")

tab_overview, tab_review, tab_tests, tab_how = st.tabs(["Overview", "Review & approve", "Tests & log", "How it works"])


# ================================================================ overview
with tab_overview:
    k = st.columns(4)
    k[0].metric("Recoverable", S["recoverable"], help="Fixable cause per the catalog, including abandoned",
                border=True)
    k[1].metric("TPV at stake, per month", f"{S['recoverable_tpv_lakh']:,.1f} lakh",
                help="Expected monthly TPV of the recoverable merchants (Rs lakh)", border=True)
    k[2].metric("Closed", S["closed"], help="Restricted business category: polite close, no workarounds",
                border=True)
    k[3].metric("Compliance", S["compliance"], help="Routed to Compliance; merchant only hears it is under review",
                border=True)
    k2 = st.columns(4)
    k2[0].metric("Human review", S["needs_human_review"], border=True,
                 help="Unsure, borderline or high-value text diagnoses, inferred closures, blocked messages")
    k2[1].metric("Agent calls", S["agent_calls"], border=True,
                 help=f"Recoverable merchants with {config.REPEAT_ATTEMPTS}+ attempts or "
                      f"{config.REPEAT_CONTACTS}+ support contacts")
    k2[2].metric("WhatsApp / email", f"{S['whatsapp']} / {S['email']}", border=True,
                 help="WhatsApp only when the merchant opted in")
    reviewed = int((PLAN["approval_status"] != "Pending").sum())
    k2[3].metric("Messages reviewed", f"{reviewed} / {len(PLAN)}", border=True,
                 help="Approved or rejected so far in this session")
    st.caption(f"{S['non_activated']} of {S['total_applications']} applications are not activated: "
               f"{S['with_code']} with a reviewer code, {S['note_only']} with only a reviewer note, "
               f"{S['no_code_no_note']} with neither (abandoned).")

    causes = R.causes
    if len(causes):
        st.subheader("What is blocking recoverable merchants")
        mode = theme()
        swatch = ("<span style='display:inline-block;width:10px;height:10px;border-radius:2px;"
                  "background:{c};margin:0 6px 0 0;vertical-align:baseline'></span>")
        st.markdown(f"{swatch.format(c=BLUE[mode])}Top 3 causes by merchants affected"
                    f"&emsp;{swatch.format(c=GRAY)}Other causes", unsafe_allow_html=True)
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**Merchants blocked, by cause**")
            st.altair_chart(cause_chart(causes, "merchants", "Merchants", "d"), width="stretch")
        with c2:
            st.markdown("**Expected TPV blocked, by cause** (Rs lakh / month)")
            st.altair_chart(cause_chart(causes, "expected_tpv_lakh", "Rs lakh / month", ".1f"), width="stretch")
        top = causes.head(3)
        by_tpv = causes.sort_values("expected_tpv_lakh", ascending=False).iloc[0]
        dense = causes.assign(per=causes["expected_tpv_lakh"] / causes["merchants"]).sort_values(
            "per", ascending=False).iloc[0]
        st.markdown(
            "**Top 3 causes by merchants:** " + "; ".join(
                f"{r.code} {r.cause} ({r.merchants} merchants, {r.expected_tpv_lakh:g} lakh)" for r in top.itertuples())
            + f".  \nBy TPV the biggest is **{by_tpv.code}** ({by_tpv.expected_tpv_lakh:g} lakh). "
              f"**{dense.code}** blocks only {dense.merchants} merchant(s) but {dense.expected_tpv_lakh:g} lakh.")
        with st.expander("Table view"):
            st.dataframe(causes, hide_index=True, width="stretch")

    u1, u2 = st.columns(2)
    with u1:
        st.markdown("**Recoverable merchants by days idle**")
        order = [lbl for _, lbl in config.URGENCY_BUCKETS]
        urg = pd.DataFrame([{"urgency": u, "merchants": S["urgency_recoverable"].get(u, 0)} for u in order])
        st.dataframe(urg, hide_index=True, width="stretch")
    with u2:
        lc = R.log.counts()
        st.markdown("**Data checks**")
        if lc["WARNING"] or lc["ERROR"]:
            st.warning(f"{lc['WARNING']} warnings and {lc['ERROR']} errors in the run log "
                       "(for example, idle days longer than the time since signup). See Tests & log.",
                       icon=":material/warning:")
        else:
            st.success("No data problems found.", icon=":material/check_circle:")


# ================================================================== review
with tab_review:
    f1, f2, f3, f4 = st.columns(4)
    outcomes = [o for o in OUTCOME_ORDER if o in set(PLAN["outcome"])]
    f_out = f1.multiselect("Outcome", outcomes, placeholder="All outcomes")
    f_lang = f2.multiselect("Language", sorted(PLAN["preferred_language"].dropna().unique()), placeholder="All languages")
    urg_opts = [lbl for _, lbl in config.URGENCY_BUCKETS if lbl in set(PLAN["urgency"])]
    f_urg = f3.multiselect("Priority (days idle)", urg_opts, placeholder="All")
    f_status = f4.multiselect("Approval", ["Pending", "Approved", "Rejected"], placeholder="All")
    t1, t2, t3 = st.columns(3)
    only_review = t1.toggle("Only cases needing human review")
    only_high = t2.toggle(f"Only high-value (>= {config.HIGH_TPV_LAKH:g} lakh)")
    only_agent = t3.toggle("Only agent calls")

    view = PLAN.copy()
    if f_out:
        view = view[view["outcome"].isin(f_out)]
    if f_lang:
        view = view[view["preferred_language"].isin(f_lang)]
    if f_urg:
        view = view[view["urgency"].isin(f_urg)]
    if f_status:
        view = view[view["approval_status"].isin(f_status)]
    if only_review:
        view = view[view["needs_human_review"]]
    if only_high:
        view = view[view["high_value"]]
    if only_agent:
        view = view[view["agent_call"]]
    view = (view.assign(_rv=~view["needs_human_review"], _hp=view["review_priority"] != "High",
                        _o=view["outcome"].map(OUTCOME_ORDER),
                        _r=pd.to_numeric(view["priority_rank"], errors="coerce").fillna(10_000))
            .sort_values(["_rv", "_hp", "_o", "_r", "application_id"]).reset_index(drop=True))

    n_review = int(PLAN["needs_human_review"].sum())
    if n_review:
        ids = ", ".join(PLAN.loc[PLAN["needs_human_review"], "application_id"])
        st.warning(f"**{n_review} case(s) need a human check before anything is sent:** {ids}. "
                   "They are listed first.", icon=":material/person_alert:")

    if view.empty:
        st.info("No cases match these filters.")
    else:
        table = pd.DataFrame({
            "Flag": view.apply(lambda r: ("⚠ Review · High" if r["review_priority"] == "High" else "⚠ Review")
                               if r["needs_human_review"] else "", axis=1),
            "Rank": ["" if pd.isna(v) else str(int(v)) for v in view["priority_rank"]],
            "ID": view["application_id"],
            "Outcome": view["outcome"],
            "Cause": view["diagnosed_code"].fillna("?") + "  " + view["cause"].fillna(""),
            "TPV (lakh)": view["expected_monthly_tpv_inr_lakh"],
            "Days idle": view["days_since_last_activity"],
            "Language": view["preferred_language"],
            "Channel": view["channel"],
            "Agent call": view["agent_call"].map({True: "Yes", False: ""}),
            "Check": view["safety_check"],
            "Approval": view["approval_status"],
        })
        filt_key = "cases_" + str(abs(hash((tuple(f_out), tuple(f_lang), tuple(f_urg), tuple(f_status),
                                             only_review, only_high, only_agent))))
        event = st.dataframe(
            table, hide_index=True, width="stretch", height=min(38 * len(table) + 40, 330),
            on_select="rerun", selection_mode="single-row", key=filt_key,
            column_config={"TPV (lakh)": st.column_config.NumberColumn(format="%.1f"),
                           "Rank": st.column_config.NumberColumn(help="Recoverable only, by expected TPV")})
        st.caption(f"{len(view)} case(s) shown. Click a row to open it; the first one opens by default.")

        eligible = view[(~view["needs_human_review"]) & (view["safety_check"] == "Pass")
                        & (view["approval_status"] == "Pending") & (view["message"].fillna("") != "")]
        with st.expander(f"Bulk approve ({len(eligible)} shown cases are eligible)"):
            st.caption("Approves every case shown above that passed the safety checker, needs no human review "
                       "and is still pending. Review cases are never bulk-approved.")
            st.button("Approve these", disabled=eligible.empty, on_click=bulk_approve,
                      args=(eligible["application_id"].tolist(),))

        rows = event.selection.rows if event and event.selection else []
        sel = view.iloc[rows[0]]["application_id"] if rows else view.iloc[0]["application_id"]
        _, row = plan_row(sel)

        # ---------------------------------------------------- case detail
        st.divider()
        flash = st.session_state.get("flash")
        if flash and flash[2] in (sel, None):
            getattr(st, flash[0])(flash[1])
            st.session_state.flash = None

        code = nn(row["diagnosed_code"])
        title = f"{sel} · {code or 'no diagnosis'} {row['cause'] or ''}"
        st.subheader(title)
        conf_c = {"High": "green", "Borderline": "orange", "Low": "red", "Unclear": "red"}.get(row["confidence"], "gray")
        chk_c = {"Pass": "green", "Warn": "orange", "Blocked": "red"}.get(row["safety_check"], "gray")
        apr_c = {"Approved": "green", "Rejected": "red"}.get(row["approval_status"], "gray")
        st.markdown(f":{OUTCOME_COLOR.get(row['outcome'], 'gray')}-badge[{row['outcome']}] "
                    f":{conf_c}-badge[Confidence: {row['confidence']}] "
                    f":{chk_c}-badge[Safety check: {row['safety_check']}] "
                    f":{apr_c}-badge[Approval: {row['approval_status']}]")
        if row["needs_human_review"]:
            reasons = [x for x in str(row["review_reasons"]).split(" | ") if x]
            st.error("**Needs human review" + (" · High priority" if row["review_priority"] == "High" else "")
                     + "**\n\n" + "\n".join(f"- {x}" for x in reasons), icon=":material/person_alert:")

        left, right = st.columns(2, gap="large")
        with left:
            st.markdown("#### Why this diagnosis")
            st.markdown(f"**Reviewer code:** {nn(row['rejection_code']) or 'none'}  \n"
                        f"**Reviewer note:** {('“' + row['rejection_message'] + '”') if nn(row['rejection_message']) else 'none'}  \n"
                        f"**Step reached:** {row['step_reached']} · **Status:** {row['application_status']}")
            src = row["diagnosis_source"]
            if src == SRC_CODE:
                st.markdown(f"**Method:** the reviewer's code **{code}** is used as given.")
            elif src == SRC_TEMPLATE:
                st.markdown(f"**Method:** no code, but the note is word for word the same as coded cases. "
                            f"{row['evidence']}.")
            elif src == SRC_ABANDONED:
                st.markdown(f"**Method:** no code and no note, so the merchant simply stopped. "
                            f"Abandoned at **{row['step_reached']}**; the message names that step.")
            elif src == SRC_KEYWORDS:
                st.markdown("**Method:** keyword rules on the note. Each code scores catalog words (unique words "
                            f"count most) plus {config.TUNED_RULE_WEIGHT:g} per hand-written rule hit.")
                note = row["rejection_message"]
                scores = R.diagnoser.keyword_scores(note)[:4]
                st.dataframe(pd.DataFrame([{"Code": c, "Score": s, **matched_words(note, c),
                                            "Cause": R.catalog.field(c, "reviewer_reason")} for c, s, _ in scores]),
                             hide_index=True, width="stretch")
                st.caption(f"Sure = score >= {config.MIN_SCORE:g} and a lead >= {config.MIN_MARGIN:g} over the "
                           f"runner-up. Within {config.BORDERLINE_BAND:g} of either bar = borderline, sent to a person.")
            else:
                st.markdown(f"**Method:** {src}. {row['evidence']}")
            if code:
                st.markdown(f"**Catalog meaning:** {R.catalog.field(code, 'plain_language_meaning')}  \n"
                            f"**Fixable (catalog):** {row['fixable']}  \n"
                            f"**Catalog fix (internal):** {R.catalog.field(code, 'standard_fix')}")
            st.markdown("#### Decision")
            ch_reason = "opted in to WhatsApp" if row["whatsapp_opted_in"] == "Yes" else "not opted in to WhatsApp"
            rank = nn(row["priority_rank"])
            st.markdown(
                f"**Owner:** {row['owner']}  \n**Next action:** {row['next_action']}  \n"
                f"**Channel:** {row['channel']} ({ch_reason})  \n"
                f"**Expected TPV:** {row['expected_monthly_tpv_inr_lakh']:g} lakh / month"
                + (f" · **Priority rank:** {int(rank)} of {S['recoverable']}" if rank else "") + "  \n"
                f"**Urgency:** {row['urgency']}  \n"
                f"**Attempts / support contacts:** {row['attempts']:g} / {row['support_contacts']:g}"
                + (" → repeat trier" if row["repeat_trier"] else "")
                + (" → **agent call**" if row["agent_call"] else
                   (" (no recovery call: owned by its team)" if row["repeat_trier"] else "")))

        with right:
            st.markdown("#### Message")
            if not (row["message"] or st.session_state.get(f"msg_{sel}")):
                st.info("No message drafted for this case; it stays with a person.")
            else:
                st.caption(f"{row['message_language']} · {row['channel']}"
                           + (f" · {row['native_check']}" if nn(row["native_check"]) else ""))
                if row["channel"] == "Email" and nn(row["email_subject"]):
                    st.markdown(f"**Subject:** {row['email_subject']}")
                key = f"msg_{sel}"
                if key not in st.session_state:
                    st.session_state[key] = row["message"]
                fixed = row["outcome"] in (CLOSED, COMPLIANCE)
                st.text_area("Message text", key=key, height=300, disabled=fixed,
                             label_visibility="collapsed")
                if fixed:
                    st.caption("Closure and compliance messages use fixed approved wording and cannot be edited.")
                status, issues = check_text(row, st.session_state[key])
                if status == "Pass":
                    st.success("Safety check: pass", icon=":material/check_circle:")
                else:
                    box = st.error if status == "Blocked" else st.warning
                    box(f"**Safety check: {status}**\n\n" + "\n".join(f"- {x}" for x in issues.split("; ")),
                        icon=":material/shield:")
                nkey = f"note_{sel}"
                if nkey not in st.session_state:
                    st.session_state[nkey] = nn(row["approver_notes"]) or ""
                st.text_input("Reviewer notes (optional)", key=nkey)
                if row["needs_human_review"]:
                    st.checkbox("I have checked the diagnosis and this message", key=f"confirm_{sel}")
                ok_to_approve = status != "Blocked" and (not row["needs_human_review"]
                                                         or st.session_state.get(f"confirm_{sel}", False))
                a1, a2, a3, a4 = st.columns(4)
                a1.button("Approve", type="primary", on_click=save_message, args=(sel, True),
                          disabled=not ok_to_approve, width="stretch")
                a2.button("Save edit", on_click=save_message, args=(sel, False), disabled=fixed,
                          width="stretch")
                a3.button("Reject", on_click=reject_message, args=(sel,), width="stretch")
                a4.button("Reset", on_click=reset_message, args=(sel,), disabled=fixed, width="stretch",
                          help="Back to the library text")
                if nn(row["reviewed_at"]):
                    st.caption(f"Last decision: {row['approval_status']} at {row['reviewed_at']}")


# =================================================================== tests
with tab_tests:
    acc = R.extras.get("accuracy")
    st.subheader("Accuracy test of the diagnosis rules")
    if acc is None:
        st.info("The accuracy test could not run on this batch (see the run log).")
    else:
        st.markdown(
            f"Coded rows split 50/50 with a fixed seed **before** any rule was written "
            f"({acc['n_tune']} tune / {acc['n_test']} test). Rules may only learn from the tune half. "
            "On each test row the reviewer code is hidden and predicted from the note alone. "
            "**Seen** = the note is word for word a tuning note; **New** = a phrasing the rules never saw.")
        sm = acc["summary"].rename(columns={
            "group": "Group", "rows": "Rows", "correct_code": "Correct", "accuracy_pct": "Accuracy %",
            "keyword_rules_alone_correct": "Rules alone correct", "keyword_rules_alone_pct": "Rules alone %",
            "correct_outcome": "Correct outcome", "sent_to_human_review": "Sent to review",
            "auto_accepted": "Auto-accepted", "auto_accepted_correct": "Auto-accepted & correct",
            "wrong_and_auto_accepted": "Wrong & auto-accepted"})
        st.dataframe(sm, hide_index=True, width="stretch")
        is_frozen_data = False
        if FIRST_RESULT.exists():
            first = json.loads(FIRST_RESULT.read_text(encoding="utf-8"))
            a = [(r["application_id"], r["predicted_code"], bool(r["sent_to_human_review"])) for r in first["per_row"]]
            b = [(r.application_id, r.predicted_code, bool(r.sent_to_human_review))
                 for r in acc["per_row"].itertuples()]
            is_frozen_data = a == b
            if is_frozen_data:
                st.success(f"Identical to the frozen first result recorded {first['run_at'].replace('T', ' ')} "
                           f"(rule fingerprint {first['rule_code_fingerprint']}).", icon=":material/lock:")
            else:
                st.info("This batch differs from the data the first result was frozen on, so this is a fresh "
                        "test on this batch's own coded rows.")
        pr = acc["per_row"]
        if len(pr):
            new = pr[pr["phrasing"] == "New phrasing"]
            n_seen = len(pr) - len(new)
            lines = [f"- The test has {len(pr)} rows; {n_seen} of them are word-for-word copies of tuning notes "
                     "(reviewers reuse sentences), so the template match does the work there."]
            if len(new):
                lines.append(f"- The {len(new)} new-phrasing rows are {new['reviewer_note'].nunique()} distinct "
                             f"sentence(s). With so few rows, {pr['correct'].sum()}/{len(pr)} correct is consistent "
                             f"with true accuracy as low as {100 * wilson_low(pr['correct'].sum(), len(pr)):.0f}% "
                             f"overall and {100 * wilson_low(new['correct'].sum(), len(new)):.0f}% on new phrasings "
                             "(95% lower bounds).")
                lines.append(f"- On new phrasings the rules were *sure* (High confidence) on "
                             f"{(new['confidence'] == 'High').sum()} of {len(new)}; "
                             f"{int(new['sent_to_human_review'].sum())} went to a person.")
            if is_frozen_data:
                lines.append("- Whoever wrote the rules had read every note of this data beforehand. The fixed seed "
                             "and the rule-grounding check limit that, but do not remove it.")
            else:
                lines.append("- The rules were written on the Data Pack, not on this batch, so this is a fresh test "
                             "of them (apart from any notes this batch shares with the Data Pack).")
            st.markdown("**How to read it honestly**\n" + "\n".join(lines))
        with st.expander("Every test row"):
            st.dataframe(acc["per_row"], hide_index=True, width="stretch")

    st.subheader("Safety checker tests")
    fa, rt = R.extras.get("false_alarms"), R.extras.get("redteam")
    if fa is not None and rt is not None:
        plain, tricky = rt[~rt["tricky"]], rt[rt["tricky"]]
        m = st.columns(3)
        m[0].metric("False alarms on library text", f"{int((fa['issues'] != '').sum())} / {len(fa)}", border=True)
        m[1].metric("Plain unsafe messages caught", f"{int(plain['caught_by_expected_check'].sum())} / {len(plain)}",
                    border=True)
        m[2].metric("Tricky paraphrases flagged", f"{int(tricky['flagged_at_all'].sum())} / {len(tricky)}",
                    border=True, help=f"{int(tricky['blocked_by_any_check'].sum())} blocked, rest warned as edited")
        st.markdown("""
- The red team is deliberately unsafe messages in all six languages: promises, dates, "nothing is wrong",
  workarounds, compliance hints, wrong channel, wrong language.
- **First run:** the word lists caught 45 of 45 plain phrasings but **0 of 7** tricky paraphrases.
- **Fix (structural, not new words):** a closure or compliance message that differs from the library text is
  blocked; any other edited message is flagged "read before approving".
- Since the fix, the tricky cases are no longer an independent test.""")
        with st.expander("Red-team cases"):
            st.dataframe(rt, hide_index=True, width="stretch")

    st.subheader("Run log")
    logdf = R.log.to_frame()
    lv = st.multiselect("Level", ["ERROR", "WARNING", "INFO"], default=["ERROR", "WARNING"])
    st.dataframe(logdf[logdf["level"].isin(lv)] if lv else logdf, hide_index=True, width="stretch")
    st.download_button("Download log (CSV)", data=logdf.to_csv(index=False).encode("utf-8-sig"),
                       file_name="ramp_run_log.csv", mime="text/csv")


# ============================================================ how it works
with tab_how:
    st.markdown("""
**Who decides what**

| Step | Done by |
|---|---|
| Diagnosis | Rules: reviewer code, else identical coded note, else keyword rules, else abandoned |
| Recoverable / closed / compliance | The uploaded catalog's `fixable` column |
| Channel, priority, urgency, agent call | Fixed rules (below) |
| Message text | Pre-written library in six languages, never generated |
| Safety | Checker on every message and every edit |
| Sending | **A person**, by approving. Unsure, borderline and high-value text diagnoses, inferred closures and blocked messages always need a person first |
""")
    st.markdown("**The rules**\n" + "\n".join(f"- **{topic}:** {rule}" for topic, rule in ABOUT))
    st.caption("Non-English messages are marked as pending a native-speaker check.")
