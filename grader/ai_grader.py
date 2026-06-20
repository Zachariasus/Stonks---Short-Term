"""
grader/ai_grader.py
===================
Claude API narrative grader — the second half of the grader (Phase 7).

WHAT THIS DOES
    Takes the structured analysis report (from analysis_pipeline.py) and hands it
    to the Claude API, which returns a letter grade (A–D) plus a narrative: a
    one-line verdict, the bull case, the bear case, key risks, and a suggested
    action. The numbers say WHAT is true; the AI grader says what it MEANS.

GRACEFUL DEGRADATION
    If ANTHROPIC_API_KEY isn't set (still a placeholder), call_claude_grader
    returns a clean stub dict so the rest of the system keeps working — no crash,
    no fake grade.
"""

import contextlib
import io
import json

# Imports. PYTHONPATH=<project root> makes the first block work; the fallback
# inserts the project root so the file runs standalone too.
try:
    from data.config import ANTHROPIC_API_KEY
    from grader.analysis_pipeline import format_analysis_report, run_full_analysis
except ImportError:  # pragma: no cover
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from data.config import ANTHROPIC_API_KEY
    from grader.analysis_pipeline import format_analysis_report, run_full_analysis

# Claude model for the grade. Opus is the strong default here: grading is a
# nuanced synthesis task (reconciling conflicting engine signals into one letter
# grade), and each grade is a single on-demand call, so the cost is trivial.
# Swap to "claude-haiku-4-5" if you'd rather have cheaper, faster grades.
GRADER_MODEL = "claude-opus-4-8"

# JSON Schema for the grade. Passed as a structured-output format so the API
# GUARANTEES a valid, parseable object back — no reliance on the model
# remembering to avoid markdown fences, no fragile regex scraping.
GRADE_SCHEMA = {
    "type": "object",
    "properties": {
        "grade": {"type": "string", "enum": ["A", "B", "C", "D"]},
        "one_line_verdict": {"type": "string"},
        "bull_case": {"type": "string"},
        "bear_case": {"type": "string"},
        "key_risks": {"type": "array", "items": {"type": "string"}},
        "suggested_action": {"type": "string"},
    },
    "required": [
        "grade",
        "one_line_verdict",
        "bull_case",
        "bear_case",
        "key_risks",
        "suggested_action",
    ],
    "additionalProperties": False,
}

# The grading criteria — kept here so every call uses the exact same rubric.
GRADE_RUBRIC = """A — High conviction. 3–4 engines aligned, score >= 70, confirmed trend,
    fundamental tailwind, favorable rotation. Actionable — size appropriately
    and plan the entry.

B — Solid setup with caveats. Score 55–69, most signals aligned but one engine
    weak or missing. Worth watching, enter on confirmation (breakout or pullback
    to 50-day), size conservatively.

C — Mixed or early signals. Score 40–54, only 2 engines aligned. Thesis is
    forming but not confirmed — watchlist only, no action yet.

D — Weak or contradicting signals. Score < 40, or a strong engine actively
    disagrees with the direction. Pass entirely or flag as a potential short
    candidate (if Stage 4 + weak RS)."""


def build_grading_prompt(analysis_packet: dict, formatted_report: str) -> str:
    """Assemble the full prompt string sent to Claude.

    Structure: analyst role + rubric + the formatted report + strict JSON
    output instructions (JSON keeps the response reliably parseable).
    """
    return f"""You are an intermediate-term (4–6 month hold) equity analyst. You are given a
complete, pre-computed quantitative analysis of ONE stock produced by a
multi-engine trading system. Synthesize it into a letter grade with concise
reasoning: connect the signals into a coherent story, surface the tension
between any conflicting signals, and be honest about the risks. Judge strictly
against the rubric — do not invent data that isn't in the report.

GRADING RUBRIC
{GRADE_RUBRIC}

ANALYSIS REPORT
{formatted_report}

INSTRUCTIONS
Return ONLY a JSON object (no markdown fences, no prose outside the JSON) with
exactly these keys:
  "grade": one of "A", "B", "C", "D"
  "one_line_verdict": a verdict of 15 words or fewer
  "bull_case": 2-3 sentences making the case FOR this setup
  "bear_case": 2-3 sentences making the case AGAINST it
  "key_risks": a JSON array of 2-3 short risk strings
  "suggested_action": one sentence on what to do and when
"""


