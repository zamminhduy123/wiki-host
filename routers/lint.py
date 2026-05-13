"""
routers/lint.py — POST /lint

Wiki health-check endpoint.
Reads the index, compares it against the actual GitHub repo tree,
and reports: orphans (in repo but not indexed), ghosts (indexed but missing),
and a basic stats summary.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from config import settings
from services import github_service

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post(
    "/lint",
    summary="Health-check the wiki",
    description=(
        "Reads wiki/index.md and the real GitHub repo tree, "
        "then reports orphan pages, ghost index entries, and overall stats."
    ),
    tags=["Maintenance"],
)
async def lint_wiki() -> JSONResponse:
    """
    POST /lint

    Returns a structured report:
    {
        "stats":   {...},
        "orphans": [...],
        "ghosts":  [...],
        "ok":      true | false
    }
    """
    logger.info("[Lint] Starting wiki health check...")

    # ── 1. Get real file tree from GitHub ──────────────────────────────────
    repo = github_service._get_repo()
    tree = repo.get_git_tree(settings.github_branch, recursive=True).tree

    real_wiki_files = {
        item.path
        for item in tree
        if item.path.startswith(settings.wiki_dir + "/")
        and item.path.endswith(".md")
        and not item.path.endswith(".gitkeep")
    }

    # Exclude special files from checks
    excluded = {settings.index_path, settings.schema_path, settings.log_path, settings.overview_path}
    real_content_files = real_wiki_files - excluded

    # ── 2. Parse index.md for all listed files ─────────────────────────────
    index = github_service.get_file(settings.index_path)
    index_content = index.content if index else ""

    # Extract paths from the markdown table (| wiki/path.md | ... |)
    indexed_files = set(re.findall(r'wiki/[\w\-/\.]+\.md', index_content))
    indexed_files -= excluded

    # ── 3. Find discrepancies ──────────────────────────────────────────────
    orphans = sorted(real_content_files - indexed_files)   # exist in repo, not in index
    ghosts  = sorted(indexed_files - real_content_files)   # listed in index, not in repo

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    ok = len(orphans) == 0 and len(ghosts) == 0

    report = {
        "date": today,
        "ok": ok,
        "stats": {
            "total_wiki_files": len(real_wiki_files),
            "indexed_files": len(indexed_files),
            "orphan_count": len(orphans),
            "ghost_count": len(ghosts),
        },
        "orphans": orphans,   # files in repo but missing from index
        "ghosts":  ghosts,    # files in index but not in repo
        "message": "✅ Wiki is healthy." if ok else (
            f"⚠️ Found {len(orphans)} orphan(s) and {len(ghosts)} ghost(s). "
            "Consider updating wiki/index.md or removing stale entries."
        ),
    }

    # ── 4. Append to log ───────────────────────────────────────────────────
    status_str = "healthy" if ok else f"{len(orphans)} orphan(s), {len(ghosts)} ghost(s)"
    log_entry = f"## [{today}] lint | Wiki health check\nResult: {status_str}."
    try:
        github_service.append_to_log(log_entry)
    except Exception as e:
        logger.warning("[Lint] Could not write to log: %s", e)

    logger.info("[Lint] ✅ Done. ok=%s orphans=%d ghosts=%d", ok, len(orphans), len(ghosts))
    return JSONResponse(content=report)
