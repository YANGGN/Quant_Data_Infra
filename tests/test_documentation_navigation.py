"""Offline links in current entry points, without relinting historical narratives.

Covers the inline links, ATX headings and explicit HTML anchors used here.
It is intentionally not a general Markdown parser or an external URL checker.
"""
from html import unescape
from pathlib import Path
import re
import tempfile
import unittest
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
SECTIONS = (
    ("README.md", None, "## Historical rebuild record"),
    ("ARCHITECTURE.md", None, "This document defines the accepted target architecture"),
    ("ROADMAP.md", None, "Planning baseline:"),
    ("AGENTS.md", None, None),
    ("deploy/systemd/README.md", None, "## Historical activation records"),
    ("docs/rebuild/FAST_PATH_DEVELOPMENT.md", None, None),
    ("docs/rebuild/README.md", "## 2. Document map", "## 3. Authority"),
    ("docs/rebuild/TEST_STRATEGY.md", "## 4. Test layers", "## 5. Fixtures"),
    ("docs/rebuild/CURRENT_OPERATING_ENVELOPE.md",
     "### Latest recorded decisions by workflow", "### Historical baseline scopes"),
)
LINK = re.compile(r"\[[^\]\n]+\]\(([^\s)]+)\)")


def prose(text):
    """Ignore fenced examples, which may deliberately contain placeholder links."""
    lines = []
    fence = None
    for line in text.splitlines():
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
        if marker:
            token = marker[1]
            if fence is None:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence):
                fence = None
        elif fence is None:
            lines.append(line)
    return "\n".join(lines)


def anchors(text):
    text = prose(text)
    found = set(re.findall(r'<(?:a|[a-z][a-z0-9]*)\b[^>]*\b(?:id|name)=["\']([^"\']+)["\']', text))
    headings = set()
    for heading in re.findall(r"^ {0,3}#{1,6}\s+(.+?)\s*#*\s*$", text, re.MULTILINE):
        heading = LINK.sub(lambda m: m[0].split("](", 1)[0][1:], heading)
        heading = re.sub(r"<[^>]+>", "", unescape(heading)).lower()
        slug = re.sub(r"[^\w\- ]", "", heading).replace(" ", "-")
        candidate, suffix = slug, 0
        while candidate in headings:
            suffix += 1
            candidate = f"{slug}-{suffix}"
        headings.add(candidate)
    return found | headings


def link_errors(root, source, text):
    errors = []
    for destination in LINK.findall(prose(text)):
        url = urlsplit(destination)
        if url.scheme or url.netloc:
            continue
        target = (source.parent / unquote(url.path)).resolve() if url.path else source
        if not target.is_relative_to(root) or not target.is_file():
            errors.append(f"{destination}: missing repository file")
        elif url.fragment:
            if target.suffix != ".md" or unquote(url.fragment) not in anchors(target.read_text()):
                errors.append(f"{destination}: missing Markdown anchor")
    return errors


class DocumentationNavigationTests(unittest.TestCase):
    def test_current_navigation_files_and_anchors_resolve(self):
        for name, start, end in SECTIONS:
            with self.subTest(document=name):
                source = ROOT / name
                text = source.read_text()
                if start:
                    text = text[text.index(start):]
                if end:
                    text = text[:text.index(end)]
                self.assertTrue(LINK.search(prose(text)), "Navigation selection contains no links")
                self.assertEqual(link_errors(ROOT, source, text), [])

    def test_missing_file_and_fragment_are_reported_without_fetching_urls(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "README.md"
            source.write_text("# Main\n")
            target = root / "target.md"
            target.write_text('# Repeated heading\n# Repeated heading\n<a id="stable-anchor"></a>\n')
            good = ("[heading](target.md#repeated-heading) [duplicate](target.md#repeated-heading-1) "
                    "[explicit](target.md#stable-anchor) [local](#main) "
                    "[external](https://example.invalid/never-requested)\n"
                    "```md\n[example](missing.md)\n```\n")
            self.assertEqual(link_errors(root, source, good), [])
            self.assertEqual(link_errors(root, source, "[bad](missing.md) [bad](target.md#absent)"),
                             ["missing.md: missing repository file", "target.md#absent: missing Markdown anchor"])
