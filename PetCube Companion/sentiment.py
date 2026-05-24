"""
sentiment.py
Analizza una stringa (titolo evento, prima frase) e produce:
  - sentiment ∈ {positive, neutral, negative, question}
  - urgency ∈ {low, high}
  - category (combinato) → NotifCategory

spaCy italiano non ha un sentiment analyzer integrato come VADER (che è solo inglese),
quindi usiamo un approccio ibrido:
  1. spaCy per tokenizzazione, lemmatizzazione, POS tagging
  2. Dizionari di parole positive/negative italiane
  3. Detection di domande via punto interrogativo o POS-pattern (verbo iniziale, "?", "come/cosa/dove/quando/perché/chi")
  4. Detection urgenza via keyword list

Per inglese funziona altrettanto bene grazie alle keyword.
"""
import re
import logging
from notification_packet import NotifCategory

logger = logging.getLogger(__name__)

# ─── Dizionari sentimentali ────────────────────────────────────
# Keyword case-insensitive. Aggiungere lemmi italiani e inglesi.

POSITIVE_KEYWORDS = {
    # Italiano
    "grazie", "ottimo", "perfetto", "bravo", "complimenti", "felice", "bene",
    "successo", "vittoria", "fantastico", "splendido", "eccellente", "buon",
    "grande", "magnifico", "approvato", "completato", "finito",
    # Inglese
    "great", "thanks", "thank", "good", "awesome", "amazing", "excellent",
    "well", "done", "perfect", "love", "happy", "congrats", "kudos", "nice",
    "approved", "merged", "fixed", "resolved", "completed", "success",
}

NEGATIVE_KEYWORDS = {
    # Italiano
    "errore", "problema", "rotto", "fallito", "down", "crash", "disastro",
    "male", "sbagliato", "non", "mai", "pessimo", "schifoso", "orribile",
    "annullato", "respinto", "rifiutato", "blocca", "blocco", "bug",
    # Inglese
    "error", "issue", "broken", "failed", "fail", "crash", "down", "outage",
    "wrong", "bad", "terrible", "horrible", "rejected", "blocked", "cancelled",
    "bug", "problem", "fix", "stuck", "regression",
}

URGENCY_KEYWORDS = {
    # Italiano
    "urgente", "subito", "ora", "immediatamente", "scadenza", "entro", "veloce",
    "rapido", "presto", "stat", "oggi", "aiuto", "aiutare", "salvare", "salvami",
    "rotto", "crashato", "bloccato",
    # Inglese
    "urgent", "asap", "now", "immediately", "deadline", "today", "soon",
    "quick", "fast", "rush", "critical", "emergency", "help", "blocker",
    "broken", "crashed", "stuck",
}

# Indicatori di domanda
QUESTION_STARTERS_IT = {"come", "cosa", "dove", "quando", "perché", "chi", "quale", "quanto"}
QUESTION_STARTERS_EN = {"how", "what", "where", "when", "why", "who", "which",
                        "can", "could", "would", "should", "is", "are", "do", "does"}


# ─── spaCy loader (lazy) ──────────────────────────────────────
_nlp = None

def get_nlp():
    """Carica spaCy IT al primo uso. Restituisce None se non disponibile."""
    global _nlp
    if _nlp is not None:
        return _nlp
    try:
        import spacy
        try:
            _nlp = spacy.load("it_core_news_sm")
            logger.info("spaCy 'it_core_news_sm' caricato.")
        except OSError:
            logger.warning("spaCy 'it_core_news_sm' non installato. Eseguire: python -m spacy download it_core_news_sm")
            logger.warning("Procedo con dizionario keyword puro (senza lemmatizzazione).")
            _nlp = False  # marker: non riprovare
    except ImportError:
        logger.warning("spaCy non installato. Procedo con dizionario keyword puro.")
        _nlp = False
    return _nlp if _nlp else None


# ─── Analisi ───────────────────────────────────────────────────
def tokenize(text: str) -> list[str]:
    """
    Tokenizza un testo. Usa spaCy se disponibile (lemma + token originale + lowercase),
    altrimenti split naive sui non-alfanumerici.

    Strategia cross-lingua: il modello italiano lemmatizza male le parole inglesi
    (es. 'help' → 'elpvere'). Per essere robusti, ritorniamo SIA il lemma SIA il
    token originale, così le keyword italiane matchano sul lemma e quelle inglesi
    sul token originale.
    """
    nlp = get_nlp()
    if nlp:
        doc = nlp(text)
        result = []
        for tok in doc:
            if tok.is_punct or tok.is_space:
                continue
            # Lowercased original token
            result.append(tok.text.lower())
            # Lemma (se diverso dal token, evita duplicati)
            lemma_lower = tok.lemma_.lower()
            if lemma_lower != tok.text.lower():
                result.append(lemma_lower)
        return result
    # Fallback semplice
    return [t.lower() for t in re.findall(r"\w+", text)]


