"""Parse the Part 0 markdown sources of runbook v2.1 so the HTML report can never drift from them."""
import html
import re

V21 = "/tmp/claude-1872320012/-home-contact-cyberultron-com-AI-Mesh-Firewall/1b4d4994-cf08-4d34-a6cb-54fd36f36877/scratchpad/v21"


def inline(s):
    """Escape, then render the only inline markdown the sources use: **bold**, *italic*, `code`."""
    s = html.escape(s.strip(), quote=False)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<em>\1</em>", s)
    return s


def _cells(line):
    parts = line.strip().split("|")
    return [p.strip() for p in parts[1:-1]]


def register():
    rows = []
    for line in open(f"{V21}/part0_register.md", encoding="utf-8"):
        if re.match(r"^\| C\d+ \|", line):
            c = _cells(line)
            assert len(c) == 6, (len(c), line[:80])
            rows.append(dict(id=c[0], where=c[1], proved=c[2], fix=c[3], impact=c[4], before=c[5]))
    return rows


def register_legend():
    for line in open(f"{V21}/part0_register.md", encoding="utf-8"):
        if line.startswith("**Impact**"):
            return line.strip()
    return ""


def facts():
    rows, on = [], False
    for line in open(f"{V21}/part0_draft.md", encoding="utf-8"):
        if line.startswith("## 0.2"):
            on = True
            continue
        if on and line.startswith("## "):
            break
        if on and line.startswith("| ") and not line.startswith("| Quantity") and not line.startswith("|---"):
            c = _cells(line)
            assert len(c) == 4, line[:80]
            rows.append(dict(q=c[0], claim=c[1], measured=c[2], src=c[3]))
    return rows


def verdict():
    head, bullets, on = "", [], False
    for line in open(f"{V21}/part0_draft.md", encoding="utf-8"):
        if line.startswith("## 0.1"):
            on = True
            continue
        if on and line.startswith("## "):
            break
        if on and line.startswith("**"):
            head = line.strip()
        elif on and line.startswith("- "):
            bullets.append(line[2:].strip())
    return head, bullets


def new_cards():
    """Title + objective of each new card in §0.4."""
    out, cur = [], None
    for line in open(f"{V21}/part0_newcards.md", encoding="utf-8"):
        m = re.match(r"^### (GW\d+b) — (.+)$", line.strip())
        if m:
            cur = dict(id=m.group(1), title=m.group(2), objective="", why="")
            out.append(cur)
        elif cur and line.startswith("| Objective |"):
            cur["objective"] = _cells(line)[1]
        elif cur and line.startswith("**Why"):
            cur["why"] = re.sub(r"^\*\*Why[^*]*\*\*\s*", "", line.strip())
        elif line.startswith("## 0.5"):
            break
    return out


def placeholders():
    n = 0
    for f in ("part0_draft.md", "part0_register.md", "part0_newcards.md", "blocks.md"):
        n += open(f"{V21}/{f}", encoding="utf-8").read().count("[BENCH RESULT")
    return n
