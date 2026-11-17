"""Local packaging tests. No Vercel/Render API, Firebase or OpenAI calls."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = {"index.html", "style.css", "auth-client.js", "script.js", "data-manager.js"}


class DeploymentTests(unittest.TestCase):
    def test_static_allowlist_and_fixed_same_origin_proxy(self):
        with tempfile.TemporaryDirectory(prefix="m12-deploy-test-") as folder:
            root = Path(folder)
            (root / "tools").mkdir()
            shutil.copy(ROOT / "tools/build_frontend.mjs", root / "tools/build_frontend.mjs")
            shutil.copy(ROOT / "vercel.json", root / "vercel.json")
            for file in PUBLIC:
                shutil.copy(ROOT / file, root / file)
            (root / ".env").write_text("FAKE_SECRET=never-publish-this-test-marker", encoding="utf-8")
            (root / "firebase-service-account.json").write_text("fake-test-only", encoding="utf-8")
            env = dict(os.environ, BACKEND_ORIGIN="https://mock-backend.invalid")
            configure = subprocess.run(["node", "tools/build_frontend.mjs", "--configure"], cwd=root, env=env, capture_output=True)
            self.assertEqual(configure.returncode, 0)
            build = subprocess.run(["node", "tools/build_frontend.mjs"], cwd=root, env=env, capture_output=True)
            self.assertEqual(build.returncode, 0)
            self.assertEqual({p.name for p in (root / "dist").iterdir()}, PUBLIC)
            for p in (root / "dist").iterdir():
                self.assertNotIn("never-publish-this-test-marker", p.read_text(encoding="utf-8"))
            config = json.loads((root / "vercel.json").read_text())
            self.assertEqual(config["rewrites"][0], {"source": "/api/:path*", "destination": "https://mock-backend.invalid/api/:path*"})
            (root / "dist/.env").write_text("fake-test-only")
            rejected = subprocess.run(["node", "tools/build_frontend.mjs"], cwd=root, env=env, capture_output=True)
            self.assertNotEqual(rejected.returncode, 0)

    def test_backend_origin_not_user_controlled_path_or_http(self):
        script = ROOT / "tools/build_frontend.mjs"
        for origin in ["http://backend.invalid", "https://backend.invalid/path", "https://user:password@backend.invalid", "https://backend.invalid/?secret=x"]:
            result = subprocess.run(["node", str(script)], cwd=ROOT, env=dict(os.environ, BACKEND_ORIGIN=origin), capture_output=True)
            self.assertNotEqual(result.returncode, 0)

    def test_frontend_never_embeds_secrets_or_direct_backend_credentials(self):
        for name in PUBLIC:
            text = (ROOT / name).read_text(encoding="utf-8")
            for marker in ["OPENAI_API_KEY", "FIREBASE_SERVICE_ACCOUNT_JSON", "EVAL_PASSWORD_HASH", "AUTH_SECRET"]:
                self.assertNotIn(marker, text)
        config = (ROOT / "render.yaml").read_text(encoding="utf-8")
        self.assertIn("--host 0.0.0.0 --port $PORT", config)
        self.assertNotIn("upload_data", config)
        self.assertIn("sync: false", config)


if __name__ == "__main__":
    unittest.main()
