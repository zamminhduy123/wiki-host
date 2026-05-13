"""
agents/researcher.py — The query pipeline agent.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Callable, Optional

from config import settings
from llm import LLMProvider
from models import QueryRouting, ResearcherAnswer

logger = logging.getLogger(__name__)

# ─── Call 1: Query Routing ──────────────────────────────────────

_QUERY_ROUTING_PROMPT = """\
You are the Navigator of a personal knowledge wiki.

The user has asked a question. Your ONLY job is to decide which wiki files
to open so the answer can be found inside them.

CRITICAL RULES:
- wiki/index.md is a TABLE OF CONTENTS ONLY. It contains one-line summaries,
  NOT actual content. It can NEVER be the source of a real answer.
- You MUST always return at least one file in files_to_read.
- Choose every file whose one-line summary suggests it might contain the answer.
- Also check wiki/analyses/ — past queries may already be filed there.
- If the question is broad or unclear, include ALL wiki files — it is better to
  read too many than too few.
- Return repo-relative paths exactly as listed in the index.
- Keep "reasoning" to one sentence.
- NEVER return an empty files_to_read list.
"""

async def route_query(
    question: str,
    index_content: str,
    provider: LLMProvider,
    on_status: Optional[Callable[[str], None]] = None,
) -> QueryRouting:
    if on_status:
        on_status("Navigating wiki index to find relevant files...")

    prompt = f"""\
{_QUERY_ROUTING_PROMPT}

---
## wiki/index.md (the map of all knowledge)
{index_content}

---
## User's question
{question}

---
Respond with a valid JSON object matching the QueryRouting schema.
"""
    logger.info("Researcher Call 1 (query routing) — %d chars.", len(prompt))
    routing = await asyncio.to_thread(provider.generate, prompt, QueryRouting)
    logger.info("Query routing — will read: %s", routing.files_to_read)
    return routing


# ─── Call 2: Answer Synthesis ─────────────────────────────────────

_RESEARCHER_ANSWER_PROMPT = """\
You are the Researcher — a precise, knowledgeable assistant.

Your job is to extract facts from the provided wiki files to answer the user's
question accurately.

CRITICAL RULES:
- DO NOT talk about the files (e.g., avoid "Based on the files..." or "File X says...").
- Answer the question DIRECTLY as if you already know the information.
- Use ONLY the provided wiki content.
- Cite your sources in the "sources" field with the exact filename and excerpt.
- If the information is missing, use the "confidence" field to say so.
- Use professional, clean markdown formatting.
- NEVER mention "the index" or "the available files". Just give the answer.
"""

async def answer_query(
    question: str,
    fetched_files: dict,
    provider: LLMProvider,
    on_status: Optional[Callable[[str], None]] = None,
) -> ResearcherAnswer:
    if on_status:
        count = len(fetched_files)
        on_status(f"Synthesizing answer from {count} relevant wiki file(s)...")

    if not fetched_files:
        return ResearcherAnswer(
            answer="I couldn't find any specific information in your wiki to answer this.",
            sources=[],
            confidence="low"
        )

    files_section_parts: list = []
    for path, content in fetched_files.items():
        files_section_parts.append(f"### {path}\n```markdown\n{content}\n```")
    files_section = "\n\n".join(files_section_parts)

    prompt = f"""\
{_RESEARCHER_ANSWER_PROMPT}

---
## Relevant Wiki Content
{files_section}

---
## User's Question
{question}

---
Respond with a valid JSON object matching the ResearcherAnswer schema.
"""
    logger.info("Researcher Call 2 (answer) — %d chars.", len(prompt))
    answer = await asyncio.to_thread(provider.generate, prompt, ResearcherAnswer)
    logger.info(
        "Researcher answer ready (confidence=%s, sources=%s).",
        answer.confidence,
        [s.filename for s in answer.sources],
    )
    return answer


# ─── Analysis Filing ──────────────────────────────────────────────────────────

def build_analysis_page(question: str, answer: ResearcherAnswer) -> tuple[str, str]:
    """
    Format a ResearcherAnswer as a reusable analysis page.

    Returns
    -------
    tuple[str, str]
        (repo_path, markdown_content)
    """
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Slugify the question for the filename
    slug = re.sub(r"[^\w\s-]", "", question.lower())
    slug = re.sub(r"[\s_]+", "-", slug).strip("-")[:60]

    path = f"{settings.analyses_dir}/{today}-{slug}.md"

    sources_section = "\n".join(
        f"- `{s.filename}`: {s.relevant_excerpt}" for s in answer.sources
    ) or "_No specific sources cited._"

    content = f"""\
---
type: analysis
date: {today}
question: "{question}"
confidence: {answer.confidence}
---

## Question
{question}

## Answer
{answer.answer}

## Sources
{sources_section}
"""
    return path, content
