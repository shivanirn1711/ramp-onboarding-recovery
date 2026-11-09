"""All tunable numbers in one place, so every decision can be explained and changed."""

# --- Repeat-trier / agent-call rule (user rule) ---------------------------
REPEAT_ATTEMPTS = 3          # 3 or more attempts  -> repeat trier
REPEAT_CONTACTS = 2          # 2 or more support contacts -> repeat trier
# Only RECOVERABLE repeat triers get an agent call. Closed and compliance
# cases keep the flag for visibility but stay with their owning team.

# --- Urgency tag by days since last activity -------------------------------
# The longer a merchant has been idle, the more likely they never come back,
# so urgency rises with inactivity. (upper bound in days, label)
URGENCY_BUCKETS = [
    (7, "Low (0-7 days idle)"),
    (14, "Medium (8-14 days idle)"),
    (30, "High (15-30 days idle)"),
    (None, "Critical (31+ days idle)"),
]

# --- Keyword-rule confidence ------------------------------------------------
# A keyword guess is "High" only if the best code scores at least
# MIN_SCORE and beats the runner-up by at least MIN_MARGIN. Anything else
# goes to human review. Below UNCLEAR_BELOW we do not guess at all.
MIN_SCORE = 2.0
MIN_MARGIN = 1.0
UNCLEAR_BELOW = 0.5
TUNED_RULE_WEIGHT = 2.0      # weight of one hand-written rule hit

# --- Borderline and high-value guard (added before the first accuracy run) --
# A keyword guess that passes the bar by less than BORDERLINE_BAND on either
# score or margin is "Borderline" -> human review.
BORDERLINE_BAND = 1.0
# For merchants at or above this expected TPV, ANY diagnosis that rests only on
# keyword rules (no reviewer code, no identical coded note) goes to human
# review, and their review is marked High priority. 20 lakh = top ~quarter of
# the data pack's non-activated merchants, ~61% of the TPV at stake.
HIGH_TPV_LAKH = 20.0

# --- Honest accuracy test ---------------------------------------------------
SPLIT_SEED = 42              # fixed before any rule was written

# --- Messages ---------------------------------------------------------------
RESUME_LINK_TOKEN = "[resume link]"   # filled in by the sending system
LIBRARY_LANGUAGES = ["English", "Hinglish", "Hindi", "Tamil", "Kannada", "Marathi"]
FALLBACK_LANGUAGE = "English"
