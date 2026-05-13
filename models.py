"""
models.py — Pydantic models used as structured output schemas for Gemini.

Gemini's response_schema feature converts these into a JSON schema that
constrains the model's output, guaranteeing parseable responses every time.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class FactList(BaseModel):
    """Used for chunked compression of massive documents."""
    facts: list[str] = Field(description="A list of atomic factual claims or mentions.")


# ─── LLM Call 1: Routing & Selection ─────────────────────────────────────────


class RoutingDecision(BaseModel):
    """
    Returned by the first Gemini call.

    The LLM reads the raw Telegram dump and the current index.md, then
    decides which existing wiki files need to be fetched (to be updated)
    and whether any brand-new files should be created.
    """

    files_to_fetch: list[str] = Field(
        default_factory=list,
        description=(
            "List of existing repo-relative file paths that must be read "
            "before integration (e.g. ['wiki/bio.md', 'wiki/projects.md']). "
            "May be empty if only a new file is needed."
        ),
    )
    new_files: list[str] = Field(
        default_factory=list,
        description=(
            "List of repo-relative paths for brand-new wiki files to create "
            "(e.g. ['wiki/travel.md']). May be empty."
        ),
    )
    reasoning: str = Field(
        default="",
        description="Short explanation of why these files were chosen.",
    )


# ─── LLM Call 2: Librarian Compilation ───────────────────────────────────────


class FileUpdate(BaseModel):
    """A single file to be written (created or overwritten) in the GitHub repo."""

    filename: str = Field(
        description=(
            "Repo-relative path of the file to write "
            "(e.g. 'wiki/bio.md' or 'wiki/index.md')."
        )
    )
    new_content: str = Field(
        description="The complete, final markdown content for this file."
    )


class LibrarianOutput(BaseModel):
    """
    Returned by the second (Librarian) Gemini call.

    Must include every file that was modified — including wiki/index.md
    if its table of contents changed (new file added, summary updated, etc.).
    """

    updated_files: list[FileUpdate] = Field(
        description=(
            "All files that the Librarian rewrote. Must include wiki/index.md "
            "if it was modified. The raw_sources dump is added automatically "
            "by the pipeline and must NOT appear here."
        )
    )
    summary: str = Field(
        description=(
            "One or two sentences describing what was integrated and what changed. "
            "Sent to the user as a Telegram notification."
        )
    )
    log_entry: Optional[str] = Field(
        default=None,
        description=(
            "A log entry to prepend to wiki/log.md. "
            "Format: '## [YYYY-MM-DD] ingest | Title\\nOne-sentence summary.'"
        )
    )


# ─── Query / Researcher API ───────────────────────────────────────────────────


class QueryRequest(BaseModel):
    """Incoming payload for POST /query."""

    question: str = Field(
        description="The natural-language question to answer from the wiki."
    )


class QueryRouting(BaseModel):
    """
    Returned by the Researcher's first Gemini call.

    Identifies which wiki files are relevant to answer the question
    without reading ALL files (which would be slow and expensive).
    """

    files_to_read: list[str] = Field(
        default_factory=list,
        description=(
            "Repo-relative paths of the wiki files that likely contain the "
            "answer (e.g. ['wiki/bio.md', 'wiki/projects.md']). "
            "Return an empty list only if index.md already contains the answer."
        ),
    )
    reasoning: str = Field(
        default="",
        description="One sentence explaining which files were chosen and why.",
    )


class CitedSource(BaseModel):
    """A single file used as a source when forming the answer."""

    filename: str = Field(description="Repo-relative path of the source file.")
    relevant_excerpt: str = Field(
        description="The short snippet from that file that supports the answer."
    )


class ResearcherAnswer(BaseModel):
    """
    Returned by the Researcher's second Gemini call — the final answer
    ready to be sent back to the caller (API or Telegram).
    """

    answer: str = Field(
        description=(
            "The complete, well-formatted answer to the question. "
            "Use markdown formatting (bold, bullet lists) where it adds clarity."
        )
    )
    sources: list[CitedSource] = Field(
        default_factory=list,
        description="The wiki files used to construct the answer.",
    )
    confidence: str = Field(
        default="high",
        description=(
            "Confidence level: 'high' if the wiki clearly contains the answer, "
            "'medium' if partially covered, 'low' if the answer is inferred or missing."
        ),
    )


# ─── Telegram webhook payload ─────────────────────────────────────────────────


class TelegramUser(BaseModel):
    id: int
    first_name: Optional[str] = None
    username: Optional[str] = None


class TelegramChat(BaseModel):
    id: int


class TelegramDocument(BaseModel):
    file_id: str
    file_name: Optional[str] = None
    mime_type: Optional[str] = None
    file_size: Optional[int] = None


class TelegramMessage(BaseModel):
    message_id: int
    chat: TelegramChat
    text: Optional[str] = None
    caption: Optional[str] = None
    document: Optional[TelegramDocument] = None
    from_: Optional[TelegramUser] = Field(default=None, alias="from")

    model_config = {"populate_by_name": True}


class TelegramUpdate(BaseModel):
    update_id: int
    message: Optional[TelegramMessage] = None
