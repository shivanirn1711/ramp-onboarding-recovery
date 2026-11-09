"""The rejection catalog, read from the uploaded file (never hardcoded).

From the catalog we derive:
  * the outcome of each code, from the `fixable` column
      Yes                      -> Recoverable
      No                       -> Closed
      mentions "compliance"    -> Compliance
  * which code means "abandoned" (the one whose reason says abandoned / "No rejection")
  * keyword weights per code, from `reviewer_reason` + `plain_language_meaning`
    (words unique to one code count most; words shared by many codes count little)
"""
import math

import pandas as pd

from .runlog import RunLog
from .textnorm import NEG, stems

REQUIRED_COLUMNS = ["code", "reviewer_reason", "plain_language_meaning", "fixable",
                    "standard_fix", "typical_days_to_fix"]

RECOVERABLE, CLOSED, COMPLIANCE, HUMAN = "Recoverable", "Closed", "Compliance", "Needs human review"


class Catalog:
    def __init__(self, df: pd.DataFrame, log: RunLog):
        self.log = log
        missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
        if missing:
            raise ValueError(f"Rejection catalog is missing columns: {', '.join(missing)}")
        df = df.copy()
        df["code"] = df["code"].astype(str).str.strip().str.upper()
        df = df[df["code"].ne("") & df["code"].ne("NAN")]
        dups = df[df["code"].duplicated()]["code"].tolist()
        if dups:
            log.error("catalog", f"Duplicate codes in catalog, keeping the first: {dups}")
            df = df.drop_duplicates("code")
        self.df = df.set_index("code")
        self.codes = list(self.df.index)
        self.outcomes = {c: self._outcome_from_fixable(c, self.df.at[c, "fixable"]) for c in self.codes}
        self.abandoned_code = self._find_abandoned()
        self.keyword_weights = self._build_keywords()
        log.info("catalog", f"Loaded {len(self.codes)} codes; abandoned code = {self.abandoned_code}; "
                            f"closed = {self.codes_with(CLOSED)}; compliance = {self.codes_with(COMPLIANCE)}")

    # ---------------------------------------------------------------- outcome
    def _outcome_from_fixable(self, code, value) -> str:
        v = str(value).strip().lower()
        if v == "yes":
            return RECOVERABLE
        if v == "no":
            return CLOSED
        if "compliance" in v:
            return COMPLIANCE
        self.log.warn("catalog", f"Code {code}: unrecognised 'fixable' value '{value}', sending to human review")
        return HUMAN

    def codes_with(self, outcome) -> list:
        return [c for c, o in self.outcomes.items() if o == outcome]

    def _find_abandoned(self):
        hits = []
        for c in self.codes:
            reason = str(self.df.at[c, "reviewer_reason"]).lower()
            meaning = str(self.df.at[c, "plain_language_meaning"]).lower()
            if "abandon" in reason or meaning.startswith("no rejection"):
                hits.append(c)
        if len(hits) != 1:
            self.log.error("catalog", f"Expected exactly one 'abandoned' code, found {hits}. "
                                      "Applications with no code and no note will go to human review.")
            return None
        return hits[0]

    # --------------------------------------------------------------- keywords
    def _build_keywords(self) -> dict:
        """code -> {stem: weight}. Weight = how unique the word is across codes (0..1)."""
        words = {}
        for c in self.codes:
            if c == self.abandoned_code:      # a note exists, so it is never "abandoned"
                continue
            text = f"{self.df.at[c, 'reviewer_reason']} {self.df.at[c, 'plain_language_meaning']}"
            words[c] = stems(text) - {NEG}
        n = max(len(words), 2)
        doc_freq = {}
        for ws in words.values():
            for w in ws:
                doc_freq[w] = doc_freq.get(w, 0) + 1
        return {c: {w: math.log(n / doc_freq[w]) / math.log(n) for w in ws} for c, ws in words.items()}

    # ---------------------------------------------------------------- lookups
    def has(self, code) -> bool:
        return code in self.outcomes

    def outcome(self, code) -> str:
        return self.outcomes.get(code, HUMAN)

    def field(self, code, column) -> str:
        if code not in self.df.index:
            return ""
        return str(self.df.at[code, column])