def _key_is_set() -> bool:
    """True only if a real Anthropic key is configured (not missing/placeholder)."""
    return bool(ANTHROPIC_API_KEY) and ANTHROPIC_API_KEY != "your_key_here"


def _parse_json(text: str):
    """Parse a JSON object from the model's text, tolerating stray fences/prose."""
    # 1. Direct parse.
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        pass
    # 2. Strip a ```json ... ``` fence if present.
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("```", 2)[1] if "```" in cleaned else cleaned
        cleaned = cleaned.removeprefix("json").strip()
        try:
            return json.loads(cleaned)
        except (json.JSONDecodeError, TypeError):
            pass
    # 3. Last resort: grab the outermost { ... } and parse that.
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except (json.JSONDecodeError, TypeError):
            pass
    return None


def call_claude_grader(prompt: str) -> dict:
    """Send the prompt to Claude and parse the JSON grade. Degrades gracefully.

    Assumes a key is configured (grade_stock only calls this when _key_is_set()).
    On any failure it returns an api_error dict so the caller can fall back to the
    rules-based grade.
    """
    key = ANTHROPIC_API_KEY
    if not _key_is_set():
        return {"grade": "?", "stub": False, "api_error": True,
                "one_line_verdict": "No API key configured"}

    try:
        import anthropic  # lazy import — only needed when a key is configured

        client = anthropic.Anthropic(api_key=key)
        message = client.messages.create(
            model=GRADER_MODEL,
            max_tokens=4096,  # headroom for adaptive thinking + the JSON grade
            thinking={"type": "adaptive"},  # let it reason through conflicting signals
            output_config={"format": {"type": "json_schema", "schema": GRADE_SCHEMA}},
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(
            block.text for block in message.content
            if getattr(block, "type", None) == "text"
        ).strip()
    except Exception as err:  # noqa: BLE001 - network / API / auth failures
        print(f"⚠️  call_claude_grader: Claude API call failed — {err}")
        return {
            "grade": "?",
            "one_line_verdict": "AI grading unavailable (API error)",
            "bull_case": str(err),
            "bear_case": "—",
            "key_risks": [],
            "suggested_action": "—",
            "stub": False,
            "api_error": True,
        }

    parsed = _parse_json(text)
    if parsed is None:
        # Couldn't parse JSON — return the raw text so nothing is silently lost.
        return {
            "grade": "?",
            "one_line_verdict": "Could not parse AI response",
            "bull_case": text[:500],
            "bear_case": "—",
            "key_risks": [],
            "suggested_action": "—",
            "stub": False,
            "parse_error": True,
        }

    parsed["stub"] = False
    parsed["source"] = "ai"
    return parsed


# ---------------------------------------------------------------------------
# Rules-based grade — the keyless DEFAULT.
#
# The letter grade is a transparent function of the confluence score + how many
# engines are firing — exactly the rubric the AI is asked to follow. The
# narrative (verdict, bull/bear, key risks, action) is assembled deterministically
# from the same engine signals already computed for the stock. So the grader is
# fully functional with NO API key and NO cost. Configure a real ANTHROPIC_API_KEY
# and grade_stock automatically upgrades to the AI-written version instead.
# ---------------------------------------------------------------------------
def _letter_grade(total_score, engines_firing) -> str:
    """A/B/C/D straight from the confluence rubric (see GRADE_RUBRIC)."""
    if total_score is None:
        return "?"
    if total_score >= 70 and (engines_firing or 0) >= 3:
        return "A"
    if total_score >= 55:
        return "B"
    if total_score >= 40:
        return "C"
    return "D"


def _join_phrases(phrases, limit=3) -> str:
    """Join up to `limit` non-empty phrases into a natural ', ' / ', and ' list."""
    items = [p for p in phrases if p][:limit]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + ", and " + items[-1]


def rule_based_grade(packet: dict) -> dict:
    """Deterministic grade + narrative from the analysis packet (no API call)."""
    s1 = packet.get("section_1_identity") or {}
    s2 = packet.get("section_2_trend") or {}
    s3 = packet.get("section_3_fundamental") or {}
    s4 = packet.get("section_4_topdown") or {}
    s5 = packet.get("section_5_valuation") or {}
    s6 = packet.get("section_6_confluence") or {}

    total = s6.get("total_score")
    firing = s6.get("engines_firing")
    direction = s6.get("direction") or "Long"
    regime = s6.get("market_regime")
    is_short = direction == "Short"
    grade = _letter_grade(total, firing)

    # Signals (each may be None — handled below).
    stage = s2.get("stage")
    stage_label = s2.get("stage_label") or (f"Stage {stage}" if stage else "stage n/a")
    rs = (s2.get("rs_vs_spy") or {}).get("label")
    vol = (s2.get("volume_profile") or {}).get("label")
    rev = s3.get("revision_direction")
    op_margin = s3.get("operating_margin_direction")
    beat = s3.get("beat_raise_pattern_label")
    rot = s4.get("sector_rotation_label")
    val_room = s5.get("valuation_room")
    upside = s5.get("upside_pct")
    es = s2.get("entry_signals") or {}
    days = s3.get("days_to_earnings")
    earn_date = s3.get("next_earnings_date")
    ticker = s1.get("ticker") or "This name"

    supporting = []  # the case FOR the setup (in its direction)
    opposing = []    # the case AGAINST it

    if is_short:
        if stage == 4:
            supporting.append("an established Stage 4 downtrend")
        if rs in ("Weak Laggard", "Laggard"):
            supporting.append("deeply lagging relative strength")
        if vol in ("Distribution", "Mild Distribution"):
            supporting.append("distribution showing in the volume")
        if rev == "Falling":
            supporting.append("a falling estimate-revision cycle")
        if op_margin in ("Compressing", "Consistently Compressing"):
            supporting.append("compressing operating margins")
        if beat in ("Chronic Misser", "Mixed"):
            supporting.append("a weak earnings track record")
        if rot == "Lagging":
            supporting.append("a sector the rotation has abandoned")
        if regime == "Risk-Off":
            supporting.append("a risk-off tape that favors shorts")
        if val_room == "Limited — already extended":
            supporting.append("a still-rich multiple with room to compress")
        if es.get("breakdown") or es.get("failed_rally"):
            supporting.append("a short entry triggering now")

        if regime == "Risk-On":
            opposing.append("a strong-bull tape — shorting against the market's upward drift")
        if stage != 4:
            opposing.append(f"no clean Stage 4 downtrend yet ({stage_label})")
        if rs not in ("Weak Laggard", "Laggard"):
            opposing.append("relative strength that isn't weak enough")
        if rot == "Leading":
            opposing.append("a sector that is actually leading")
        if not (es.get("breakdown") or es.get("failed_rally")):
            opposing.append("no short entry signal yet")
        opposing.append("the short's unbounded-loss asymmetry, carry cost, and squeeze risk")
    else:
        if stage == 2:
            supporting.append("an established Stage 2 uptrend")
        if rs in ("Strong Leader", "Leader"):
            supporting.append("relative-strength leadership versus the market")
        if vol in ("Accumulation", "Mild Accumulation"):
            supporting.append("volume under accumulation")
        if rev == "Rising":
            supporting.append("a rising estimate-revision cycle")
        if op_margin in ("Expanding", "Consistently Expanding"):
            supporting.append("expanding operating margins")
        if beat in ("Beat-and-Raise Cycle", "Consistent Beater"):
            supporting.append("a beat-and-raise track record")
        if rot == "Leading":
            supporting.append("a sector the rotation favors")
        if regime == "Risk-On":
            supporting.append("a risk-on market backdrop")
        if isinstance(val_room, str) and val_room.startswith(("Yes", "Partial")):
            supporting.append("valuation room to re-rate")
        if isinstance(upside, (int, float)) and upside > 0:
            supporting.append(f"about {upside:.0f}% upside to the target")
        if es.get("breakout") or es.get("pullback"):
            supporting.append("a clean long entry triggering now")

        if stage != 2:
            opposing.append(f"not yet a Stage 2 trend ({stage_label})")
        if rs in ("Laggard", "Weak Laggard"):
            opposing.append("lagging relative strength")
        if vol in ("Distribution", "Mild Distribution"):
            opposing.append("distribution in the volume")
        if rev == "Falling":
            opposing.append("a falling estimate-revision cycle")
        if op_margin in ("Compressing", "Consistently Compressing"):
            opposing.append("compressing margins")
        if beat == "Chronic Misser":
            opposing.append("a history of earnings misses")
        if rot == "Lagging":
            opposing.append("an out-of-favor sector")
        if regime == "Risk-Off":
            opposing.append("a risk-off market working against the trend")
        if val_room == "Limited — already extended":
            opposing.append("an already-extended valuation")
        if isinstance(upside, (int, float)) and upside <= 0:
            opposing.append("a valuation target below the current price")
        if not (es.get("breakout") or es.get("pullback")):
            opposing.append("no clean entry signal yet")

    bull = _join_phrases(supporting)
    bear = _join_phrases(opposing)
    bull_case = f"{ticker} has {bull}." if bull else "No strongly supportive signals stand out right now."
    bear_case = f"The case against: {bear}." if bear else "No major red flags stand out."

    # Key risks (2–3).
    risks = []
    # Only an UPCOMING report (0–45 days out) is a gap risk; a negative value is a
    # stale past date, so skip it.
    if isinstance(days, (int, float)) and 0 <= days <= 45:
        risks.append(
            f"Earnings ~{int(days)}d out{f' ({earn_date})' if earn_date else ''} — a two-sided gap through the hold."
        )
    if is_short and regime == "Risk-On":
        risks.append("Shorting in a strong-bull tape — market drift and squeeze risk work against you; size small, stop above.")
    if not is_short and regime == "Risk-Off":
        risks.append("Buying into a risk-off market — the trend filter is against the position.")
    if isinstance(firing, int) and firing < 2:
        risks.append(f"Only {firing}/4 engines firing — thin confirmation.")
    if is_short and not any("asymmetry" in r for r in risks):
        risks.append("Short asymmetry: unbounded loss, carry, and squeeze risk — honor the stop without exception.")
    if not risks:
        risks.append("Standard market risk; re-test the thesis at each earnings print.")
    risks = risks[:3]

    dir_word = "short" if is_short else "long"
    verdict = {
        "A": f"High-conviction {dir_word} — signals aligned (score {total}/100).",
        "B": f"Solid {dir_word} with caveats — wait for confirmation (score {total}/100).",
        "C": f"Mixed, early {dir_word} signals — watchlist only (score {total}/100).",
        "D": f"Weak {dir_word} setup — pass (score {total}/100).",
        "?": "Insufficient data to grade.",
    }.get(grade, "Insufficient data to grade.")

    if grade == "A":
        action = (
            "Actionable but defensive — size small, hard stop above, enter on a breakdown or failed rally."
            if is_short else
            "Actionable — size per the card and enter on a breakout or a pullback to the rising 50-day."
        )
    elif grade == "B":
        action = "Enter only on a clean confirmation signal; size conservatively. Worth watching."
    elif grade == "C":
        action = "Watchlist only — the thesis is forming but not confirmed. No action yet."
    elif grade == "D":
        action = "Pass — or keep on the short watchlist for a weaker tape." if is_short else "Pass."
    else:
        action = "—"

    return {
        "grade": grade,
        "one_line_verdict": verdict,
        "bull_case": bull_case,
        "bear_case": bear_case,
        "key_risks": risks,
        "suggested_action": action,
        "source": "rules",
        "stub": False,
    }


def grade_stock(ticker: str) -> dict:
    """Run the full pipeline and grade it.

    Default is the keyless RULES-BASED grade. If a real ANTHROPIC_API_KEY is set,
    use the AI grade instead — and fall back to the rules grade if the AI call
    fails or returns something unusable, so a grade always comes back.
    """
    ticker = ticker.strip().upper()
    packet = run_full_analysis(ticker)

    if _key_is_set():
        # format_analysis_report prints AND returns the string; we only want the
        # string here (the report itself is the grade card's job, not this step).
        with contextlib.redirect_stdout(io.StringIO()):
            report = format_analysis_report(packet)
        grade = call_claude_grader(build_grading_prompt(packet, report))
        if grade.get("api_error") or grade.get("parse_error") or grade.get("grade") in (None, "?"):
            grade = rule_based_grade(packet)  # AI unavailable/unusable → deterministic fallback
    else:
        grade = rule_based_grade(packet)

    s1 = packet.get("section_1_identity", {})
    s6 = packet.get("section_6_confluence", {})

    return {
        "ticker": ticker,
        "company_name": s1.get("company_name"),
        "analysis_date": s1.get("analysis_date"),
        "grade": grade.get("grade"),
        "one_line_verdict": grade.get("one_line_verdict"),
        "bull_case": grade.get("bull_case"),
        "bear_case": grade.get("bear_case"),
        "key_risks": grade.get("key_risks", []),
        "suggested_action": grade.get("suggested_action"),
        "confluence_score": s6.get("total_score"),
        "confidence_label": s6.get("confidence_label"),
        "direction": s6.get("direction"),
        "market_regime": s6.get("market_regime"),
        # Per-engine point breakdown (powers the Grader UI's E1–E4 bars).
        "engine_1_pts": s6.get("engine_1_pts"),
        "engine_2_pts": s6.get("engine_2_pts"),
        "engine_3_pts": s6.get("engine_3_pts"),
        "engine_4_pts": s6.get("engine_4_pts"),
        "engines_firing": s6.get("engines_firing"),
        # "rules" (keyless default) or "ai" (when a key is configured).
        "grade_source": grade.get("source", "rules"),
        "stub": grade.get("stub", False),
        "parse_error": grade.get("parse_error", False),
        "api_error": grade.get("api_error", False),
    }


def print_grade_card(grade_dict: dict) -> dict:
    """Print a formatted grade card and return the dict."""
    g = grade_dict
    name = g.get("company_name") or "Unknown"
    grade = g.get("grade") or "?"

    # --- Header box ---
    inner_width = 50
    grade_str = f"Grade: {grade}  "
    left = f"  {g.get('ticker')} — {name}"
    space = inner_width - len(left) - len(grade_str)
    if space < 1:  # name too long → truncate
        keep = max(0, len(name) - (1 - space) - 1)
        name = name[:keep] + "…"
        left = f"  {g.get('ticker')} — {name}"
        space = max(1, inner_width - len(left) - len(grade_str))
    header_line = (left + " " * space + grade_str)[:inner_width].ljust(inner_width)

    print("╔" + "═" * inner_width + "╗")
    print("║" + header_line + "║")
    print("╚" + "═" * inner_width + "╝")

    if g.get("one_line_verdict"):
        print(f'"{g["one_line_verdict"]}"')

    print("\nBULL CASE")
    print(g.get("bull_case") or "—")
    print("\nBEAR CASE")
    print(g.get("bear_case") or "—")
    print("\nKEY RISKS")
    risks = g.get("key_risks") or []
    if risks:
        for risk in risks:
            print(f"• {risk}")
    else:
        print("• —")
    print("\nSUGGESTED ACTION")
    print(g.get("suggested_action") or "—")

    print(
        f"\nConfluence: {g.get('confluence_score')}/100  |  "
        f"{g.get('confidence_label')}  |  {g.get('direction')}  |  "
        f"{g.get('analysis_date')}"
    )
    print("─" * 52)

    return g


if __name__ == "__main__":
    result = grade_stock("AAPL")
    print_grade_card(result)
    src = result.get("grade_source")
    if src == "rules":
        print("\nℹ️  Rules-based grade (no API key). Add a real ANTHROPIC_API_KEY to .env for the AI-written version.")
    else:
        print("\n✅ AI grade (ANTHROPIC_API_KEY configured).")
