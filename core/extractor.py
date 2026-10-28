#Actionableitems , decision , questions 

import os

from langchain_mistralai import ChatMistralAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import RunnablePassthrough, RunnableLambda
from langchain_text_splitters import RecursiveCharacterTextSplitter

# A full, unchunked transcript used to go straight into one LLM call per
# extraction type (action items / decisions / questions). For a long
# meeting that risks silently exceeding the model's context window —
# there was no check or fallback at all. This threshold + splitter
# mirrors the same chunking approach summarizer.py already uses for the
# same reason, just sized for extraction rather than summarization.
#
# Most real meeting transcripts (a few thousand words) stay comfortably
# under this, so the common case still makes exactly one LLM call with
# the exact same prompt/transcript it always did — this only changes
# behavior once a transcript is actually long enough to be a risk.
MAX_TRANSCRIPT_CHARS_PER_CALL = int(os.getenv("NOTIV_EXTRACT_MAX_CHARS", "12000"))
EXTRACT_CHUNK_OVERLAP = 500

ACTION_ITEMS_PROMPT = (
    "You are an expert meeting analyst. From the meeting transcript, "
    "extract all action items. For each provide:\n"
    "- Task description\n"
    "- Owner (who is responsible)\n"
    "- Deadline (if mentioned, else write 'Not specified')\n\n"
    "Format as a numbered list. If none found say 'No action items found.'"
)

KEY_DECISIONS_PROMPT = (
    "You are an expert meeting analyst. From the meeting transcript, "
    "extract all key decisions made. Format as a numbered list. "
    "If none found say 'No key decisions found.'"
)

QUESTIONS_PROMPT = (
    "From the meeting transcript, extract all unresolved questions "
    "or topics needing follow-up. Format as a numbered list. "
    "If none found say 'No open questions found.'"
)


def get_llm():
    return ChatMistralAI(model = "mistral-small-latest", mistral_api_key = os.getenv("MISTRAL_API_KEY"),temperature=0.2)


def build_chain(system_prompt : str):
    llm = get_llm()
    return (
        RunnablePassthrough() | RunnableLambda(lambda x : {"text" : x}) |ChatPromptTemplate.from_messages([
        ("system", system_prompt),
        ("human","{text}"),
    ]) | llm |StrOutputParser()
    )


def _chunk_transcript_for_extraction(transcript: str) -> list:
    """Returns [transcript] unchanged if it's within the safe per-call
    size; otherwise splits it so no single LLM call ever receives an
    unbounded transcript."""
    if len(transcript) <= MAX_TRANSCRIPT_CHARS_PER_CALL:
        return [transcript]

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=MAX_TRANSCRIPT_CHARS_PER_CALL,
        chunk_overlap=EXTRACT_CHUNK_OVERLAP,
    )
    return splitter.split_text(transcript)


def _run_extraction(system_prompt: str, transcript: str) -> str:
    chain = build_chain(system_prompt)
    parts = _chunk_transcript_for_extraction(transcript)

    if len(parts) == 1:
        # Identical to the old behavior: one call, the whole transcript.
        return chain.invoke(parts[0])

    # Long transcript: extract from each part independently and label
    # them, rather than risk sending the whole thing in one call. This
    # is deliberately simple (no LLM-based re-merge/re-numbering step,
    # which would add another full LLM call per extraction type) — the
    # per-part outputs are still readable and nothing is silently
    # dropped or truncated.
    results = []
    for i, part in enumerate(parts, start=1):
        result = chain.invoke(part)
        results.append(f"— Part {i}/{len(parts)} —\n{result}")
    return "\n\n".join(results)


def extract_action_items(transcript:str)->str:
    return _run_extraction(ACTION_ITEMS_PROMPT, transcript)


def extract_key_decisions(transcript: str) -> str:
    return _run_extraction(KEY_DECISIONS_PROMPT, transcript)


def extract_questions(transcript: str) -> str:
    return _run_extraction(QUESTIONS_PROMPT, transcript)


