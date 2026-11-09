"""Safety checker: scans every drafted message (and email subject) before approval.

BLOCK = must not be approved as it stands.  WARN = worth a look, not unsafe.

Checks
  promise       promises approval / activation / "guaranteed"            (all messages)
  timeline      dates, days, hours, "soon", "tomorrow"                     (all messages)
  reassurance   "nothing is wrong", "no problem with your application"     (all messages)
  workaround    reapply / other category / resume link in a closure       (closed only)
  compliance    compliance / sanctions / screening words, or reason hints (all; stricter for compliance)
  leak          reviewer note copied in, internal instructions, codes      (all messages)
  wording       closure and compliance messages must keep the approved text exactly
  channel       WhatsApp without opt-in
  language      wrong language, wrong script, Hindi/Marathi or English/Hinglish mix-ups
  structure     empty message, leftover {placeholder}, abandoned message without its step,
                no resume link, very long WhatsApp, email without subject

Word lists cover English, Hinglish, Hindi, Tamil, Kannada, Marathi. They are
pattern lists: they catch what they list, and the red-team test
(checker_tests.py) shows honestly what slips through.
"""
import re
from dataclasses import dataclass

from . import config
from .catalog import CLOSED, COMPLIANCE, RECOVERABLE

DEV, TAM, KAN = "ऀ-ॿ", "஀-௿", "ಀ-೿"


def _w(s, block):
    """Whole word in an Indic script (Python's \\b is unreliable with vowel signs)."""
    return rf"(?<![{block}]){s}(?![{block}])"


def _start(s, block):
    """Word that may take suffixes (Tamil/Kannada/Marathi attach them)."""
    return rf"(?<![{block}]){s}"


