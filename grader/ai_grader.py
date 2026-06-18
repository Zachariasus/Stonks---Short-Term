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


def _stub_response() -> dict:
    """Returned when no API key is configured — keeps the pipeline working."""
    return {
        "grade": "?",
        "one_line_verdict": "API key not set",
        "bull_case": "Set ANTHROPIC_API_KEY in .env to enable AI grading.",
        "bear_case": "—",
        "key_risks": [],
        "suggested_action": "—",
        "stub": True,
    }


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
    """Send the prompt to Claude and parse the JSON grade. Degrades gracefully."""
    key = ANTHROPIC_API_KEY
    if not key or key == "your_key_here":
        return _stub_response()

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
    return parsed


def grade_stock(ticker: str) -> dict:
    """Run the full pipeline, grade it with Claude, and return the combined result."""
    ticker = ticker.strip().upper()
    packet = run_full_analysis(ticker)

    # format_analysis_report prints AND returns the string; we only want the
    # string here (the report itself is the grade card's job, not this step).
    with contextlib.redirect_stdout(io.StringIO()):
        report = format_analysis_report(packet)

    prompt = build_grading_prompt(packet, report)
    grade = call_claude_grader(prompt)

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
        # Per-engine point breakdown (powers the Grader UI's E1–E4 bars).
        "engine_1_pts": s6.get("engine_1_pts"),
        "engine_2_pts": s6.get("engine_2_pts"),
        "engine_3_pts": s6.get("engine_3_pts"),
        "engine_4_pts": s6.get("engine_4_pts"),
        "engines_firing": s6.get("engines_firing"),
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
    if result.get("stub"):
        print("\nℹ️  Stub response shown — add a real ANTHROPIC_API_KEY to .env to enable live AI grading.")
