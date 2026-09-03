"""Focused tests for the single-cell Hermes process boundary."""

from __future__ import annotations

import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import run_task_model


class RunTaskModelTests(unittest.TestCase):
    def test_default_command_skips_shell_wrapper_and_uses_python_backed_hermes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            wrapper_directory = root / "wrapper-bin"
            real_directory = root / "real-bin"
            wrapper_directory.mkdir()
            real_directory.mkdir()

            wrapper = wrapper_directory / "hermes"
            wrapper.write_text(
                "#!/usr/bin/env bash\nexec /path/to/real/hermes \"$@\"\n",
                encoding="utf-8",
            )
            wrapper.chmod(wrapper.stat().st_mode | stat.S_IXUSR)

            real_launcher = real_directory / "hermes"
            real_launcher.write_text("#!/bin/sh\n", encoding="utf-8")
            real_launcher.chmod(real_launcher.stat().st_mode | stat.S_IXUSR)
            hermes_python = real_directory / "python3"
            hermes_python.write_text("", encoding="utf-8")
            hermes_python.chmod(hermes_python.stat().st_mode | stat.S_IXUSR)

            path = os.pathsep.join((str(wrapper_directory), str(real_directory)))
            with patch.dict(os.environ, {"PATH": path}, clear=False):
                command = run_task_model._default_agent_command("aegis")

            self.assertEqual(command[0], str(hermes_python.resolve()))
            self.assertNotIn("bash", Path(command[0]).name)
            self.assertEqual(command[1], str(run_task_model.ROOT / "scripts" / "hermes_no_tools.py"))


if __name__ == "__main__":
    unittest.main()