PATTERNS = {
    "promise": [
        r"\bapprov(e|ed|al|ing)\b", r"\bguarant", r"\b(definitely|certainly|surely)\b", r"\b100\s?%",
        r"\bactivat\w*", r"\b(go|goes|going) live\b", r"\bwill (definitely |surely )?(be )?(accepted|verified|cleared)\b",
        r"\byou (will|can) (now )?(start )?accept(ing)? payments\b", r"\bpakka\b", r"\bzaroor\b",
        r"\blive ho (jayegi|jayega|jaegi|jaega)\b",
        "मंज़ूर", "मंजूर", "स्वीकृत", "अनुमोदित", "गारंटी", _w("पक्का", DEV), _w("ज़रूर", DEV), _w("जरूर", DEV),
        "निश्चित रूप से", "सक्रिय हो जाएगा", "सक्रिय कर दिया जाएगा", "चालू हो जाएगा",
        "அங்கீகரிக்கப்படும்", "ஒப்புதல்", "உத்தரவாத", "நிச்சயமாக", "செயல்படுத்தப்படும்",
        "ಅನುಮೋದ", "ಖಾತರಿ", "ಖಂಡಿತ", "ಸಕ್ರಿಯಗೊಳಿಸಲಾಗುವುದು", "ಸಕ್ರಿಯಗೊಳ್ಳುತ್ತದೆ",
        "मंजुरी", _w("हमी", DEV), _start("नक्की", DEV), "सक्रिय होईल", "सक्रिय केले जाईल",
    ],
    "timeline": [
        r"\b(within|in|by|after|under)\s+(\d+|a|one|two|three|four|five|few|couple of)\s*(-\s*\d+\s*)?"
        r"(business\s+|working\s+)?(minute|hour|hr|day|week|month)s?\b",
        r"\b\d+\s*(-\s*\d+\s*)?(business\s+|working\s+)?(minutes?|hours?|hrs?|days?|weeks?)\b",
        r"\b(today|tomorrow|tonight|asap|soon|shortly|immediately|right away|instantly|overnight)\b",
        r"\b(next|this|by)\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday|week|month)\b",
        r"\bend of (the )?(day|week|month)\b",
        r"\b\d{1,2}\s*(st|nd|rd|th)?\s*(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b",
        r"\b(jan|feb|mar|apr|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+\d{1,2}\b",
        r"\b\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}\b", r"\b(typically|usually) takes\b",
        r"\b(aaj|kal|parso)\b", r"\bjaldi\b", r"\bturant\b",
        r"\b\d+\s*(din|dino|dinon|ghante|ghanton|hafte|hafton)\b", r"\b\d+\s*mahin[eo]\s*(mein|me|tak|ke andar)\b",
        r"\b(ek|do|teen|char|paanch)\s+(din|ghante|hafte)\b",
        r"\d+\s*(दिन|घंटे|घंटों|हफ़्ते|हफ्ते|सप्ताह)", r"\d+\s*महीने\s*(में|के भीतर|तक)",
        _w("आज", DEV), _w("कल", DEV), _start("जल्द", DEV), "तुरंत", "शीघ्र",
        r"\d+\s*(நாட்கள|நாளில|நாளுக்குள|மணி நேர|வாரத்)", r"\d+\s*மாதங்களில்", "நாளை",
        _start("இன்று", TAM), "விரைவில்", "உடனடியாக",
        r"\d+\s*(ದಿನ|ಗಂಟೆ|ವಾರ)", r"\d+\s*ತಿಂಗಳಲ್ಲಿ", "ನಾಳೆ", _start("ಇಂದು", KAN), "ಶೀಘ್ರ", "ತಕ್ಷಣ",
        _start("ಬೇಗ", KAN),
        r"\d+\s*(दिवस|तास|आठवड)", r"\d+\s*महिन्यांत", "उद्या", _start("लवकर", DEV), "ताबडतोब", "त्वरित",
    ],
    "reassurance": [
        r"\bnothing (is |was )?wrong\b", r"\bno (problem|issue|error)s? with (your|the) application\b",
        r"\b(everything|all) (is |looks |seems )?(fine|good|ok|okay|in order|correct)\b",
        r"\bthere (is|was) no (problem|issue)\b", r"\byour application is (fine|perfect|correct)\b",
        r"\bkoi (problem|issue|dikkat|galti|gadbad) nahi\b", r"\bsab (theek|thik|sahi|badhiya) hai\b",
        r"\bkuch (galat|gadbad) nahi\b",
        "कोई समस्या नहीं", "कोई दिक्कत नहीं", "कोई परेशानी नहीं", "कोई गड़बड़ी नहीं", "कुछ गलत नहीं",
        "कुछ ग़लत नहीं", "सब ठीक", "सब कुछ ठीक",
        "பிரச்சனை இல்லை", "பிரச்சினை இல்லை", "பிரச்சனையும் இல்லை", "எல்லாம் சரி", "தவறு இல்லை",
        "ಸಮಸ್ಯೆ ಇಲ್ಲ", "ಸಮಸ್ಯೆಯಿಲ್ಲ", "ಎಲ್ಲವೂ ಸರಿ", "ತಪ್ಪಿಲ್ಲ", "ತಪ್ಪು ಇಲ್ಲ",
        "अडचण नाही", "समस्या नाही", "सर्व ठीक", "सगळं ठीक", "काहीही चूक नाही", "काही चूक नाही",
    ],
    "workaround": [   # closed messages only
        r"\bre-?apply", r"\bapply again\b", r"\btry again\b",
        r"\b(different|another|other|new|correct|right) (business )?category\b",
        r"\bchange (your|the) (business )?category\b", r"\bselect (a|another|the correct|a different) category\b",
        r"\bworkaround\b", r"\balternative", r"\binstead\b", r"\bother (option|way)s?\b", r"\bresubmit",
        r"\bappeal\b",
        r"\b(dobara|phir se|fir se) apply\b", r"\bcategory (badal|change)", r"\b(doosri|dusri|alag|nayi) category\b",
        r"\bkoi aur tarika\b",
        "दोबारा आवेदन", "फिर से आवेदन", "श्रेणी बदल", "दूसरी श्रेणी", "अन्य श्रेणी", "विकल्प", "कोई और तरीका",
        "மீண்டும் விண்ணப்ப", "வேறு வகை", "வகையை மாற்ற", "மாற்று வழி",
        "ಮತ್ತೆ ಅರ್ಜಿ", "ಮರು ಅರ್ಜಿ", "ಬೇರೆ ವರ್ಗ", "ವರ್ಗವನ್ನು ಬದಲಾ", "ಪರ್ಯಾಯ",
        "पुन्हा अर्ज", "दुसरी श्रेणी", "श्रेणी बदल", "पर्याय",
    ],
    "compliance": [   # never in ANY merchant message
        r"\bcomplian", r"\bsanction", r"\bscreen(ing|ed)?\b", r"\bwatch ?list", r"\bblack ?list",
        r"\bnegative list", r"\bAML\b", r"\bmoney laundering", r"\bfraud", r"\bterror", r"\bPEP\b",
        "अनुपालन", "प्रतिबंध", "स्क्रीनिंग", "काली सूची", "धोखाधड़ी", "मनी लॉन्ड्रिंग",
        "இணக்க", "தடை", "கருப்புப் பட்டியல்", "மோசடி",
        "ಅನುಸರಣೆ", "ನಿರ್ಬಂಧ", "ಕಪ್ಪು ಪಟ್ಟಿ", "ವಂಚನೆ",
        "निर्बंध", "काळी यादी", "फसवणूक",
    ],
    "compliance_strict": [   # extra words, compliance messages only
        r"\blegal\b", r"\bpolice\b", r"\binvestigat", r"\bsuspicious\b", r"\bflagged\b", r"\brisk\b",
        r"\bmatched\b", r"\blist\b", r"\breason\b",
        "कानूनी", "संदिग्ध", "कारण", "சந்தேக", "காரண", "ಅನುಮಾನ", "ಕಾರಣ", "संशय", "चौकशी",
    ],
    "leak": [
        r"\bdo not discuss\b", r"\broute to\b", r"\bpolitely close\b", r"\bdo not suggest\b",
        r"\btell the applicant\b", r"\bapplicant\b", r"\breason code\b", r"\bR\d{2}\b", r"\breviewer\b",
        r"\binternal\b",
    ],
}
_COMPILED = {k: [re.compile(p, re.IGNORECASE) for p in v] for k, v in PATTERNS.items()}

