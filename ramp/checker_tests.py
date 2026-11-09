"""Two tests of the safety checker.

1. False alarms: render EVERY template in every language, on both channels (and
   every step for the abandoned message) and run the checker. A good checker
   says nothing about our own approved text.
2. Red team: deliberately unsafe messages, each labelled with the check that
   should catch it. Some are written in phrasings the word lists do NOT
   contain ("tricky": True), to show honestly where pattern matching stops.
   Note: the same person wrote the checker and this red team, so this proves
   the patterns work, not that they are complete.

FIRST RUN (word lists only, 2026-11-09): false alarms 0/192 templates;
plain unsafe phrasings caught by the right check 45/45; tricky phrasings
caught 0/7. Fix made after that run (structural, not new words): any message
that differs from the library text is BLOCKED if it is a closure/compliance
message and WARNED otherwise. The tricky cases are therefore no longer an
independent test of that rule.
"""
import pandas as pd

from .catalog import CLOSED, COMPLIANCE, RECOVERABLE
from .checker import SafetyChecker

STEPS = ["Business details", "KYC docs", "Bank verification", "Website/App review"]


def _record(lib, code, outcome, lang, channel, step="KYC docs", app_id="T0001"):
    m = lib.compose(code, outcome, lang, channel, app_id, step)
    return {"application_id": app_id, "diagnosed_code": code, "outcome": outcome, "channel": channel,
            "whatsapp_opted_in": "Yes" if channel == "WhatsApp" else "No", "preferred_language": lang,
            "message_language": m["message_language"], "step_reached": step, "rejection_message": None,
            "message": m["message"], "email_subject": m["email_subject"]}


def false_alarm_test(lib, cat) -> pd.DataFrame:
    chk = SafetyChecker(lib, cat)
    rows = []
    for lang in lib.langs:
        for code in sorted(lib.enabled):
            outcome = cat.outcome(code)
            steps = STEPS if code == cat.abandoned_code else ["KYC docs"]
            for channel in ("WhatsApp", "Email"):
                for step in steps:
                    rec = _record(lib, code, outcome, lang, channel, step)
                    issues = chk.check(rec)
                    rows.append({"language": lang, "code": code, "channel": channel, "step": step,
                                 "issues": "; ".join(f"[{i.severity} {i.check}] {i.detail}" for i in issues)})
    return pd.DataFrame(rows)


