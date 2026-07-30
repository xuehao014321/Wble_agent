import json
import os
import sys
import tempfile
import unittest

from core.autostart import AUTOSTART_FLAG, build_autostart_command
from core.config import ConfigManager, config_mgr
from core.engine import (
    chunk_text_by_lines,
    diff_course_snapshots,
    enable_chrome_password_manager,
    is_valid_course,
    normalize_ics_calendar,
    safe_download_filename,
)
from core.security import protect_secret, unprotect_secret


class CoreBehaviorTests(unittest.TestCase):
    def test_chrome_password_manager_preferences_are_enabled(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            preferences_path = os.path.join(
                temp_dir, "Default", "Preferences"
            )
            os.makedirs(os.path.dirname(preferences_path))
            with open(preferences_path, "w", encoding="utf-8") as file:
                json.dump({"profile": {"name": "WBLE"}}, file)

            self.assertTrue(enable_chrome_password_manager(temp_dir))
            with open(preferences_path, encoding="utf-8") as file:
                preferences = json.load(file)

            self.assertTrue(preferences["credentials_enable_service"])
            self.assertTrue(
                preferences["profile"]["password_manager_enabled"]
            )
            self.assertEqual(preferences["profile"]["name"], "WBLE")

    def test_safe_download_filename(self):
        self.assertEqual(safe_download_filename("../../CON"), "_CON")
        self.assertEqual(safe_download_filename(r"..\L01?.pdf"), "L01_.pdf")

    def test_course_code_variants(self):
        previous_blacklist = config_mgr.config.get("blacklisted_courses", [])
        config_mgr.config["blacklisted_courses"] = []
        try:
            self.assertTrue(is_valid_course("UCCD2063 Artificial Intelligence"))
            self.assertTrue(is_valid_course("Course: MPU34012 Social Responsibility"))
            self.assertFalse(is_valid_course("Dashboard"))
        finally:
            config_mgr.config["blacklisted_courses"] = previous_blacklist

    def test_chunking_preserves_all_text(self):
        lines = [f"line-{index}-" + ("x" * 100) for index in range(300)]
        chunks = chunk_text_by_lines("\n".join(lines), max_chars=5000)
        self.assertGreater(len(chunks), 1)
        self.assertLessEqual(max(map(len, chunks)), 5000)
        self.assertEqual("\n".join(chunks), "\n".join(lines))

    def test_ics_timezone_validation_and_metadata(self):
        valid = (
            "BEGIN:VCALENDAR\nVERSION:2.0\nBEGIN:VEVENT\n"
            "SUMMARY:Test\n"
            "DTSTART;TZID=Asia/Kuala_Lumpur:20260810T120000\n"
            "DTEND;TZID=Asia/Kuala_Lumpur:20260810T130000\n"
            "END:VEVENT\nEND:VCALENDAR"
        )
        normalized = normalize_ics_calendar(valid)
        self.assertIsNotNone(normalized)
        self.assertIn("UID:", normalized)
        self.assertIn("DTSTAMP:", normalized)
        self.assertIn("X-WR-TIMEZONE:Asia/Kuala_Lumpur", normalized)

        invalid = valid.replace(
            "DTSTART;TZID=Asia/Kuala_Lumpur:20260810T120000",
            "DTSTART:20260810T040000Z",
        )
        self.assertIsNone(normalize_ics_calendar(invalid))

    def test_structured_snapshot_diff(self):
        old = {
            "sections": [{
                "id": "section-1",
                "title": "Week 1",
                "summary": "",
                "activities": [{
                    "id": "module-1",
                    "type": "assignment",
                    "text": "Assignment",
                    "links": [],
                }],
            }],
            "external_links": [],
        }
        new = json.loads(json.dumps(old))
        new["sections"][0]["activities"][0]["text"] = "Assignment due Friday"
        self.assertIn("修改活动 module-1", diff_course_snapshots(old, new))

    @unittest.skipUnless(sys.platform == "win32", "DPAPI is Windows-only")
    def test_dpapi_round_trip(self):
        secret = "unit-test-secret"
        encrypted = protect_secret(secret)
        self.assertTrue(encrypted.startswith("dpapi:"))
        self.assertNotEqual(encrypted, secret)
        self.assertEqual(unprotect_secret(encrypted), secret)

    @unittest.skipUnless(sys.platform == "win32", "DPAPI is Windows-only")
    def test_failed_dpapi_decryption_never_erases_ciphertext(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            previous_dir = os.getcwd()
            os.chdir(temp_dir)
            try:
                protected_value = "dpapi:not-valid-base64"
                with open("wble_config.json", "w", encoding="utf-8") as file:
                    json.dump({
                        "api_keys": {"openai": protected_value},
                        "serverchan_key": "",
                    }, file)

                manager = ConfigManager()
                self.assertEqual(manager.config["api_keys"]["openai"], "")
                manager.save_config()
                with open("wble_config.json", encoding="utf-8") as file:
                    saved = json.load(file)
                self.assertEqual(
                    saved["api_keys"]["openai"], protected_value
                )
            finally:
                os.chdir(previous_dir)

    def test_atomic_json_backup_and_recovery(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "state.json")
            ConfigManager._atomic_write_json(path, {"version": 1})
            ConfigManager._atomic_write_json(path, {"version": 2})
            with open(path, encoding="utf-8") as file:
                self.assertEqual(json.load(file)["version"], 2)
            with open(path + ".bak", encoding="utf-8") as file:
                self.assertEqual(json.load(file)["version"], 1)

            with open(path, "w", encoding="utf-8") as file:
                file.write("{broken")
            self.assertEqual(
                ConfigManager._load_json_with_backup(path)["version"], 1
            )

    def test_autostart_command_is_quoted_and_flagged(self):
        command = build_autostart_command()
        self.assertIn(AUTOSTART_FLAG, command)
        self.assertIn("main.py", command)


if __name__ == "__main__":
    unittest.main()
