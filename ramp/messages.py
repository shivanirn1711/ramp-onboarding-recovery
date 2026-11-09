"""Build each merchant message from the pre-written library (no live AI).

Safety on load:
  * Every library code records the reviewer reason it was written for. If the
    uploaded catalog gives that code a DIFFERENT reason, the template is
    switched off for this run (a renumbered catalog must never get the wrong
    message), and those merchants go to human review.
  * Missing pieces per language are logged; those languages fall back to English
    for that code, flagged for human review.

Message shapes:
  fix        : greeting, intro, problem, "What to do:" fix, resume link, after-fix, help, sign-off
  abandoned  : greeting, intro, "you stopped at the <step> step", resume link, help, sign-off
  closed     : greeting, intro, polite close (no workarounds), sign-off
  compliance : greeting, intro, "under review" only, sign-off
"""
import json
import os
import re

from . import config
from .catalog import CLOSED, COMPLIANCE, RECOVERABLE, Catalog
from .runlog import RunLog

DEFAULT_LIBRARY = os.path.join(os.path.dirname(__file__), "message_library.json")
COMMON_KEYS = ["greeting", "intro", "fix_label", "resume", "after_fix", "help", "signoff",
               "subject_fix", "subject_abandoned", "subject_update"]


def _norm_reason(s) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(s).lower()).strip()


class MessageLibrary:
    def __init__(self, cat: Catalog, log: RunLog, path: str = DEFAULT_LIBRARY):
        self.cat, self.log = cat, log
        with open(path, encoding="utf-8") as f:
            self.lib = json.load(f)
        self.langs = self.lib["languages"]
        self.native_review = self.lib.get("native_review", {})
        self.enabled = self._check_against_catalog()
        self._check_completeness()

    def _check_against_catalog(self) -> set:
        enabled = set()
        for code, expected in self.lib["expected_reasons"].items():
            if not self.cat.has(code):
                continue
            actual = self.cat.field(code, "reviewer_reason")
            if _norm_reason(actual) == _norm_reason(expected):
                enabled.add(code)
            else:
                self.log.error("messages", f"Library template {code} was written for '{expected}' but the "
                                           f"catalog says '{actual}'. Template switched off for this run.")
        for code in self.cat.codes:
            if code not in self.lib["expected_reasons"]:
                self.log.warn("messages", f"Catalog code {code} has no pre-written message; "
                                          "those merchants go to human review")
        return enabled

    def _check_completeness(self):
        for lang, d in self.langs.items():
            missing = [k for k in COMMON_KEYS if not d.get(k)]
            missing += [f"bodies.{c}" for c in self.enabled if c not in d.get("bodies", {})]
            if missing:
                self.log.warn("messages", f"{lang} library is missing: {', '.join(missing)} (English used there)")

    # -------------------------------------------------------------- compose
    def _piece(self, lang, key, code=None, part=None):
        """Get a text piece in `lang`, falling back to English. Returns (text, used_fallback)."""
        for l in (lang, config.FALLBACK_LANGUAGE):
            d = self.langs.get(l, {})
            val = d.get("bodies", {}).get(code, {}).get(part) if code else d.get(key)
            if val:
                return val, l != lang
        return None, True

    def compose(self, code, outcome, language, channel, app_id, step, quiet=False):
        """Return dict(subject, body, message_language, problems[list]). quiet=True: no log entries."""
        warn = (lambda *a, **k: None) if quiet else self.log.warn
        problems = []
        out = {"email_subject": "", "message": "", "message_language": "", "problems": problems}
        if not code:
            problems.append("No diagnosis, so no message drafted")
            return out
        lang = language if language in self.langs else config.FALLBACK_LANGUAGE
        if lang != language:
            problems.append(f"No library for language '{language}', drafted in {lang}")
            warn("messages", f"Unsupported preferred_language '{language}', used {lang}", app_id)

        template_code = code
        if code not in self.enabled:
            # A compliance holding message reveals nothing, so the generic one is always safe.
            generic = self.lib.get("generic_compliance_code")
            if outcome == COMPLIANCE and generic in self.enabled:
                template_code = generic
            else:
                problems.append(f"No usable pre-written message for {code}")
                return out

        if template_code == self.cat.abandoned_code:
            kind = "abandoned"
        elif outcome == CLOSED:
            kind = "closed"
        elif outcome == COMPLIANCE:
            kind = "compliance"
        elif outcome == RECOVERABLE:
            kind = "fix"
        else:
            problems.append(f"Outcome '{outcome}' has no message type")
            return out

        fallback = False

        def get(key=None, part=None):
            nonlocal fallback
            text, fb = self._piece(lang, key, template_code if part else None, part)
            fallback |= fb
            return text or ""

        step_names = self.langs.get(lang, {}).get("steps", {})
        step_text = step_names.get(step)
        if kind == "abandoned" and not step_text:
            step_text = str(step)
            problems.append(f"No translated name for step '{step}', used as is")
            warn("messages", f"Step '{step}' has no translated name in {lang}", app_id)

        fill = {"app_id": app_id, "step": step_text or "", "resume_link": config.RESUME_LINK_TOKEN}
        if kind == "fix":
            lines = [get("greeting"), get("intro"), get(part="problem"),
                     f"{get('fix_label')} {get(part='fix')}", get("resume"), get("after_fix"),
                     get("help"), get("signoff")]
            subject = get("subject_fix")
        elif kind == "abandoned":
            lines = [get("greeting"), get("intro"), get(part="problem"), get("resume"),
                     get("help"), get("signoff")]
            subject = get("subject_abandoned")
        else:  # closed / compliance
            lines = [get("greeting"), get("intro"), get(part="body"), get("signoff")]
            subject = get("subject_update")

        if fallback:
            problems.append(f"Some text missing in {lang}, English used for that part")
        sep = "\n" if channel == "WhatsApp" else "\n\n"
        body = sep.join(l for l in lines if l)
        for k, v in fill.items():
            body = body.replace("{" + k + "}", str(v))
            subject = subject.replace("{" + k + "}", str(v))
        out.update(email_subject=subject if channel == "Email" else "",
                   message=body, message_language=lang)
        if lang != config.FALLBACK_LANGUAGE and "pending" in str(self.native_review.get(lang, "")):
            out["native_check"] = "Pending native-speaker check"
        else:
            out["native_check"] = ""
        return out
