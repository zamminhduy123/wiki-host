"""
services/document_service.py — Extracts text from standard document formats.
"""

import logging
import io

import pymupdf4llm

logger = logging.getLogger(__name__)


def extract_text(file_bytes: bytes, file_name: str, mime_type: str) -> str:
    """
    Extract readable markdown or text from a document.

    Supports:
    - application/pdf -> Markdown (using pymupdf4llm)
    - text/plain, text/markdown -> Raw text string
    """
    mime_lower = mime_type.lower() if mime_type else ""
    name_lower = file_name.lower() if file_name else ""

    logger.debug("Extracting text for document: %s (mime: %s), size: %d bytes", file_name, mime_type, len(file_bytes))

    # 1. Text / Markdown
    if mime_lower.startswith("text/") or name_lower.endswith(".txt") or name_lower.endswith(".md"):
        try:
            return file_bytes.decode("utf-8")
        except UnicodeDecodeError:
            try:
                return file_bytes.decode("latin-1")
            except Exception as e:
                raise ValueError("Could not decode text file contents.") from e

    # 2. PDF (via PyMuPDF4LLM to Markdown)
    if mime_lower == "application/pdf" or name_lower.endswith(".pdf"):
        # pymupdf4llm natively supports reading from memory if we pass pymupdf document, 
        # or we can write to temp disk. pymupdf4llm.to_markdown accepts a byte stream in recent versions.
        # However, to be safe across versions, it explicitly supports streaming via standard pymupdf library objects,
        # but the easiest way is to let the library handle it via a temporary file or memory buffer if supported.
        import fitz  # PyMuPDF core library
        
        try:
            doc = fitz.open("pdf", file_bytes)
            # Convert to markdown
            md_text = pymupdf4llm.to_markdown(doc)
            return md_text
        except Exception as e:
            logger.error("Failed to parse PDF: %s", e)
            raise ValueError(f"Failed to read PDF document: {e}") from e

    # 3. Unsupported format
    raise ValueError(f"Unsupported file type. Received: {file_name} ({mime_type})")


def chunk_text(text: str, chunk_size: int = 25000) -> list[str]:
    """Divide a long string into smaller chunks for processing."""
    if len(text) <= chunk_size:
        return [text]
    
    chunks = []
    for i in range(0, len(text), chunk_size):
        chunks.append(text[i : i + chunk_size])
    return chunks


async def condense_document(text: str, provider, on_status=None) -> str:
    """
    Condense a massive document into high-density facts to fit in context.
    
    This is used when a source (like a 100-page PDF) exceeds the LLM's memory.
    """
    if len(text) < 40000:
        return text  # Small enough for a single pass

    from models import FactList
    chunks = chunk_text(text, chunk_size=30000)
    
    if on_status:
        on_status(f"Document is massive ({len(text)} chars). Compressing {len(chunks)} chunks...")
    
    all_facts = []
    for i, chunk in enumerate(chunks):
        if on_status:
            on_status(f"Extracting facts from chunk {i+1}/{len(chunks)}...")
        
        prompt = f"""\
Extract every single factual claim, entity (person/project/org), and concept from this text.
Return a dense bulleted list. Skip fluff, intros, and boilerplate.

---
## CHUNK {i+1}
{chunk}
"""
        try:
            # We use a simple FactList model (just a list of strings)
            res = provider.generate(prompt, FactList)
            all_facts.extend(res.facts)
        except Exception as e:
            logger.warning(f"Failed to process chunk {i+1}: {e}")
            all_facts.append(f"[ERROR: could not process part of document]")

    return "\n".join(f"- {f}" for f in all_facts)

