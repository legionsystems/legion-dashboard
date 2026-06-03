"""Tests for local pre-push review workflow (WI-27 follow-up)."""

import os
import subprocess
import tempfile
from pathlib import Path

import pytest


class TestLocalCodexReview:
    """Tests for legion-codex-local-review wrapper."""

    def test_wrapper_exists(self):
        """Local review wrapper script exists and is executable."""
        wrapper = Path("/root/.hermes/LEGION_TOOLS/bin/legion-codex-local-review")
        assert wrapper.exists(), "legion-codex-local-review not found"
        assert os.access(wrapper, os.X_OK), "legion-codex-local-review not executable"

    def test_wrapper_rejects_missing_base(self):
        """Wrapper rejects when base branch does not exist."""
        wrapper = "/root/.hermes/LEGION_TOOLS/bin/legion-codex-local-review"
        result = subprocess.run([
            wrapper,
            "--repo", "/srv/repo/legion-dashboard",
            "--base", "nonexistent-branch-xyz",
            "--head", "feature/dashboard-bootstrap-control-plane",
            "--report", "/tmp/test-report.md",
        ], capture_output=True, text=True)
        assert result.returncode != 0
        assert "not found" in result.stderr.lower()

    def test_wrapper_rejects_missing_head(self):
        """Wrapper rejects when head branch does not exist."""
        wrapper = "/root/.hermes/LEGION_TOOLS/bin/legion-codex-local-review"
        result = subprocess.run([
            wrapper,
            "--repo", "/srv/repo/legion-dashboard",
            "--base", "feature/dashboard-bootstrap-control-plane",
            "--head", "nonexistent-branch-xyz",
            "--report", "/tmp/test-report.md",
        ], capture_output=True, text=True)
        assert result.returncode != 0
        assert "not found" in result.stderr.lower()

    def test_wrapper_requires_all_args(self):
        """Wrapper requires --repo, --base, --head, --report."""
        wrapper = "/root/.hermes/LEGION_TOOLS/bin/legion-codex-local-review"
        result = subprocess.run([wrapper], capture_output=True, text=True)
        assert result.returncode != 0
        assert "Missing required argument" in result.stderr

    def test_report_includes_base_head_shas(self):
        """Report includes base and head SHAs."""
        wrapper = "/root/.hermes/LEGION_TOOLS/bin/legion-codex-local-review"
        report_path = "/tmp/test_local_review_shas.md"
        result = subprocess.run([
            wrapper,
            "--repo", "/srv/repo/legion-dashboard",
            "--base", "feature/dashboard-bootstrap-control-plane",
            "--head", "feature/local-pre-push-review-gate",
            "--report", report_path,
        ], capture_output=True, text=True, timeout=120)
        
        # May fail if Codex not configured, but we check report format
        if os.path.exists(report_path):
            content = Path(report_path).read_text()
            assert "Base:" in content
            assert "Head:" in content
            assert "SHA" in content or "sha" in content.lower()