def detect_sentiment(tokens: list[str], original_text: str) -> str:
    """
    Ritorna 'positive', 'neutral', 'negative', o 'question'.
    Question detection prima (ha priorità) — se la stringa è chiaramente una domanda,
    sentiment positivo/negativo viene comunque collassato in 'question'.
    """
    # Question check: punto interrogativo o starter
    if "?" in original_text:
        return "question"
    # I primi 1-2 elementi sono token+lemma della prima parola
    if tokens and (tokens[0] in (QUESTION_STARTERS_IT | QUESTION_STARTERS_EN) or
                   (len(tokens) > 1 and tokens[1] in (QUESTION_STARTERS_IT | QUESTION_STARTERS_EN))):
        return "question"

    # Bigrammi "non + verbo" che indicano malfunzionamento (auto-negativi)
    text_lower = original_text.lower()
    NEGATIVE_BIGRAMS = [
        "non funziona", "non va", "non riesco", "non parte", "non si",
        "doesn't work", "won't start", "can't", "isn't working", "not working",
    ]
    if any(bg in text_lower for bg in NEGATIVE_BIGRAMS):
        return "negative"

    pos_score = sum(1 for t in tokens if t in POSITIVE_KEYWORDS)
    neg_score = sum(1 for t in tokens if t in NEGATIVE_KEYWORDS)

    # Gestione negazione contestuale: inverte i punteggi solo se un token di
    # negazione forte ("non", "not", "never"…) è adiacente a una keyword scored.
    # "no" da solo è escluso perché è ambiguo in inglese ("no problem" = positivo).
    # La finestra è di 3 token: NEG_TOKEN ... KEYWORD (o KEYWORD ... NEG_TOKEN).
    STRONG_NEGATIONS = {"non", "not", "nessuno", "nothing", "never", "né"}
    has_negation = False
    WINDOW = 3
    for i, t in enumerate(tokens):
        if t in STRONG_NEGATIONS:
            window_tokens = tokens[max(0, i - WINDOW): i + WINDOW + 1]
            if any(w in POSITIVE_KEYWORDS or w in NEGATIVE_KEYWORDS for w in window_tokens):
                has_negation = True
                break
    if has_negation:
        pos_score, neg_score = neg_score, pos_score

    if pos_score > neg_score:
        return "positive"
    if neg_score > pos_score:
        return "negative"
    return "neutral"


def detect_urgency(tokens: list[str], original_text: str) -> str:
    """Ritorna 'low' o 'high'."""
    if any(t in URGENCY_KEYWORDS for t in tokens):
        return "high"
    # Punto esclamativo singolo in stringa breve = alta urgenza
    if "!" in original_text and len(original_text) <= 40:
        return "high"
    # Esclamativi multipli = alta urgenza
    if original_text.count("!") >= 2:
        return "high"
    # Tutto MAIUSCOLO (10+ char) = urgenza
    if len(original_text) >= 10 and original_text.upper() == original_text and any(c.isalpha() for c in original_text):
        return "high"
    return "low"


def sentiment_urgency_to_category(sentiment: str, urgency: str) -> NotifCategory:
    """
    Tabella di mapping dal design GDD §16.3.2.
    """
    mapping = {
        ("positive", "low"):  NotifCategory.LODE,
        ("positive", "high"): NotifCategory.OPPORTUNITA,
        ("neutral",  "low"):  NotifCategory.ROUTINE,
        ("neutral",  "high"): NotifCategory.SCADENZA,
        ("negative", "low"):  NotifCategory.CRITICA,
        ("negative", "high"): NotifCategory.CRISI,
        ("question", "low"):  NotifCategory.CURIOSITA,
        ("question", "high"): NotifCategory.AIUTO,
    }
    return mapping.get((sentiment, urgency), NotifCategory.ROUTINE)


def analyze(text: str) -> tuple[str, str, NotifCategory]:
    """
    Analizza un testo e ritorna (sentiment, urgency, category).
    """
    tokens = tokenize(text)
    s = detect_sentiment(tokens, text)
    u = detect_urgency(tokens, text)
    c = sentiment_urgency_to_category(s, u)
    logger.debug(f"analyze({text!r}) → sentiment={s} urgency={u} cat={c.name}")
    return s, u, c


if __name__ == "__main__":
    # Test rapido
    tests = [
        "URGENT: server is down, fix ASAP",
        "Great job on the demo!",
        "Daily standup at 10am",
        "Can you help me with this?",
        "Report due tomorrow EOD",
        "PR review requested",
        "Riunione settimanale alle 15:00",
        "Aiuto! Non funziona più nulla",
        "Complimenti per il lavoro fatto",
    ]
    for t in tests:
        s, u, c = analyze(t)
        print(f"  {t!r:<50} → {s:8s} {u:5s} {c.name}")
