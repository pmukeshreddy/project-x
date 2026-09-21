"""The sole authoring backend: Astra via the installed, ChatGPT-authenticated Codex CLI."""

import os
from pathlib import Path
import shutil
import subprocess
from typing import Literal

from feature_rl.contracts import StrictModel, Text


class CodexUnavailable(RuntimeError):
    """Codex, its ChatGPT login, or Astra is unavailable. Never changes providers."""


class CodexConfig(StrictModel):
    executable: Text = "codex"
    codex_home: Path | None = None
    model: Literal["gpt-6-astra"] = "gpt-6-astra"
    reasoning_effort: Literal["low", "medium", "high", "xhigh", "max"] = "high"

    def environment(self) -> dict[str, str]:
        # Reuse Codex's auth store/keychain, without reading or copying credentials.
        # In particular, API keys and alternate API endpoint variables are not inherited.
        names = (
            "HOME",
            "PATH",
            "CODEX_HOME",
            "TMPDIR",
            "LANG",
            "LC_ALL",
            "HTTPS_PROXY",
            "HTTP_PROXY",
            "ALL_PROXY",
            "NO_PROXY",
            "SSL_CERT_FILE",
            "SSL_CERT_DIR",
        )
        result = {key: os.environ[key] for key in names if key in os.environ}
        if self.codex_home is not None:
            result["CODEX_HOME"] = str(self.codex_home.resolve())
        return result

    def verify(self, cwd: Path) -> tuple[str, str]:
        executable = shutil.which(self.executable, path=self.environment().get("PATH"))
        if executable is None:
            raise CodexUnavailable(
                "Codex CLI is unavailable; install Codex and run `codex login` with ChatGPT."
            )
        try:
            status = subprocess.run(
                [executable, "-c", 'forced_login_method="chatgpt"', "login", "status"],
                cwd=cwd,
                env=self.environment(),
                capture_output=True,
                text=True,
                timeout=15,
            )
            if (
                status.returncode != 0
                or "Logged in using ChatGPT" not in status.stdout + status.stderr
            ):
                raise CodexUnavailable(
                    "Codex ChatGPT authentication is required; run `codex login` with ChatGPT in the selected CODEX_HOME."
                )
            version = subprocess.run(
                [executable, "--version"],
                cwd=cwd,
                env=self.environment(),
                capture_output=True,
                text=True,
                timeout=15,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise CodexUnavailable(f"Codex preflight failed: {error}") from error
        if version.returncode != 0 or not version.stdout.strip().startswith(
            "codex-cli "
        ):
            raise CodexUnavailable(
                "The configured executable did not report a Codex CLI version."
            )
        return executable, version.stdout.strip()

    def command(self, executable: str, root: Path) -> tuple[str, ...]:
        return (
            executable,
            "-a",
            "never",
            "exec",
            "--ignore-user-config",
            "--ignore-rules",
            "--ephemeral",
            "--skip-git-repo-check",
            "--sandbox",
            "read-only",
            "--model",
            self.model,
            "--json",
            "--color",
            "never",
            "--output-schema",
            str(root / "schema.json"),
            "--output-last-message",
            str(root / "response.json"),
            "-c",
            'model_provider="openai"',
            "-c",
            'forced_login_method="chatgpt"',
            "-c",
            f'model_reasoning_effort="{self.reasoning_effort}"',
            "-c",
            'web_search="disabled"',
            "-c",
            "project_doc_max_bytes=0",
            "-c",
            "suppress_unstable_features_warning=true",
            "--disable",
            "shell_tool",
            "--disable",
            "apps",
            "--disable",
            "plugins",
            "--disable",
            "multi_agent",
            "--disable",
            "hooks",
            "--disable",
            "skill_search",
            "--disable",
            "browser_use",
            "--disable",
            "computer_use",
            "--disable",
            "image_generation",
            "--disable",
            "view_image",
            "--enable",
            "skip_host_skill_discovery",
            "-",
        )
