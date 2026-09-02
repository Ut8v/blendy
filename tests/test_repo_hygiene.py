"""Repo invariants, enforced rather than requested.

CONTRIBUTING.md states the rules; this file is what actually holds them. Every
test here corresponds to a way a well-meaning contribution can quietly break the
architecture or publish something that should have stayed local. A rule written
only in prose is a rule that gets broken on a busy afternoon.

Runs in plain Python, no Blender, so CI can run it on every pull request.
"""

import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def tracked(*globs):
    """Files git actually tracks. Untracked scratch files are not our business."""
    out = subprocess.run(
        ["git", "ls-files", "-z", *globs],
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout
    return [ROOT / p for p in out.split("\0") if p]


def read(path):
    return path.read_text(encoding="utf-8", errors="replace")


class TestNothingPrivateIsPublished(unittest.TestCase):
    """The repo is public. These files are local-only or the author's own input."""

    # Path fragments that must never appear in the tracked tree.
    FORBIDDEN = [
        ("CLAUDE.md", "the private harness brief stays local"),
        (".claude/settings.local.json", "local tool permissions"),
        ("director/studio_state.json", "local studio session state"),
        ("assets/references/", "reference images are the author's, not redistributed"),
        ("assets/cache/", "downloaded asset cache is a build artifact"),
        ("blendy.sqlite", "the local database is not source"),
    ]

    def test_no_local_only_paths_are_tracked(self):
        paths = [str(p.relative_to(ROOT)) for p in tracked()]
        for fragment, why in self.FORBIDDEN:
            hits = [p for p in paths if p == fragment or p.startswith(fragment)]
            self.assertEqual(hits, [], f"{fragment} must stay untracked: {why}")

    def test_no_build_artifacts_are_tracked(self):
        bad = [p for p in tracked() if p.suffix in {".blend", ".blend1", ".pyc"}]
        self.assertEqual(
            [str(p.relative_to(ROOT)) for p in bad], [],
            "the spec is the source of truth; .blend outputs are regenerable",
        )

    def test_gitignore_still_covers_the_local_only_paths(self):
        """A contributor deleting a gitignore line is how these get committed."""
        ignore = read(ROOT / ".gitignore")
        for needed in ("/CLAUDE.md", "/assets/references/", "*.blend",
                       "/.claude/settings.local.json", "/director/studio_state.json"):
            self.assertIn(needed, ignore, f".gitignore must keep ignoring {needed}")

    def test_no_images_outside_the_places_images_belong(self):
        """Renders belong in docs/images. A stray png is usually a reference leak."""
        allowed = ("docs/images/", "evals/references/", "director/web/")
        stray = [
            str(p.relative_to(ROOT)) for p in tracked("*.png", "*.jpg", "*.jpeg", "*.webp")
            if not str(p.relative_to(ROOT)).startswith(allowed)
        ]
        self.assertEqual(stray, [], "images outside docs/images are usually references")


class TestNoSecrets(unittest.TestCase):
    """Agents run on a Claude Code subscription. No key should ever be in here."""

    PATTERNS = [
        (r"sk-ant-[A-Za-z0-9_-]{8,}", "an Anthropic API key"),
        (r"AKIA[0-9A-Z]{16}", "an AWS access key id"),
        (r"gh[pousr]_[A-Za-z0-9]{20,}", "a GitHub token"),
        (r"-----BEGIN [A-Z ]*PRIVATE KEY-----", "a private key"),
        (r"""(?i)\b(api[_-]?key|secret|password|token)\b\s*[:=]\s*["'][A-Za-z0-9/+_-]{16,}["']""",
         "a hardcoded credential"),
    ]

    def test_no_secret_shaped_strings_in_tracked_text(self):
        for path in tracked():
            if path.suffix in {".png", ".jpg", ".jpeg", ".webp", ".glb", ".sqlite"}:
                continue
            text = read(path)
            for pattern, what in self.PATTERNS:
                match = re.search(pattern, text)
                if match and "test_repo_hygiene" not in str(path):
                    self.fail(f"{path.relative_to(ROOT)} looks like it contains {what}")

    def test_no_anthropic_sdk_dependency(self):
        """Hard requirement: the subscription, never the API. An SDK import is a
        route to billing the user twice and to running outside the harness."""
        for path in tracked("*.py"):
            if "test_repo_hygiene" in str(path):
                continue
            text = read(path)
            self.assertNotRegex(
                text, r"^\s*(import anthropic|from anthropic)",
                f"{path.relative_to(ROOT)} must not use the Anthropic SDK",
            )

    def test_no_absolute_home_paths(self):
        """A path under /Users or /home is both a leak and unportable."""
        for path in tracked("*.py", "*.js", "*.json", "*.sh", "*.md", "*.yml"):
            if "test_repo_hygiene" in str(path):
                continue
            self.assertNotRegex(
                read(path), r"/(Users|home)/[a-z][a-z0-9_-]{2,}/",
                f"{path.relative_to(ROOT)} contains an absolute home directory path",
            )


class TestTheAgentCannotWriteBpy(unittest.TestCase):
    """Hard rule 1: the only mutation path is editing a document and recompiling.

    The moment a tool executes arbitrary code, every other guarantee in the
    system — determinism, validation, rollback — becomes advisory.
    """

    def test_only_the_compiler_imports_bpy(self):
        allowed = ("compiler/", "tests/blender/")
        for path in tracked("*.py"):
            rel = str(path.relative_to(ROOT))
            if rel.startswith(allowed) or "test_repo_hygiene" in rel:
                continue
            self.assertNotRegex(
                read(path), r"^\s*(import bpy|from bpy)",
                f"{rel} imports bpy; only the compiler may touch Blender",
            )

    def test_the_validator_runs_without_blender(self):
        """validate.py runs in CI and in the server process, where bpy does not
        exist. An import added here fails at the worst possible moment."""
        for path in tracked("compiler/validate*.py"):
            self.assertNotRegex(
                read(path), r"^\s*(import bpy|from bpy)",
                f"{path.relative_to(ROOT)} must import cleanly outside Blender",
            )

    def test_no_tool_executes_arbitrary_code(self):
        """No execute_blender_code, no eval of agent input, no shelling out to a
        python string. This is the rule the whole architecture rests on."""
        banned = [
            (r"def\s+execute_blender_code", "an arbitrary-code tool"),
            (r"\beval\s*\(", "eval()"),
            (r"\bexec\s*\(", "exec()"),
        ]
        for path in tracked("server/*.py", "server/**/*.py"):
            text = read(path)
            for pattern, what in banned:
                self.assertNotRegex(
                    text, pattern,
                    f"{path.relative_to(ROOT)} uses {what}; the tool surface is typed",
                )


class TestSkillPartition(unittest.TestCase):
    """Hard rule 10 and the learned-line cap. A skill file that loses its marker
    silently makes the human-authored core editable by an automated process."""

    MARKER = "<!-- LEARNED -->"
    CAP = 40

    def test_every_skill_has_the_partition_marker(self):
        skills = tracked("skills/*.md")
        self.assertTrue(skills, "expected skill files under skills/")
        for path in skills:
            self.assertIn(
                self.MARKER, read(path),
                f"{path.relative_to(ROOT)} lost its core/learned partition",
            )

    def test_learned_sections_stay_under_the_cap(self):
        """The cap is the mechanism, not a limitation: it forces consolidation."""
        for path in tracked("skills/*.md"):
            body = read(path).split(self.MARKER, 1)[1]
            lines = [ln for ln in body.splitlines() if ln.strip()]
            self.assertLessEqual(
                len(lines), self.CAP,
                f"{path.relative_to(ROOT)} learned section must consolidate to add",
            )

    def test_learned_lines_cite_incidents(self):
        """A learned line without incident refs cannot be pruned when its cause
        stops recurring, so it accumulates forever."""
        for path in tracked("skills/*.md"):
            body = read(path).split(self.MARKER, 1)[1]
            for line in body.splitlines():
                stripped = line.strip()
                if not stripped.startswith("- "):
                    continue
                self.assertRegex(
                    stripped, r"\[INC-\d+(,\s*INC-\d+)*\]",
                    f"{path.relative_to(ROOT)}: learned line must cite incident ids: "
                    f"{stripped[:60]}",
                )


class TestDocumentsStayValid(unittest.TestCase):
    """Every JSON document in the repo is either a schema, a spec, or a profile.
    A malformed one fails at build time, in Blender, where it is expensive."""

    def test_all_tracked_json_parses(self):
        import json
        for path in tracked("*.json"):
            with self.subTest(path=str(path.relative_to(ROOT))):
                try:
                    json.loads(read(path))
                except json.JSONDecodeError as exc:
                    self.fail(f"{path.relative_to(ROOT)} is not valid JSON: {exc}")

    def test_model_recipes_validate(self):
        """A recipe that only fails inside Blender fails expensively."""
        import json
        import sys
        sys.path.insert(0, str(ROOT))
        from compiler.validate_model import validate_model

        for path in tracked("models/*.json"):
            with self.subTest(path=str(path.relative_to(ROOT))):
                result = validate_model(json.loads(read(path)))
                self.assertTrue(
                    result.ok,
                    f"{path.relative_to(ROOT)} is not a valid recipe: {result.errors}",
                )


class TestFileSize(unittest.TestCase):
    """A 900-line module is where the compiler stops being reviewable."""

    LIMIT = 500

    def test_no_source_file_exceeds_the_line_limit(self):
        oversize = []
        for path in tracked("*.py", "*.js"):
            count = len(read(path).splitlines())
            if count > self.LIMIT:
                oversize.append(f"{path.relative_to(ROOT)} ({count} lines)")
        self.assertEqual(
            oversize, [], f"split these rather than growing past {self.LIMIT} lines",
        )

    def test_no_large_binaries(self):
        """Renders are the only binaries here and they are page assets."""
        big = [
            f"{p.relative_to(ROOT)} ({p.stat().st_size // 1024}KB)"
            for p in tracked() if p.exists() and p.stat().st_size > 2 * 1024 * 1024
        ]
        self.assertEqual(big, [], "keep large binaries out of git history")


if __name__ == "__main__":
    unittest.main()
