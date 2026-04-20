import json
import os
import shutil
import unittest
from unittest.mock import patch

from myscoop.uninstaller import UninstallError, uninstall_app


class UninstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = os.path.join(os.getcwd(), "testdata_uninstaller")
        shutil.rmtree(self.temp_dir, ignore_errors=True)
        os.makedirs(self.temp_dir, exist_ok=True)
        self.apps_dir = os.path.join(self.temp_dir, "apps")
        self.shims_dir = os.path.join(self.temp_dir, "shims")
        self.buckets_dir = os.path.join(self.temp_dir, "buckets")
        os.makedirs(self.apps_dir, exist_ok=True)
        os.makedirs(self.shims_dir, exist_ok=True)
        os.makedirs(self.buckets_dir, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_rejects_metadata_only_uninstall_without_external_path(self):
        app_dir = os.path.join(self.apps_dir, "demo", "1.0")
        os.makedirs(app_dir, exist_ok=True)
        with open(os.path.join(app_dir, "install.json"), "w", encoding="utf-8") as fh:
            json.dump({"name": "demo"}, fh)

        with patch("myscoop.uninstaller._run_native_uninstallers", return_value=False), patch(
            "myscoop.uninstaller._run_registry_uninstaller", return_value=False
        ):
            with self.assertRaises(UninstallError):
                uninstall_app(
                    "demo",
                    self.buckets_dir,
                    self.apps_dir,
                    self.shims_dir,
                    lambda _name: None,
                )

        self.assertTrue(os.path.isdir(os.path.join(self.apps_dir, "demo")))

    def test_removes_managed_payload_even_without_external_uninstaller(self):
        app_dir = os.path.join(self.apps_dir, "demo", "1.0")
        app_root = os.path.join(self.apps_dir, "demo")
        os.makedirs(app_dir, exist_ok=True)
        with open(os.path.join(app_dir, "demo.exe"), "wb") as fh:
            fh.write(b"payload")

        original_exists = os.path.exists

        def fake_exists(path):
            if os.path.normcase(path) == os.path.normcase(app_root):
                return False
            return original_exists(path)

        with patch("myscoop.uninstaller._run_native_uninstallers", return_value=False), patch(
            "myscoop.uninstaller._run_registry_uninstaller", return_value=False
        ), patch("myscoop.uninstaller._remove_app_root") as remove_root, patch(
            "myscoop.uninstaller.os.path.exists", side_effect=fake_exists
        ):
            result = uninstall_app(
                "demo",
                self.buckets_dir,
                self.apps_dir,
                self.shims_dir,
                lambda _name: None,
            )

        remove_root.assert_called_once_with(app_root)
        self.assertIn("uninstalled successfully", result["message"])


if __name__ == "__main__":
    unittest.main()