# (expected check, language, kind, injected sentence or full message, tricky?)
# kind: fix (R01), abandoned (R13), closed (R10), compliance (R12)
REDTEAM = [
    # promises
    ("promise", "English", "fix", "Once you upload it, your application will be approved.", False),
    ("promise", "English", "fix", "Upload the PAN and you are guaranteed to go live.", False),
    ("promise", "Hinglish", "fix", "PAN upload karte hi aapki application pakka approve ho jayegi.", False),
    ("promise", "Hindi", "fix", "PAN अपलोड करते ही आपका आवेदन मंज़ूर हो जाएगा।", False),
    ("promise", "Tamil", "fix", "PAN-ஐப் பதிவேற்றியதும் உங்கள் விண்ணப்பம் அங்கீகரிக்கப்படும்.", False),
    ("promise", "Kannada", "fix", "PAN ಅಪ್‌ಲೋಡ್ ಮಾಡಿದರೆ ನಿಮ್ಮ ಅರ್ಜಿಗೆ ಅನುಮೋದನೆ ಸಿಗುತ್ತದೆ.", False),
    ("promise", "Marathi", "fix", "PAN अपलोड केल्यावर तुमचा अर्ज नक्की मंजूर होईल.", False),
    ("promise", "English", "fix", "After this, you'll be all set to accept payments.", True),
    ("promise", "Hinglish", "fix", "Bas yeh kar dijiye, baaki sab hum sambhal lenge.", True),
    # timelines / dates
    ("timeline", "English", "fix", "Our team will review it within 24 hours.", False),
    ("timeline", "English", "fix", "This usually takes 2-3 business days to fix.", False),
    ("timeline", "English", "fix", "We will get back to you by Monday.", False),
    ("timeline", "English", "fix", "Please complete this before 15 Oct 2026.", False),
    ("timeline", "English", "fix", "You should hear from us in a couple of days.", True),
    ("timeline", "Hinglish", "fix", "Hamari team 2 din mein review kar degi.", False),
    ("timeline", "Hindi", "fix", "हमारी टीम 2 दिन में समीक्षा करेगी।", False),
    ("timeline", "Tamil", "fix", "எங்கள் குழு 3 நாட்களில் சரிபார்க்கும்.", False),
    ("timeline", "Kannada", "fix", "ನಾಳೆ ನಿಮ್ಮ ಅರ್ಜಿ ಪರಿಶೀಲಿಸಲಾಗುತ್ತದೆ.", False),
    ("timeline", "Marathi", "fix", "आमची टीम उद्या तुमचा अर्ज तपासेल.", False),
    ("timeline", "Hindi", "fix", "हम दो दिनों में आपसे संपर्क करेंगे।", True),
    # "nothing is wrong"
    ("reassurance", "English", "abandoned", "Nothing is wrong with your application, you just need to finish it.", False),
    ("reassurance", "English", "abandoned", "Don't worry, there is no problem with your application.", False),
    ("reassurance", "Hinglish", "abandoned", "Aapki application mein koi problem nahi hai, bas step poora karein.", False),
    ("reassurance", "Hindi", "abandoned", "आपके आवेदन में कोई समस्या नहीं है।", False),
    ("reassurance", "Tamil", "abandoned", "உங்கள் விண்ணப்பத்தில் எந்தப் பிரச்சனையும் இல்லை.", False),
    ("reassurance", "Kannada", "abandoned", "ನಿಮ್ಮ ಅರ್ಜಿಯಲ್ಲಿ ಯಾವುದೇ ಸಮಸ್ಯೆ ಇಲ್ಲ.", False),
    ("reassurance", "Marathi", "abandoned", "तुमच्या अर्जात कोणतीही अडचण नाही.", False),
    ("reassurance", "English", "abandoned", "Your application looks great so far!", True),
    # workarounds in a closure
    ("workaround", "English", "closed", "You could reapply under a different category.", False),
    ("workaround", "Hinglish", "closed", "Aap doosri category mein dobara apply kar sakte hain.", False),
    ("workaround", "Hindi", "closed", "आप किसी दूसरी श्रेणी में दोबारा आवेदन कर सकते हैं।", False),
    ("workaround", "Tamil", "closed", "நீங்கள் வேறு வகையில் மீண்டும் விண்ணப்பிக்கலாம்.", False),
    ("workaround", "Kannada", "closed", "ನೀವು ಬೇರೆ ವರ್ಗದಲ್ಲಿ ಮತ್ತೆ ಅರ್ಜಿ ಸಲ್ಲಿಸಬಹುದು.", False),
    ("workaround", "Marathi", "closed", "तुम्ही दुसरी श्रेणी निवडून पुन्हा अर्ज करू शकता.", False),
    ("workaround", "English", "closed", "Continue your application here: [resume link]", False),
    ("workaround", "English", "closed", "If you remove those products from your website, we can take another look.", True),
    # compliance reasons revealed
    ("compliance", "English", "compliance", "Your application matched a sanctions list during screening.", False),
    ("compliance", "English", "compliance", "Our compliance team is reviewing your application.", False),
    ("compliance", "Hinglish", "compliance", "Aapki application compliance check mein hai.", False),
    ("compliance", "Hindi", "compliance", "आपका आवेदन अनुपालन जाँच में है।", False),
    ("compliance", "Tamil", "compliance", "உங்கள் விண்ணப்பம் இணக்கச் சரிபார்ப்பில் உள்ளது.", False),
    ("compliance", "Kannada", "compliance", "ನಿಮ್ಮ ಅರ್ಜಿ ಅನುಸರಣೆ ಪರಿಶೀಲನೆಯಲ್ಲಿದೆ.", False),
    ("compliance", "Marathi", "compliance", "तुमचा अर्ज अनुपालन तपासणीत आहे.", False),
    ("compliance", "English", "compliance", "Your name came up in a background check, so this is on hold.", True),
    ("leak", "English", "compliance", "Reviewer note: Screening hit - refer compliance", False),
]

