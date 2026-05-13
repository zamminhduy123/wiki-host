"""
agents/librarian.py — The ingest pipeline agent.
"""

from __future__ import annotations

import logging

from config import settings
from llm import LLMProvider
from models import LibrarianOutput, RoutingDecision

logger = logging.getLogger(__name__)

# ─── Call 1: Routing & Selection ─────────────────────────────────────────────

_ROUTING_SYSTEM_PROMPT = """\
You are the Router component of a personal knowledge management system.

Your job is to analyse an incoming raw message/document and the current wiki
index, then decide which existing wiki files need to be fetched and potentially
updated, and whether any entirely new wiki files should be created.

CRITICAL RULES (read the SCHEMA for full details):
- Always include "wiki/index.md" in files_to_fetch.
- Source summaries go in wiki/sources/<slug>.md
- Entity pages go in wiki/entities/<name>.md
- Concept pages go in wiki/concepts/<name>.md
- Return concise, repo-relative paths.
- Keep "reasoning" to one or two sentences.
"""

def route_and_select(
    raw_text: str,
    index_content: str,
    schema_content: str,
    provider: LLMProvider,
) -> RoutingDecision:
    prompt = f"""\
{_ROUTING_SYSTEM_PROMPT}

---
## MISSION CRITICAL
- If this is a new source/document, return its name in 'new_files' AND 'files_to_fetch'.
- Always include 'wiki/index.md'.

---
## wiki/SCHEMA.md
{schema_content}

---
## current index.md
{index_content}

---
## New user info
{raw_text}

---
Respond with a valid JSON.
"""
    logger.info("Librarian Call 1 (routing) — sending %d chars.", len(prompt))
    decision = provider.generate(prompt, RoutingDecision)
    
    # ── Path Correction ───────────────────────────────────────────────
    corrected_fetch = []
    for p in decision.files_to_fetch:
        corrected_fetch.append(f"wiki/{p.lstrip('wiki/').lstrip('/')}")
    decision.files_to_fetch = corrected_fetch

    logger.info(
        "Routing decision — fetch: %s | new: %s",
        decision.files_to_fetch,
        decision.new_files,
    )
    return decision


# ─── Call 2: Librarian Compilation ───────────────────────────────────────────

_LIBRARIAN_SYSTEM_PROMPT = """\
You are the Librarian — a meticulous, elegant knowledge curator.

You MUST follow wiki/SCHEMA.md precisely. Key rules:
1. Create wiki/sources/<slug>.md for every new ingested document.
2. Update or create wiki/entities/<name>.md for every person/org/project.
3. Update or create wiki/concepts/<name>.md for key ideas or methods.
4. Update wiki/index.md — STRICT table format, exact paths, no hallucinations.
5. Generate a log_entry: '## [YYYY-MM-DD] ingest | <Title>\\n<1-2 sentences>'.
6. Return ONLY files that actually changed.
"""

def compile_updates(
    raw_text: str,
    index_content: str,
    schema_content: str,
    fetched_files: dict,
    provider: LLMProvider,
) -> LibrarianOutput:
    files_section_parts: list = []
    for path, content in fetched_files.items():
        files_section_parts.append(f"### {path}\n```markdown\n{content}\n```")
    files_section = (
        "\n\n".join(files_section_parts)
        if files_section_parts
        else "_No existing files fetched._"
    )

    prompt = f"""\
{_LIBRARIAN_SYSTEM_PROMPT}

---
## MISSION CRITICAL CONVENTIONS
- ALL files MUST be in the wiki/ folder.
- New sources MUST go in wiki/sources/<slug>.md
- New entities MUST go in wiki/entities/<slug>.md
- You MUST update wiki/index.md (headers: | File | Summary |)
- You MUST provide a log_entry (## [YYYY-MM-DD] ingest | Title\\nSummary)

---
## wiki/SCHEMA.md (The LAW)
{schema_content}

---
## Current wiki/index.md
```markdown
{index_content}
```

---
## Existing files to fetch & update
{files_section}

---
## NEW INFORMATION TO INTEGRATE
{raw_text}

---
Respond with a valid JSON object.
"""
    logger.info("Librarian Call 2 (compilation) — sending %d chars.", len(prompt))
    output = provider.generate(prompt, LibrarianOutput)

    # ── Post-processing Safety Check: Force Paths ─────────────────────
    # Sometimes local models forget the 'wiki/' prefix even when told twice.
    for f in output.updated_files:
        if not f.filename.startswith("wiki/"):
            original = f.filename
            f.filename = f"wiki/{f.filename.lstrip('/')}"
            logger.warning("Librarian Hallucination: forced path '%s' -> '%s'", original, f.filename)

        # Force sources/ subdir if it's a new file but not in a subdir
        if "/" not in f.filename.replace("wiki/", "") and f.filename != settings.index_path:
             f.filename = f.filename.replace("wiki/", "wiki/sources/")
             logger.warning("Librarian Hallucination: forced into sources/ -> '%s'", f.filename)

    logger.info(
        "Librarian output — %d file(s) to write: %s",
        len(output.updated_files),
        [f.filename for f in output.updated_files],
    )
    return output
