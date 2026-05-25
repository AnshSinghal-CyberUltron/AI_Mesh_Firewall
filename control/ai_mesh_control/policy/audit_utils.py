"""
Audit helpers for policy check: prompt/response snippets, hashes, and file references (Phase 3).
"""

import hashlib
import re


def extract_file_paths_from_prompt(prompt: str, max_results: int = 50) -> list:
    """
    Extract likely file paths from prompt text using simple patterns.
    Returns list of unique strings (paths or path-like substrings).
    """
    if not prompt or not isinstance(prompt, str):
        return []
    # Common patterns: /path/to/file, C:\path\to\file, path/to/file.ext, "path", 'path'
    pattern = r'(?:^|[\s"\'(])((?:[A-Za-z]:)?(?:[\\/][^\s\'"<>|*?]*)|(?:[a-zA-Z0-9_.-]+(?:[\\/][^\s\'"<>|*?]*)+))(?:[\s"\')]|$)'
    matches = re.findall(pattern, prompt)
    seen = set()
    out = []
    for m in matches:
        m = m.strip().strip("\"'").strip()
        if len(m) > 2 and m not in seen:
            seen.add(m)
            out.append(m)
            if len(out) >= max_results:
                break
    return out


def _hash_text(text: str) -> str:
    """Return SHA-256 hex digest of text (empty string for empty input)."""
    if not text:
        return ""
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def build_audit_metadata(prompt: str, response_text: str, snippet_length: int) -> dict:
    """
    Build metadata dict for audit: prompt_snippet, response_snippet, prompt_hash, response_hash, files_in_prompts.
    Use redacted/snippet versions to limit PII exposure.
    """
    prompt = prompt or ""
    response_text = response_text or ""
    return {
        "prompt_snippet": prompt[:snippet_length] if prompt else "",
        "response_snippet": response_text[:snippet_length] if response_text else "",
        "prompt_hash": _hash_text(prompt),
        "response_hash": _hash_text(response_text),
        "files_in_prompts": extract_file_paths_from_prompt(prompt),
    }