# whole-message cases: (expected check, kind, preferred lang, channel, opted_in, message_language, message)
REDTEAM_WHOLE = [
    ("channel", "fix", "English", "WhatsApp", "No", "English", None),
    ("language", "fix", "Tamil", "Email", "No", "English", "ENGLISH_R01"),
    ("language", "fix", "Hindi", "Email", "No", "Hindi", "HINGLISH_R01"),
    ("language", "fix", "Marathi", "Email", "No", "Marathi", "HINDI_R01"),
    ("language", "fix", "English", "Email", "No", "English", "HINGLISH_R01"),
    ("structure", "abandoned", "English", "Email", "No", "English", "NO_STEP"),
    ("structure", "fix", "English", "Email", "No", "English", "PLACEHOLDER"),
]

KIND_CODE = {"fix": ("R01", RECOVERABLE), "abandoned": ("R13", RECOVERABLE),
             "closed": ("R10", CLOSED), "compliance": ("R12", COMPLIANCE)}


def redteam_test(lib, cat) -> pd.DataFrame:
    chk = SafetyChecker(lib, cat)
    rows = []

    def run_case(expected, lang, kind, rec, desc, tricky):
        issues = chk.check(rec)
        fired = sorted({i.check for i in issues if i.severity == "BLOCK"})
        warned = sorted({i.check for i in issues if i.severity == "WARN"})
        rows.append({"expected_check": expected, "language": lang, "message_type": kind, "tricky": tricky,
                     "unsafe_text": desc, "caught_by_expected_check": expected in fired,
                     "blocked_by_any_check": bool(fired), "flagged_at_all": bool(fired or warned),
                     "checks_fired": ", ".join(fired + [f"{w} (warn)" for w in warned])})

    for expected, lang, kind, sentence, tricky in REDTEAM:
        code, outcome = KIND_CODE[kind]
        rec = _record(lib, code, outcome, lang, "Email")
        signoff = lib.langs[lang]["signoff"]
        rec["message"] = rec["message"].replace(signoff, f"{sentence}\n\n{signoff}")
        if expected == "leak":
            rec["rejection_message"] = "Screening hit - refer compliance"
        run_case(expected, lang, kind, rec, sentence, tricky)

    for expected, kind, pref, channel, opted, mlang, variant in REDTEAM_WHOLE:
        code, outcome = KIND_CODE[kind]
        src_lang = {"ENGLISH_R01": "English", "HINGLISH_R01": "Hinglish", "HINDI_R01": "Hindi"}.get(variant, pref)
        rec = _record(lib, code, outcome, src_lang, channel)
        rec.update(preferred_language=pref, message_language=mlang, whatsapp_opted_in=opted)
        desc = {None: "WhatsApp to a merchant who did not opt in",
                "ENGLISH_R01": "English message to a Tamil merchant",
                "HINGLISH_R01": f"Hinglish (Roman) text labelled as {mlang}",
                "HINDI_R01": "Hindi text sent to a Marathi merchant",
                "NO_STEP": "Abandoned reminder that does not name the step",
                "PLACEHOLDER": "Message with an unfilled {step} placeholder"}[variant]
        if variant == "NO_STEP":
            step_name = lib.langs["English"]["steps"]["KYC docs"]
            rec["message"] = rec["message"].replace(f"the {step_name} step", "your application")
        if variant == "PLACEHOLDER":
            rec["message"] = rec["message"] + "\n\nYou stopped at the {step} step."
        run_case(expected, pref, kind, rec, desc, False)
    return pd.DataFrame(rows)
