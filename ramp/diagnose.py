"""Why did each application stall?

Order of evidence, strongest first:
  1. Reviewer code present and in the catalog            -> use it.
  2. No code, but the note is word-for-word the same as a note on a coded row
     in the reference set ("template match")             -> use that row's code.
  3. No code, new note -> keyword scoring:
        score(code) = catalog keyword weights + 2.0 per hand-written rule hit
     High confidence only if best >= 2.0 and beats the runner-up by >= 1.0.
  4. No code and no note                                 -> abandoned code (from catalog).
Then a step check: if the code is usually seen at a different step (learned
from the reference rows) confidence drops to Low.
Low / Unclear confidence always means human review.

`reference` = the coded rows the diagnoser may learn templates and usual steps
from. In normal runs that is every coded row of the batch; in the accuracy
test it is only the tuning half.
"""
from collections import Counter

import pandas as pd

from . import config
from .catalog import Catalog
from .rules import TUNED_RULES
from .runlog import RunLog
from .textnorm import stems, template_key

SRC_CODE = "Reviewer code"
SRC_TEMPLATE = "Same note as a coded case"
SRC_KEYWORDS = "Keyword rules on reviewer note"
SRC_ABANDONED = "No code and no note (abandoned)"
SRC_NONE = "Could not diagnose"


