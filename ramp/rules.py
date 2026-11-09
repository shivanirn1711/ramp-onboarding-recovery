"""Hand-written keyword rules, tuned ONLY on the tuning half of the coded rows.

A rule fires when ALL of its word stems appear in the reviewer note, in any
order. Each rule records the tuning row it was written from (`evidence`).
`check_rules()` proves two things on the tuning half:
  1. grounded: every rule matches at least one tuning note of its own code
     (so no rule was written from a test-half note), and
  2. clean: no rule fires on a tuning note of a different code.

The catalog keywords (catalog.py) do the rest, and they need no tuning.
Tuning half = output/split_seed42.csv (seed fixed in config.py beforehand).
"""
from dataclasses import dataclass

from .textnorm import NEG, stems


@dataclass(frozen=True)
class Rule:
    code: str
    words: frozenset
    evidence: str            # tuning-half application_id(s) the rule came from
    note: str = ""

    def matches(self, note_stems: set) -> bool:
        return self.words <= note_stems

    def label(self) -> str:
        return "+".join(sorted(self.words))


def R(code, words, evidence, note=""):
    return Rule(code, frozenset(words), evidence, note)


TUNED_RULES = [
    # R01 PAN name mismatch. NOT "pan+name+business": the R03 bank note A3070
    # also says "PAN / business name", so that rule would misfire.
    R("R01", {"pan", "naam"}, "A3036", "Hinglish 'PAN ka naam'"),
    R("R01", {"pan", "card"}, "A3038"),
    # R02 GST
    R("R02", {"gst"}, "A3009"),
    R("R02", {"gstin"}, "A3049"),
    # R03 bank account NAME mismatch (vs R04 bank verification failed)
    R("R03", {"bank", "account", "naam"}, "A3043", "Hinglish 'bank account ka naam'"),
    R("R03", {"account", "name", "mismatch"}, "A3040"),
    R("R03", {"account", "holder", "name"}, "A3070"),
    R("R03", {"bank", "letter"}, "A3040"),
    # R04 bank verification failed
    R("R04", {"ifsc"}, "A3021"),
    R("R04", {"account", "number"}, "A3021,A3068"),
    R("R04", {"bank", "verify", NEG}, "A3068", "Hinglish 'verify nahi hua'"),
    # R05 unreadable document
    R("R05", {"document", "readable", NEG}, "A3089", "'not readable'"),
    # R06 policy pages. Bare "policy" is NOT a rule: it needs page/refund/privacy.
    R("R06", {"policy", "page"}, "A3018,A3080"),
    R("R06", {"refund", "policy"}, "A3018,A3033,A3080"),
    R("R06", {"privacy", "policy"}, "A3033,A3080"),
    # R07 website not live
    R("R07", {"website", "khul", NEG}, "A3019", "Hinglish 'khul nahi raha'"),
    # R08 address proof
    R("R08", {"address", "proof"}, "A3072"),
    # R09 signatory
    R("R09", {"signatory"}, "A3023"),
    # R10 restricted category. Needs 'restrict', or 'support' WITH a negation.
    # "category" alone is never enough: R11 notes also say "category".
    R("R10", {"restrict"}, "A3039"),
    R("R10", {"category", "support", NEG}, "A3093", "'support nahi kar sakte' / 'not supported'"),
    # R12 compliance
    R("R12", {"compliance"}, "A3094,A3096"),
    R("R12", {"screen", "hit"}, "A3094"),
    # R11: no tuning rows (its only coded row fell in the test half), so it
    # relies purely on catalog keywords. Deliberately left that way.
]


def check_rules(rules, tune_rows) -> list:
    """tune_rows: iterable of (application_id, code, message). Returns a list of problems (empty = OK)."""
    rows = [(a, c, stems(m)) for a, c, m in tune_rows]
    problems = []
    for rule in rules:
        own = [a for a, c, s in rows if c == rule.code and rule.matches(s)]
        if not own:
            problems.append(f"NOT GROUNDED: {rule.code} [{rule.label()}] matches no tuning note of its own code")
        cited = set(rule.evidence.split(","))
        tune_ids = {a for a, _, _ in rows}
        if not cited <= tune_ids:
            problems.append(f"BAD EVIDENCE: {rule.code} [{rule.label()}] cites non-tuning rows {sorted(cited - tune_ids)}")
        other = [f"{a}({c})" for a, c, s in rows if c != rule.code and rule.matches(s)]
        if other:
            problems.append(f"MISFIRES: {rule.code} [{rule.label()}] also fires on {', '.join(other)}")
    return problems
