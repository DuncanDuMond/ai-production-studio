"""Exercise local-only secret bootstrap without printing credentials."""

import base64
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class GenerateEnvironmentTests(unittest.TestCase):
    def test_generates_consistent_private_secrets_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / ".env"
            command = [sys.executable, str(ROOT / "deploy/generate_env.py"), "--output", str(target)]
            first = subprocess.run(command, text=True, capture_output=True, check=False)
            self.assertEqual(first.returncode, 0, first.stderr)
            data = dict(
                line.split("=", 1) for line in target.read_text().splitlines()
                if line and not line.startswith("#")
            )
            self.assertEqual(data["STUDIO_ENABLE_LIVE"], "false")
            self.assertEqual(data["OPENAI_API_KEY"], "")
            self.assertEqual(data["PHOENIX_API_KEY"], "")
            self.assertEqual(data["STUDIO_LITELLM_API_KEY"], data["LITELLM_MASTER_KEY"])
            self.assertTrue(re.fullmatch(r"[0-9a-f]{64}", data["LANGFUSE_ENCRYPTION_KEY"]))
            self.assertEqual(
                base64.b64decode(data["LANGFUSE_BASIC_AUTH"]).decode(),
                data["LANGFUSE_PUBLIC_KEY"] + ":" + data["LANGFUSE_SECRET_KEY"],
            )
            self.assertNotIn(data["STUDIO_API_TOKEN"], first.stdout + first.stderr)
            original = target.read_bytes()
            second = subprocess.run(command, text=True, capture_output=True, check=False)
            self.assertNotEqual(second.returncode, 0)
            self.assertEqual(target.read_bytes(), original)
            self.assertIn("exists", second.stdout + second.stderr)


if __name__ == "__main__":
    unittest.main()