class Diagnoser:
    def __init__(self, catalog: Catalog, reference: pd.DataFrame, log: RunLog, rules=TUNED_RULES):
        self.cat = catalog
        self.log = log
        self.rules = [r for r in rules if catalog.has(r.code)]
        dropped = sorted({r.code for r in rules} - {r.code for r in self.rules})
        if dropped:
            log.warn("diagnose", f"Rules for codes not in this catalog were ignored: {dropped}")
        ref = reference[reference["rejection_code"].notna() & reference["rejection_message"].notna()]
        ref = ref[ref["rejection_code"].map(catalog.has)]
        # templates: exact reviewer sentence -> code (dropped if one sentence maps to 2 codes)
        by_key = {}
        for app_id, code, msg in ref[["application_id", "rejection_code", "rejection_message"]].itertuples(index=False):
            by_key.setdefault(template_key(msg), {}).setdefault(code, []).append(app_id)
        self.templates = {}
        for key, codes in by_key.items():
            if len(codes) == 1:
                (code, ids), = codes.items()
                self.templates[key] = (code, ids)
            else:
                log.warn("diagnose", f"The same reviewer note is used for different codes {sorted(codes)}; "
                                     "it will not be used as a template")
        # usual step per code
        ref_steps = ref.dropna(subset=["step_reached"])
        self.usual_step = {code: Counter(g["step_reached"]).most_common(1)[0][0]
                           for code, g in ref_steps.groupby("rejection_code")}

    # ------------------------------------------------------------- scoring
    def keyword_scores(self, note: str) -> list:
        """Sorted list of (code, score, evidence-text) for every code that scored above zero."""
        s = stems(note)
        out = []
        for code, weights in self.cat.keyword_weights.items():
            cat_hits = sorted(w for w in weights if w in s)
            cat_score = sum(weights[w] for w in cat_hits)
            rule_hits = [r for r in self.rules if r.code == code and r.matches(s)]
            score = cat_score + config.TUNED_RULE_WEIGHT * len(rule_hits)
            if score > 0:
                parts = []
                if rule_hits:
                    parts.append("rules " + ", ".join(f"[{r.label()}]" for r in rule_hits))
                if cat_hits:
                    parts.append("catalog words " + ", ".join(cat_hits))
                out.append((code, round(score, 2), "; ".join(parts)))
        return sorted(out, key=lambda x: (-x[1], x[0]))

    # ----------------------------------------------------------- diagnose
    def diagnose(self, row) -> dict:
        app_id = row["application_id"]
        code, note, step = row["rejection_code"], row["rejection_message"], row["step_reached"]
        has_code, has_note = pd.notna(code), pd.notna(note)
        res = {"diagnosed_code": None, "diagnosis_source": SRC_NONE, "confidence": "Unclear",
               "evidence": "", "runner_up": "", "review_reasons": []}

        if has_code and self.cat.has(code):
            res.update(diagnosed_code=code, diagnosis_source=SRC_CODE, confidence="High",
                       evidence="Code given by the reviewer")
        elif has_code:
            self.log.error("diagnose", f"Reviewer code '{code}' is not in the catalog", app_id)
            res["review_reasons"].append(f"Unknown reviewer code '{code}'")
            if has_note:
                self._from_note(note, app_id, res)
        elif has_note:
            self._from_note(note, app_id, res)
        else:
            ab = self.cat.abandoned_code
            if ab is None:
                res["review_reasons"].append("No code, no note, and the catalog has no 'abandoned' code")
            else:
                res.update(diagnosed_code=ab, diagnosis_source=SRC_ABANDONED, confidence="High",
                           evidence=f"No rejection recorded; stopped at '{step}'")
                if row["application_status"] == "Rejected":
                    res["confidence"] = "Low"
                    res["review_reasons"].append("Status is Rejected but there is no code or note")
                    self.log.warn("diagnose", "Rejected with no code and no note", app_id)

        # step check (only for inferred diagnoses; a reviewer code is taken as given)
        dc = res["diagnosed_code"]
        if dc and res["diagnosis_source"] in (SRC_TEMPLATE, SRC_KEYWORDS):
            usual = self.usual_step.get(dc)
            if usual and pd.notna(step) and step != usual:
                res["confidence"] = "Low"
                res["review_reasons"].append(f"{dc} is usually seen at '{usual}', but this merchant is at '{step}'")
        if res["confidence"] in ("Low", "Unclear", "Borderline") and not res["review_reasons"]:
            res["review_reasons"].append("Keyword rules are not sure")
        return res

    def _from_note(self, note, app_id, res):
        key = template_key(note)
        if key in self.templates:
            code, ids = self.templates[key]
            shown = ", ".join(ids[:3]) + (" ..." if len(ids) > 3 else "")
            res.update(diagnosed_code=code, diagnosis_source=SRC_TEMPLATE, confidence="High",
                       evidence=f"Exact same reviewer note as {shown} (coded {code})")
            return
        g = self.keyword_guess(note)
        res.update(diagnosed_code=g["code"], diagnosis_source=SRC_KEYWORDS, confidence=g["confidence"],
                   evidence=g["evidence"], runner_up=g["runner_up"])
        if g["confidence"] == "Unclear":
            res["review_reasons"].append("Reviewer note matched no keyword")
            self.log.warn("diagnose", f"Could not diagnose note: '{note}'", app_id)
        elif g["confidence"] == "Low":
            res["review_reasons"].append(f"Keyword rules not sure: {g['code']} {g['score']} vs {g['runner_up']}")
            self.log.warn("diagnose", f"Low-confidence keyword diagnosis {g['code']} for note '{note}'", app_id)
        elif g["confidence"] == "Borderline":
            res["review_reasons"].append(
                f"Borderline keyword diagnosis: {g['code']} scored {g['score']} (bar {config.MIN_SCORE}), "
                f"margin {g['margin']} (bar {config.MIN_MARGIN})")
            self.log.info("diagnose", f"Borderline keyword diagnosis {g['code']} sent to review", app_id)

    def keyword_guess(self, note) -> dict:
        """Keyword rules alone. confidence: High / Borderline / Low / Unclear."""
        scores = self.keyword_scores(note)
        if not scores or scores[0][1] < config.UNCLEAR_BELOW:
            return {"code": None, "score": 0.0, "margin": 0.0, "confidence": "Unclear",
                    "evidence": "No keyword matched", "runner_up": ""}
        best = scores[0]
        second = scores[1] if len(scores) > 1 else (None, 0.0, "")
        margin = round(best[1] - second[1], 2)
        if best[1] < config.MIN_SCORE or margin < config.MIN_MARGIN:
            conf = "Low"
        elif best[1] < config.MIN_SCORE + config.BORDERLINE_BAND or margin < config.MIN_MARGIN + config.BORDERLINE_BAND:
            conf = "Borderline"
        else:
            conf = "High"
        return {"code": best[0], "score": best[1], "margin": margin, "confidence": conf,
                "evidence": f"{best[0]} scored {best[1]} ({best[2]})",
                "runner_up": f"{second[0]} scored {second[1]}" if second[0] else "none"}