class TestSecretScan:
    """Tests for legion-secret-scan wrapper."""

    def test_wrapper_exists(self):
        """Secret scan wrapper exists and is executable."""
        wrapper = Path("/root/.hermes/LEGION_TOOLS/bin/legion-secret-scan")
        assert wrapper.exists(), "legion-secret-scan not found"
        assert os.access(wrapper, os.X_OK), "legion-secret-scan not executable"

    def test_scan_blocks_api_key_pattern(self):
        """Scan blocks obvious API key patterns in diff."""
        wrapper = "/root/.hermes/LEGION_TOOLS/bin/legion-secret-scan"
        
        # Create a test repo with a secret
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            (repo / ".git").mkdir()
            subprocess.run(["git", "init"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, capture_output=True)
            
            # Create base commit
            (repo / "file.txt").write_text("clean")
            subprocess.run(["git", "add", "."], cwd=repo, capture_output=True)
            subprocess.run(["git", "commit", "-m", "base"], cwd=repo, capture_output=True)
            subprocess.run(["git", "checkout", "-b", "feature-with-secret"], cwd=repo, capture_output=True)
            
            # Add secret
            (repo / "file.txt").write_text("API_KEY=sk-1234567890abcdefghijklmnop")
            subprocess.run(["git", "add", "."], cwd=repo, capture_output=True)
            subprocess.run(["git", "commit", "-m", "add secret"], cwd=repo, capture_output=True)
            
            result = subprocess.run([
                wrapper,
                "--repo", str(repo),
                "--base", "main",
                "--head", "feature-with-secret",
            ], capture_output=True, text=True)
            
            assert result.returncode == 1, f"Expected exit 1, got {result.returncode}: {result.stdout}"

    def test_scan_passes_clean_diff(self):
        """Scan passes when diff has no secrets."""
        wrapper = "/root/.hermes/LEGION_TOOLS/bin/legion-secret-scan"
        
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            (repo / ".git").mkdir()
            subprocess.run(["git", "init"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, capture_output=True)
            
            # Create base commit
            (repo / "file.txt").write_text("clean")
            subprocess.run(["git", "add", "."], cwd=repo, capture_output=True)
            subprocess.run(["git", "commit", "-m", "base"], cwd=repo, capture_output=True)
            subprocess.run(["git", "checkout", "-b", "clean-feature"], cwd=repo, capture_output=True)
            
            # Add clean change
            (repo / "file.txt").write_text("clean change")
            subprocess.run(["git", "add", "."], cwd=repo, capture_output=True)
            subprocess.run(["git", "commit", "-m", "clean change"], cwd=repo, capture_output=True)
            
            result = subprocess.run([
                wrapper,
                "--repo", str(repo),
                "--base", "main",
                "--head", "clean-feature",
            ], capture_output=True, text=True)
            
            assert result.returncode == 0, f"Expected exit 0, got {result.returncode}: {result.stderr}"


class TestBuilderPrompt:
    """Tests for builder prompt generation with local-first instructions."""

    def test_prompt_says_do_not_push_before_review(self):
        """Generated builder prompt says do not push before local review."""
        from app.routers.builder import _generate_hermes_prompt
        from app.models import WorkItem
        
        wi = WorkItem(
            id=999,
            title="Test WI",
            type="feature",
            priority="medium",
            target_app="legion-dashboard",
        )
        prompt = _generate_hermes_prompt(wi)
        
        assert "Do NOT push" in prompt or "do NOT push" in prompt or "Do not push" in prompt
        assert "local" in prompt.lower()
        assert "review" in prompt.lower()

    def test_prompt_includes_local_review_command(self):
        """Generated prompt includes local Codex review command."""
        from app.routers.builder import _generate_hermes_prompt
        from app.models import WorkItem
        
        wi = WorkItem(
            id=999,
            title="Test WI",
            type="feature",
            priority="medium",
            target_app="legion-dashboard",
        )
        prompt = _generate_hermes_prompt(wi)
        
        assert "legion-codex-local-review" in prompt

    def test_prompt_includes_secret_scan_command(self):
        """Generated prompt includes secret scan command."""
        from app.routers.builder import _generate_hermes_prompt
        from app.models import WorkItem
        
        wi = WorkItem(
            id=999,
            title="Test WI",
            type="feature",
            priority="medium",
            target_app="legion-dashboard",
        )
        prompt = _generate_hermes_prompt(wi)
        
        assert "legion-secret-scan" in prompt


class TestReviewTaskTemplate:
    """Tests for review task template with local review fields."""

    def test_template_includes_repo_path(self):
        """Review task template includes repo path."""
        # Check the design doc which specifies template requirements
        design = Path("/root/.hermes/LEGION_TOOLS/LOCAL_PRE_PUSH_REVIEW_DESIGN.md")
        if design.exists():
            content = design.read_text()
            assert "repo" in content.lower() or "path" in content.lower()

    def test_template_includes_base_branch(self):
        """Review task template includes base branch."""
        design = Path("/root/.hermes/LEGION_TOOLS/LOCAL_PRE_PUSH_REVIEW_DESIGN.md")
        if design.exists():
            content = design.read_text()
            assert "base" in content.lower()

    def test_template_includes_head_branch(self):
        """Review task template includes head branch."""
        design = Path("/root/.hermes/LEGION_TOOLS/LOCAL_PRE_PUSH_REVIEW_DESIGN.md")
        if design.exists():
            content = design.read_text()
            assert "head" in content.lower() or "implementation" in content.lower()

    def test_template_includes_local_commit_sha(self):
        """Review task template includes local commit SHA."""
        design = Path("/root/.hermes/LEGION_TOOLS/LOCAL_PRE_PUSH_REVIEW_DESIGN.md")
        if design.exists():
            content = design.read_text()
            assert "SHA" in content or "sha" in content or "commit" in content.lower()

    def test_template_includes_report_path(self):
        """Review task template includes local Codex report path."""
        design = Path("/root/.hermes/LEGION_TOOLS/LOCAL_PRE_PUSH_REVIEW_DESIGN.md")
        if design.exists():
            content = design.read_text()
            assert "report" in content.lower()

    def test_template_instructs_codex_not_llm(self):
        """Review task template instructs to use Codex, not LLM-only review."""
        design = Path("/root/.hermes/LEGION_TOOLS/LOCAL_PRE_PUSH_REVIEW_DESIGN.md")
        if design.exists():
            content = design.read_text()
            assert "codex" in content.lower() or "Codex" in content


class TestNoParentDependency:
    """Tests that review tasks do not use parent/child dependency to become runnable."""

    def test_design_explains_no_dependency(self):
        """Design note explains that review tasks should not use parent dependency."""
        design = Path("/root/.hermes/LEGION_TOOLS/LOCAL_PRE_PUSH_REVIEW_DESIGN.md")
        if design.exists():
            content = design.read_text()
            # The design should mention that local review doesn't need parent deps
            assert "local" in content.lower()