# ══════════════════════════════════════════════════════════════════════
# Phase 2B — one bounded call instead of three
# ══════════════════════════════════════════════════════════════════════
# job_queue.py used to call extract_action_items / extract_key_decisions /
# extract_questions separately — three full-transcript LLM calls per
# meeting. extract_findings() below does the same extraction in ONE
# call (still bounded by the same MAX_TRANSCRIPT_CHARS_PER_CALL / part
# splitting as the individual functions above, via the same
# _run_extraction helper), using one prompt that asks for all three
# sections at once.
#
# The three individual functions above are left exactly as they were —
# still used by main.py's CLI flow, still covered by their own tests —
# extract_findings() is an additional entry point, not a replacement of
# those functions themselves.
FINDINGS_PROMPT = (
    "You are an expert meeting analyst. From the meeting transcript, extract ALL of the "
    "following in a single pass, using EXACTLY these three Markdown headers, in this "
    "order, each on its own line:\n\n"
    "## Action Items\n"
    "## Decisions\n"
    "## Open Questions\n\n"
    "Under '## Action Items', for each item give:\n"
    "- Task description\n"
    "- Owner: name a specific person ONLY if the transcript explicitly assigns the task "
    "to them by name. Otherwise write 'Owner: Not specified' — never guess based on who "
    "spoke most or on an assumed role.\n"
    "- Deadline: include one ONLY if an explicit date, day, or time is stated for this "
    "task. Otherwise write 'Deadline: Not specified'.\n"
    "If none found, write 'No action items found.' under that header.\n\n"
    "Under '## Decisions', list key decisions actually made — something the transcript "
    "shows the group agreed to or committed to, not a proposal or something merely "
    "discussed. If none found, write 'No key decisions found.' under that header.\n\n"
    "Under '## Open Questions', list unresolved questions or topics needing follow-up. "
    "If none found, write 'No open questions found.' under that header.\n\n"
    "Grounding rules — follow these strictly:\n"
    "- Only report something explicitly supported by the transcript text given to you. "
    "Do not guess, infer beyond what's stated, or add outside knowledge.\n"
    "- Some transcript lines may start with a bracketed timestamp like [12:34] or "
    "[1:02:15]. You may keep that exact timestamp next to an item it clearly belongs to. "
    "NEVER invent, estimate, or reformat a timestamp that was not shown to you, and never "
    "attach one to an item that has none.\n"
    "Format each section as a numbered or bulleted list under its header."
)

_FINDINGS_HEADERS = ("Action Items", "Decisions", "Open Questions")
_FINDINGS_DEFAULTS = {
    "Action Items": "No action items found.",
    "Decisions": "No key decisions found.",
    "Open Questions": "No open questions found.",
}


def extract_findings(transcript: str) -> str:
    """One bounded pass producing Action Items + Decisions + Open
    Questions together (see FINDINGS_PROMPT). Uses the exact same
    bounding/chunking as the individual extract_* functions above
    (_run_extraction) — a normal-length transcript still makes exactly
    one LLM call; only a transcript over MAX_TRANSCRIPT_CHARS_PER_CALL
    is split into multiple calls, same threshold and overlap as before.
    `transcript` may be plain text or the `[MM:SS] ...` timestamped
    rendering built by build_timestamped_transcript() below — either
    way this function just passes it straight to the LLM.
    """
    return _run_extraction(FINDINGS_PROMPT, transcript)


def get_findings_section(structured_findings: str, header: str) -> str:
    """Pulls one section's body out of extract_findings()'s output —
    pure text parsing, no LLM call. Handles both the normal single-call
    output (each header appears once) and the long-transcript, multi-part
    output from _run_extraction (each '— Part i/N —' block repeats all
    three headers) by collecting every non-empty occurrence of the
    header and joining them in order. Returns a plain default ("No X
    found.") if the header can't be located at all, rather than raising.
    """
    lines = structured_findings.splitlines()
    bodies = []
    i = 0
    while i < len(lines):
        stripped = lines[i].strip().lstrip("#").strip()
        if stripped.lower() == header.lower():
            i += 1
            body_lines = []
            while i < len(lines) and not lines[i].strip().startswith("#") \
                    and not lines[i].strip().startswith("— Part"):
                body_lines.append(lines[i])
                i += 1
            body = "\n".join(body_lines).strip()
            if body:
                bodies.append(body)
            continue
        i += 1
    if not bodies:
        return _FINDINGS_DEFAULTS.get(header, "")
    return "\n\n".join(bodies)


def _format_timestamp(seconds) -> str:
    """`[MM:SS]` under an hour, `[H:MM:SS]` beyond that."""
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"[{h}:{m:02d}:{s:02d}]" if h else f"[{m:02d}:{s:02d}]"


def build_timestamped_transcript(transcript_result) -> str:
    """Renders a transcriber.Transcript's real segment timestamps as
    `[MM:SS] text` lines so extract_findings() can cite a genuine
    timestamp next to an item. Falls back to the plain transcript text
    when there are no segments, or none with a known start time —
    never fabricates timing that isn't there.

    Accepts either a transcriber.Transcript (with .text / .segments) or
    a plain str, so callers don't need to care which
    transcribe_all_with_timestamps() gave them.
    """
    segments = getattr(transcript_result, "segments", None)
    text = getattr(transcript_result, "text", transcript_result)
    if not segments:
        return text
    usable = [s for s in segments if s.start is not None]
    if not usable:
        return text
    return "\n".join(f"{_format_timestamp(seg.start)} {seg.text.strip()}" for seg in usable)
