"""Turn a short reviewer note into a set of simple word stems.

Steps: lowercase -> mark negations as NEG -> drop punctuation -> drop filler
words (English and Hinglish) -> crude stemming. Both the catalog text and
the reviewer notes go through the same steps, so word forms line up
("supported" and "support" both become "support").
"""
import re

NEG = "NEG"

# Negation words in English and Hinglish all become the token NEG, so a rule
# can say "category + support + NEG" and match both
# "not supported" and "support nahi kar sakte".
_NEG_PATTERNS = [
    r"\bcan\s*not\b", r"\bcan'?t\b", r"\bcan t\b", r"\bdoes\s*n[o']?t\b", r"\bdoesn'?t\b",
    r"\bdon'?t\b", r"\bdo not\b", r"\bis\s*n'?t\b", r"\bisn'?t\b", r"\bnot\b", r"\bno\b",
    r"\bnahi\b", r"\bnahin\b", r"\bnhi\b", r"\bunable\b", r"\bnever\b",
]

STOPWORDS = {
    # English filler
    "a", "an", "the", "of", "on", "in", "to", "and", "or", "by", "for", "with", "from",
    "is", "are", "was", "were", "be", "been", "this", "that", "it", "its", "as", "at",
    "we", "our", "you", "your", "their", "they", "can", "could", "does", "do", "did",
    "has", "have", "had", "so", "than", "then", "there", "which", "who", "what", "such",
    "example", "eg", "pls", "please", "per", "under", "into", "only", "any", "all",
    "but", "if", "not", "also", "more", "out", "up",
    # Hinglish filler
    "ka", "ki", "ke", "ko", "hai", "hain", "se", "pe", "par", "aur", "ya", "kar",
    "karein", "raha", "rahi", "hua", "hui", "gaya", "ye", "yeh", "woh", "mein", "me",
    "bhi", "toh", "kya", "sakte", "sakta", "hota", "tha", "thi",
}


def stem(word: str) -> str:
    """Very small suffix stripper. Same rules on both sides, so it only has to be consistent."""
    w = word
    if len(w) <= 3:
        return w
    if w.endswith("ies") and len(w) > 4:
        return w[:-3] + "y"
    if w.endswith(("ches", "shes", "sses", "xes")):
        return w[:-2]
    if w.endswith("s") and not w.endswith("ss"):
        w = w[:-1]
    if w.endswith("ing") and len(w) > 5:
        return w[:-3]
    if w.endswith("ed") and len(w) > 4:
        return w[:-2]
    return w


def normalise(text) -> str:
    """Lowercase, mark negation, strip punctuation. Returns a clean string."""
    if text is None:
        return ""
    t = str(text).lower()
    for pat in _NEG_PATTERNS:
        t = re.sub(pat, f" {NEG.lower()}_tok ", t)
    t = re.sub(r"[^\w\s]", " ", t)      # keeps letters/digits in any script
    t = re.sub(r"\s+", " ", t).strip()
    return t


def stems(text) -> set:
    """Set of word stems in the text (filler removed, negation kept as NEG)."""
    out = set()
    for tok in normalise(text).split():
        if tok == "neg_tok":
            out.add(NEG)
            continue
        if tok in STOPWORDS or len(tok) < 2:
            continue
        out.add(stem(tok))
    return out


def template_key(text) -> str:
    """Key used to spot the exact same reviewer sentence reused elsewhere."""
    return normalise(text)