SCRIPT_OF = {"English": "latin", "Hinglish": "latin", "Hindi": "dev", "Marathi": "dev",
             "Tamil": "tam", "Kannada": "kan"}
_SCRIPT_RE = {"latin": re.compile(r"[A-Za-z]"), "dev": re.compile(f"[{DEV}]"),
              "tam": re.compile(f"[{TAM}]"), "kan": re.compile(f"[{KAN}]")}
MIN_SCRIPT_SHARE = 0.40      # Indic messages keep brand/technical words (Razorpay, PAN, GSTIN) in Latin
_HINGLISH = re.compile(r"\b(hai|hain|aap|aapki|aapka|aapke|karein|nahi|kya|ke|ki|ka|mein|hum|humein|hamari|"
                       r"yeh|aur|toh|ya)\b", re.IGNORECASE)
_HINDI_MARKERS = ["है", "आप", "करें", "हमें", "हमारी", "नहीं"]
_MARATHI_MARKERS = ["आहे", "तुम", "करा", "आम्ही", "आमची", "नाही", "च्या"]
WHATSAPP_MAX = 1000


@dataclass
class Issue:
    severity: str
    check: str
    detail: str


def _norm(s) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", str(s).lower())).strip()


class SafetyChecker:
    def __init__(self, library, catalog):
        self.lib = library
        self.cat = catalog
        langs = library.langs
        self.fix_labels = [d.get("fix_label", "") for d in langs.values() if d.get("fix_label")]

    def _kind(self, rec):
        code, outcome = rec.get("diagnosed_code"), rec.get("outcome")
        if code and code == self.cat.abandoned_code:
            return "abandoned"
        return {RECOVERABLE: "fix", CLOSED: "closed", COMPLIANCE: "compliance"}.get(outcome)

    def _library_text(self, rec):
        """The exact text the library would draft for this record (None if it cannot draft one)."""
        if not rec.get("diagnosed_code"):
            return None
        m = self.lib.compose(rec.get("diagnosed_code"), rec.get("outcome"), rec.get("preferred_language"),
                             rec.get("channel"), rec.get("application_id"), rec.get("step_reached"), quiet=True)
        return m["message"] or None

    def check(self, rec: dict) -> list:
        issues = []
        add = lambda sev, chk, det: issues.append(Issue(sev, chk, det))
        msg = rec.get("message") or ""
        subject = rec.get("email_subject") or ""
        text = f"{subject}\n{msg}"
        kind = self._kind(rec)
        lang = rec.get("message_language") or ""
        pref = rec.get("preferred_language") or ""

        # structure
        if not msg.strip():
            if kind:
                add("BLOCK", "structure", "No message drafted")
            return issues
        if re.search(r"\{\w+\}", text):
            add("BLOCK", "structure", "Unfilled placeholder: " + ", ".join(re.findall(r"\{\w+\}", text)))

        # word patterns
        def scan(name, severity, label):
            for p in _COMPILED[name]:
                m = p.search(text)
                if m:
                    add(severity, label, f"'{m.group(0)}'")
        scan("promise", "BLOCK", "promise")
        scan("timeline", "BLOCK", "timeline")
        scan("reassurance", "BLOCK", "reassurance")
        scan("compliance", "BLOCK", "compliance")
        scan("leak", "BLOCK", "leak")
        if kind == "compliance":
            scan("compliance_strict", "BLOCK", "compliance")
        if kind == "closed":
            scan("workaround", "BLOCK", "workaround")

        note = rec.get("rejection_message")
        if isinstance(note, str) and len(note) >= 12 and _norm(note) in _norm(text):
            add("BLOCK", "leak", "Reviewer's internal note is copied into the message")

        # type-specific content
        token = config.RESUME_LINK_TOKEN
        if kind in ("closed", "compliance"):
            if token in msg:
                add("BLOCK", "workaround" if kind == "closed" else "compliance",
                    "Resume link in a closure/compliance message invites the merchant to keep going")
            if any(lbl and lbl in msg for lbl in self.fix_labels):
                add("BLOCK", "workaround" if kind == "closed" else "compliance",
                    "Contains 'what to do' fix instructions")

        # edited away from the library? (added after the first red-team run, see checker_tests.py)
        lib_text = self._library_text(rec)
        if kind and lib_text and msg.strip() != lib_text.strip():
            if kind in ("closed", "compliance"):
                add("BLOCK", "wording", f"{kind.title()} message differs from the approved library text; "
                                        "closures and compliance messages may not be edited")
            else:
                add("WARN", "edited", "Edited from the library text: read the whole message before approving")
        if kind in ("fix", "abandoned") and token not in msg:
            add("WARN", "structure", "No resume link")
        if kind == "abandoned":
            step = rec.get("step_reached")
            step_name = self.lib.langs.get(lang, {}).get("steps", {}).get(step, step)
            if not step_name or str(step_name) not in msg:
                add("BLOCK", "structure", f"Abandoned message does not name the step ('{step}')")

        # channel
        if rec.get("channel") == "WhatsApp" and rec.get("whatsapp_opted_in") != "Yes":
            add("BLOCK", "channel", "WhatsApp chosen but the merchant has not opted in")
        if rec.get("channel") == "WhatsApp" and len(msg) > WHATSAPP_MAX:
            add("WARN", "structure", f"Long WhatsApp message ({len(msg)} characters)")
        if rec.get("channel") == "Email" and not subject.strip():
            add("WARN", "structure", "Email has no subject")

        # language
        if pref in SCRIPT_OF and lang != pref:
            add("BLOCK", "language", f"Written in {lang or 'unknown'}, merchant prefers {pref}")
        elif pref and pref not in SCRIPT_OF:
            add("WARN", "language", f"No library for preferred language '{pref}'")
        expected = SCRIPT_OF.get(pref)
        if expected:
            counts = {s: len(r.findall(msg)) for s, r in _SCRIPT_RE.items()}
            total = sum(counts.values()) or 1
            if expected == "latin":
                indic = total - counts["latin"]
                if indic:
                    add("BLOCK", "language", f"{pref} message contains {indic} non-Latin letters")
            elif counts[expected] / total < MIN_SCRIPT_SHARE:
                add("BLOCK", "language", f"Only {100 * counts[expected] / total:.0f}% of letters are in the "
                                         f"{pref} script")
            if pref in ("English", "Hinglish"):
                hits = len(_HINGLISH.findall(msg))
                if pref == "English" and hits >= 2:
                    add("BLOCK", "language", f"English message has {hits} Hinglish words")
                if pref == "Hinglish" and hits < 3:
                    add("BLOCK", "language", "Hinglish message has almost no Hindi words (reads as English)")
            if pref in ("Hindi", "Marathi"):
                hi = sum(msg.count(m) for m in _HINDI_MARKERS)
                mr = sum(msg.count(m) for m in _MARATHI_MARKERS)
                if pref == "Hindi" and mr >= hi:
                    add("BLOCK", "language", f"Reads like Marathi, not Hindi ({mr} Marathi vs {hi} Hindi markers)")
                if pref == "Marathi" and hi >= mr:
                    add("BLOCK", "language", f"Reads like Hindi, not Marathi ({hi} Hindi vs {mr} Marathi markers)")
        return issues


def summarise(issues: list) -> tuple:
    """(status, text) where status is Pass / Warn / Blocked."""
    if not issues:
        return "Pass", ""
    status = "Blocked" if any(i.severity == "BLOCK" for i in issues) else "Warn"
    return status, "; ".join(f"[{i.severity} {i.check}] {i.detail}" for i in issues)
