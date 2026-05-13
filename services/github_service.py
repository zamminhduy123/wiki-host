"""
services/github_service.py — GitHub read & batch-write helpers.

Key design decision: we never call repo.update_file() because that creates
one commit per file.  Instead we use the low-level Git Data API:

    create_git_tree  →  create_git_commit  →  update_ref (HEAD)

This produces a single, atomic commit regardless of how many files changed.
"""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass

from github import Github, GithubException, InputGitTreeElement
from github.Repository import Repository

from config import settings

logger = logging.getLogger(__name__)


# ─── Internal helpers ─────────────────────────────────────────────────────────


@dataclass
class RepoFile:
    """Holds a file's decoded content and its current blob SHA (needed for updates)."""

    path: str
    content: str
    sha: str | None = None  # None for brand-new files


def _get_repo() -> Repository:
    """Authenticate and return the target GitHub repository object."""
    gh = Github(settings.github_token)
    return gh.get_repo(settings.github_repo)


# ─── Public API ───────────────────────────────────────────────────────────────


def get_file(path: str) -> RepoFile | None:
    """
    Fetch a single file from the repo.

    Returns None (without raising) if the file doesn't exist yet — this lets
    the pipeline handle the "new file" case gracefully.
    """
    repo = _get_repo()
    try:
        content_file = repo.get_contents(path, ref=settings.github_branch)
        # get_contents can return a list for directories; guard against that.
        if isinstance(content_file, list):
            logger.warning("get_file('%s') returned a directory listing — skipping.", path)
            return None

        decoded = base64.b64decode(content_file.content).decode("utf-8")
        return RepoFile(path=path, content=decoded, sha=content_file.sha)

    except GithubException as exc:
        if exc.status == 404:
            logger.info("File '%s' not found in repo — treating as new.", path)
            return None
        raise


def fetch_files(paths: list[str]) -> dict[str, RepoFile]:
    """
    Fetch multiple files in one pass.

    Returns a dict keyed by path.  Missing files are omitted from the result
    (rather than raising) so the caller can distinguish "exists" vs "new".
    """
    result: dict[str, RepoFile] = {}
    for path in paths:
        file = get_file(path)
        if file is not None:
            result[path] = file
        else:
            logger.info("Skipping fetch for '%s' (will be created).", path)
    return result


def batch_commit(
    file_updates: dict[str, str],
    commit_message: str,
) -> str:
    """
    Write multiple files to the repo in a **single atomic commit**.

    Parameters
    ----------
    file_updates : dict[str, str]
        Mapping of repo-relative path → full new file content (UTF-8 string).
    commit_message : str
        The Git commit message.

    Returns
    -------
    str
        The SHA of the newly created commit.

    Implementation Notes
    --------------------
    GitHub's Git Data API flow:
      1. Resolve the current HEAD commit for the target branch.
      2. Get that commit's tree SHA (the root tree we'll build on top of).
      3. Create a new tree with blobs for every file we want to write.
         - mode "100644" = regular file
         - type "blob"
         - content = raw string (GitHub creates the blob internally)
      4. Create a new commit that points to the new tree, with the current
         HEAD as its parent.
      5. Fast-forward the branch ref to the new commit SHA.
    """
    repo = _get_repo()

    # 1. Resolve current HEAD
    branch_ref = repo.get_git_ref(f"heads/{settings.github_branch}")
    head_sha = branch_ref.object.sha
    logger.debug("Current HEAD for branch '%s': %s", settings.github_branch, head_sha)

    # 2. Get base tree SHA from the HEAD commit
    head_commit = repo.get_git_commit(head_sha)
    base_tree_sha = head_commit.tree.sha
    logger.debug("Base tree SHA: %s", base_tree_sha)

    tree_elements = []
    for path, content in file_updates.items():
        element = InputGitTreeElement(
            path=path,
            mode="100644",
            type="blob",
            content=content,
        )
        tree_elements.append(element)
        logger.debug("Queued '%s' for commit (%d chars).", path, len(content))

    if not tree_elements:
        raise ValueError("batch_commit called with an empty file_updates dict.")

    # 4. Create the new tree, layered on top of the existing base tree
    new_tree = repo.create_git_tree(tree_elements, base_tree=repo.get_git_tree(base_tree_sha))

    # 5. Create the commit
    new_commit = repo.create_git_commit(
        message=commit_message,
        tree=new_tree,
        parents=[head_commit],
    )
    logger.info("Created commit %s: %s", new_commit.sha, commit_message)

    # 6. Advance the branch ref (fast-forward)
    branch_ref.edit(new_commit.sha)
    logger.info("Branch '%s' advanced to %s.", settings.github_branch, new_commit.sha)

    return new_commit.sha


def ensure_index_exists() -> str:
    """
    Return the content of wiki/index.md, creating a blank one if absent.

    Called once at the start of the pipeline so the rest of the code can
    always assume index.md exists.
    """
    index = get_file(settings.index_path)
    if index is not None:
        return index.content

    logger.warning(
        "'%s' not found — bootstrapping an empty index.", settings.index_path
    )
    placeholder = (
        "# Wiki Index\n\n"
        "This file is the master catalog of all wiki pages.\n\n"
        "| File | Summary |\n"
        "|------|---------|\n"
    )
    batch_commit(
        {settings.index_path: placeholder},
        "Librarian: Bootstrap wiki/index.md",
    )
    return placeholder


def append_to_log(entry: str) -> str:
    """
    Prepend a new entry to wiki/log.md and commit it.

    This is a lightweight single-file commit separate from the main
    batch commit so that log writes are always guaranteed to succeed
    even if the main ingest commit partially fails.

    Parameters
    ----------
    entry : str
        The formatted log entry, e.g.
        '## [2026-04-14] ingest | My Source\nSummary sentence.'

    Returns
    -------
    str
        SHA of the resulting commit.
    """
    log_file = get_file(settings.log_path)
    current_content = log_file.content if log_file else "# Wiki Operation Log\n\n---\n"

    # Separate the header from the entries, then prepend the new entry
    lines = current_content.split("\n")
    separator_idx = next(
        (i for i, l in enumerate(lines) if l.strip() == "---"), None
    )
    if separator_idx is not None:
        header = "\n".join(lines[:separator_idx + 1])
        rest = "\n".join(lines[separator_idx + 1:]).lstrip("\n")
        new_content = f"{header}\n\n{entry}\n\n{rest}"
    else:
        new_content = f"{current_content}\n\n{entry}\n"

    return batch_commit(
        {settings.log_path: new_content},
        f"Librarian: Log update",
    )
