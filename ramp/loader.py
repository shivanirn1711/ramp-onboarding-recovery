"""Read the uploaded files, find the right sheets and clean the columns.

Works with one workbook holding both tabs, or two separate files. Sheets are
found by name first (P3_Applications / P3_RejectionCatalog), then by their
columns, so a renamed tab still works.
"""
import re
from datetime import datetime

import pandas as pd

from .catalog import REQUIRED_COLUMNS as CATALOG_COLUMNS
from .runlog import RunLog

APP_COLUMNS = [
    "application_id", "business_type", "industry", "city", "preferred_language", "signup_date",
    "expected_monthly_tpv_inr_lakh", "whatsapp_opted_in", "step_reached", "application_status",
    "days_since_last_activity", "attempts", "support_contacts", "rejection_code", "rejection_message",
]
NUMERIC = ["expected_monthly_tpv_inr_lakh", "days_since_last_activity", "attempts", "support_contacts"]


def _read_all_sheets(source) -> dict:
    if hasattr(source, "seek"):
        source.seek(0)
    name = str(getattr(source, "name", source)).lower()
    if name.endswith(".csv"):
        return {"csv": pd.read_csv(source)}
    return pd.read_excel(source, sheet_name=None)


def _pick_sheet(sheets: dict, preferred: str, required: list, what: str) -> pd.DataFrame:
    if preferred in sheets:
        return sheets[preferred]
    for df in sheets.values():
        if all(c in df.columns for c in required):
            return df
    raise ValueError(f"Could not find the {what} sheet: no tab named '{preferred}' and no tab "
                     f"with the columns {', '.join(required)}.")


def find_snapshot_date(sheets: dict):
    """The data pack README states the snapshot date. Returns a date or None."""
    readme = sheets.get("README")
    if readme is None:
        return None
    for _, row in readme.iterrows():
        cells = [str(v) for v in row.values if pd.notna(v)]
        if cells and "snapshot" in cells[0].lower() and len(cells) > 1:
            m = re.search(r"(\d{1,2} \w{3} \d{4})", cells[1])
            if m:
                return datetime.strptime(m.group(1), "%d %b %Y").date()
    return None


def load_inputs(apps_source, catalog_source, log: RunLog):
    """Return (applications DataFrame, catalog DataFrame, snapshot_date or None)."""
    app_sheets = _read_all_sheets(apps_source)
    cat_sheets = app_sheets if catalog_source is None else _read_all_sheets(catalog_source)
    apps = _pick_sheet(app_sheets, "P3_Applications", ["application_id", "rejection_code"], "applications")
    cat = _pick_sheet(cat_sheets, "P3_RejectionCatalog", CATALOG_COLUMNS, "rejection catalog")
    snapshot = find_snapshot_date(app_sheets) or find_snapshot_date(cat_sheets)
    return clean_applications(apps, log), cat.copy(), snapshot


def _blank_to_na(s: pd.Series) -> pd.Series:
    s = s.astype("string").str.strip()
    return s.mask(s.isin(["", "-", "—", "–", "nan", "None", "NaN"]))


def clean_applications(apps: pd.DataFrame, log: RunLog) -> pd.DataFrame:
    missing = [c for c in APP_COLUMNS if c not in apps.columns]
    if missing:
        raise ValueError(f"Applications file is missing columns: {', '.join(missing)}")
    df = apps[APP_COLUMNS].copy()

    for c in ["application_id", "business_type", "industry", "city", "preferred_language",
              "signup_date", "whatsapp_opted_in", "step_reached", "application_status",
              "rejection_code", "rejection_message"]:
        df[c] = _blank_to_na(df[c])
    df["rejection_code"] = df["rejection_code"].str.upper()
    df["preferred_language"] = df["preferred_language"].str.title()
    df["application_status"] = df["application_status"].str.title()

    no_id = df["application_id"].isna()
    if no_id.any():
        log.error("load", f"{int(no_id.sum())} row(s) have no application_id and were skipped")
        df = df[~no_id]
    dup = df["application_id"].duplicated(keep="first")
    for app_id in df.loc[dup, "application_id"]:
        log.error("load", "Duplicate application_id; only the first row was kept", app_id)
    df = df[~dup]

    for c in NUMERIC:
        raw = df[c]
        df[c] = pd.to_numeric(raw, errors="coerce")
        for app_id in df.loc[df[c].isna() & raw.notna(), "application_id"]:
            log.warn("load", f"'{c}' is not a number, treated as blank", app_id)
        for app_id in df.loc[df[c] < 0, "application_id"]:
            log.warn("load", f"'{c}' is negative, treated as blank", app_id)
        df.loc[df[c] < 0, c] = pd.NA
    for app_id in df.loc[df["expected_monthly_tpv_inr_lakh"].isna(), "application_id"]:
        log.warn("load", "Expected TPV missing; counted as 0 for totals and ranked last", app_id)

    wa = df["whatsapp_opted_in"].str.lower()
    odd = wa.notna() & ~wa.isin(["yes", "no", "y", "n", "true", "false"])
    for app_id, v in df.loc[odd, ["application_id", "whatsapp_opted_in"]].itertuples(index=False):
        log.warn("load", f"Unclear WhatsApp opt-in value '{v}', treated as NOT opted in (email)", app_id)
    df["whatsapp_opted_in"] = wa.isin(["yes", "y", "true"]).map({True: "Yes", False: "No"})

    return df.reset_index(drop=True)


def check_activity_dates(df: pd.DataFrame, snapshot, log: RunLog):
    """Flag rows idle for longer than they have existed (impossible, so a data error)."""
    if snapshot is None:
        log.info("data-check", "No snapshot date known, so the signup-vs-idle-days check was skipped")
        return
    signup = pd.to_datetime(df["signup_date"], format="%d %b %Y", errors="coerce")
    for app_id in df.loc[signup.isna() & df["signup_date"].notna(), "application_id"]:
        log.warn("data-check", "signup_date could not be read", app_id)
    age = (pd.Timestamp(snapshot) - signup).dt.days
    bad = df["days_since_last_activity"] > age
    for app_id, d, a in df.loc[bad, ["application_id", "days_since_last_activity"]].assign(a=age[bad]).itertuples(index=False):
        log.warn("data-check", f"Idle for {int(d)} days but signed up only {int(a)} days before the "
                               f"{snapshot:%d %b %Y} snapshot. Used as given for urgency.", app_id)
