import logging
import os
import re
import subprocess
import yaml
from pathlib import Path

logger = logging.getLogger(__name__)

class PublishManager:
    """Automates the sanitization and publication of the public traderBot repo."""

    def __init__(self, config_path: str = "config/publish_config.yaml"):
        with open(config_path, "r") as f:
            self.config = yaml.safe_load(f)
        self.root_dir = Path(os.getcwd())

    def _run_git(self, args: list[str], check: bool = True) -> subprocess.CompletedProcess:
        """Run a git command and return the result."""
        result = subprocess.run(["git"] + args, capture_output=True, text=True)
        if check and result.returncode != 0:
            logger.error("Git command failed: git %s\nStderr: %s", " ".join(args), result.stderr)
            result.check_returncode()
        return result

    def verify_branch(self, expected: str = "github-public"):
        """Ensure we are on the correct branch before starting."""
        res = self._run_git(["branch", "--show-current"])
        current = res.stdout.strip()
        if current != expected:
            raise RuntimeError(f"Must be on branch '{expected}' to publish (current: '{current}')")

    def scrub_content(self, file_path: Path):
        """Apply path scrubs and project name replacements to a file."""
        if not file_path.is_file():
            return

        content = file_path.read_text(encoding="utf-8")
        original_content = content

        # 1. Strip internal-only sections
        # Pattern: <!-- INTERNAL_ONLY --> ... <!-- /INTERNAL_ONLY -->
        content = re.sub(r"<!-- INTERNAL_ONLY -->.*?<!-- /INTERNAL_ONLY -->", "", content, flags=re.DOTALL)

        # 2. Apply path scrubs and project name replacements
        for old, new in self.config.get("path_scrubs", {}).items():
            content = content.replace(old, new)

        # 3. Handle file-specific processing
        file_name = file_path.name
        file_cfg = self.config.get("file_processing", {}).get(file_name, {})
        
        if file_cfg.get("replace_project_name"):
            content = content.replace("backTestingTraderBot", "traderBot")

        if content != original_content:
            file_path.write_text(content, encoding="utf-8")
            logger.info("Scrubbed: %s", file_path.relative_to(self.root_dir))

    def pre_flight_check(self):
        """Scan for banned patterns in the codebase."""
        logger.info("Running pre-flight leak scan...")
        banned = self.config.get("banned_patterns", [])
        
        # We only scan text files in the working tree (that aren't excluded)
        for root, dirs, files in os.walk(self.root_dir):
            # Skip excluded dirs
            dirs[:] = [d for d in dirs if f"{d}/" not in self.config["exclusions"]]
            
            for file in files:
                if file.endswith((".md", ".py", ".sh", ".yaml")):
                    path = Path(root) / file
                    if any(str(path.relative_to(self.root_dir)).startswith(ex) for ex in self.config["exclusions"]):
                        continue
                        
                    content = path.read_text(encoding="utf-8", errors="ignore")
                    for pattern in banned:
                        if re.search(pattern, content):
                            raise ValueError(f"LEAK DETECTED: Pattern '{pattern}' found in {path}")
        logger.info("Pre-flight check passed.")

    def prepare_staging(self):
        """Stage files while applying exclusions."""
        self._run_git(["reset"])
        
        # Build git add command with exclusions
        # git add --all -- ':(exclude)data' ...
        args = ["add", "--all", "--"]
        for ex in self.config["exclusions"]:
            # Remove trailing slash for git pathspec if present
            clean_ex = ex.rstrip("/")
            args.append(f":(exclude){clean_ex}")
            
        self._run_git(args)
        logger.info("Staging prepared with exclusions.")

    def run_publish(self, mode: str = "incremental", commit_msg: str = None):
        """Execute the full publication workflow."""
        self.verify_branch()
        self.pre_flight_check()
        
        temp_branch = "publish-temp-" + os.urandom(4).hex()
        
        try:
            # Create a temporary branch for scrubbing
            self._run_git(["checkout", "-b", temp_branch])
            
            # Walk and scrub
            for root, dirs, files in os.walk(self.root_dir):
                dirs[:] = [d for d in dirs if not d.startswith(".")] # Skip hidden
                for file in files:
                    if file.endswith((".md", ".py", ".sh", ".yaml")):
                        self.scrub_content(Path(root) / file)
            
            self.prepare_staging()
            
            if mode == "full":
                # For full publish, we want a root commit
                # Note: This is more complex to script safely, usually we just incremental
                logger.warning("Full mode (orphan) requires manual verification. Recommended: incremental.")
            
            if not commit_msg:
                commit_msg = "Update public repository with latest hardening and bug fixes"
            
            final_msg = f"{commit_msg}\n\nCo-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
            self._run_git(["commit", "-m", final_msg])
            
            logger.info("Publication prepared on branch %s. Ready to push.", temp_branch)
            print(f"\nSUCCESS: Changes committed to local branch: {temp_branch}")
            print(f"Run: git push https://github.com/Jaggia/traderBot.git {temp_branch}:main")
            
        finally:
            # Note: We don't delete the temp branch yet so the user can push it
            self._run_git(["checkout", "github-public"])
