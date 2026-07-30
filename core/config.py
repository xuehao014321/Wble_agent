import os
import json
import logging
import copy
import shutil

from core.security import (
    SECRET_PREFIX,
    SecretDecryptionError,
    protect_secret,
    unprotect_secret,
)

CONFIG_FILE = "wble_config.json"
STATE_FILE = "wble_state.json"

DEFAULT_CONFIG = {
    "api_keys": {
        "openai": "",
        "groq": "",
        "gemini": "",
        "kimi": "",
    },
    "serverchan_key": "",
    "download_dir": os.path.join(
        os.path.expanduser("~"), "Downloads", "WBLE_Downloads"
    ),
    "max_file_size_mb": 50,
    "scan_interval_hours": 1,
    "scan_interval_str": "30 minutes",
    "auto_start": False,
    "dashboard_url": "",
    "dashboard_targets": [],
    "dashboard_targets_version": 0,
    "available_courses": [],
    "last_scan_report": {},
    "password_prompt_handled": False,
    "blacklisted_courses": [],
    "blacklisted_course_keys": [],
    "theme": "dark"
}

class ConfigManager:
    def __init__(self):
        self._protected_secrets = {}
        self._failed_secrets = set()
        self.config = self.load_config()
        self.state = self.load_state()
        if not self._failed_secrets:
            try:
                # Migrate legacy plaintext secrets to DPAPI on first launch.
                self.save_config()
            except Exception:
                logging.exception("Unable to persist encrypted WBLE configuration.")
        
    def load_config(self):
        data = self._load_json_with_backup(CONFIG_FILE)
        if not isinstance(data, dict):
            return copy.deepcopy(DEFAULT_CONFIG)

        merged = copy.deepcopy(DEFAULT_CONFIG)
        merged.update(data)
        merged_keys = copy.deepcopy(DEFAULT_CONFIG["api_keys"])
        merged_keys.update(data.get("api_keys", {}))
        merged["api_keys"] = {
            key: self._load_secret(f"api_keys.{key}", value)
            for key, value in merged_keys.items()
        }
        merged["serverchan_key"] = self._load_secret(
            "serverchan_key", data.get("serverchan_key", "")
        )
        return merged

    def save_config(self):
        data = copy.deepcopy(self.config)
        data["api_keys"] = {
            key: self._secret_for_disk(f"api_keys.{key}", value)
            for key, value in data.get("api_keys", {}).items()
        }
        data["serverchan_key"] = self._secret_for_disk(
            "serverchan_key", data.get("serverchan_key", "")
        )
        backup_existing = not self._disk_config_has_plaintext_secrets()
        self._atomic_write_json(
            CONFIG_FILE,
            data,
            backup_existing=backup_existing,
        )
        if not backup_existing:
            try:
                os.remove(f"{CONFIG_FILE}.bak")
            except FileNotFoundError:
                pass

    def _load_secret(self, key, value):
        value = str(value or "")
        if value.startswith(SECRET_PREFIX):
            self._protected_secrets[key] = value
        try:
            return unprotect_secret(value)
        except SecretDecryptionError:
            self._failed_secrets.add(key)
            return ""

    def _secret_for_disk(self, key, value):
        value = str(value or "")
        if key in self._failed_secrets and not value:
            # Never destroy ciphertext merely because DPAPI was temporarily
            # unavailable (different Windows token, damaged profile, etc.).
            return self._protected_secrets.get(key, "")
        protected = protect_secret(value)
        self._failed_secrets.discard(key)
        if protected.startswith(SECRET_PREFIX):
            self._protected_secrets[key] = protected
        else:
            self._protected_secrets.pop(key, None)
        return protected

    def load_state(self):
        data = self._load_json_with_backup(STATE_FILE)
        return data if isinstance(data, dict) else {}

    def save_state(self):
        self._atomic_write_json(STATE_FILE, self.state)

    @staticmethod
    def _load_json_with_backup(path):
        for candidate in (path, f"{path}.bak"):
            if not os.path.exists(candidate):
                continue
            try:
                with open(candidate, "r", encoding="utf-8") as file:
                    data = json.load(file)
                if candidate.endswith(".bak"):
                    logging.warning("Recovered WBLE data from backup: %s", candidate)
                return data
            except (OSError, json.JSONDecodeError) as error:
                logging.error("Failed to load %s: %s", candidate, error)
        return None

    def _disk_config_has_plaintext_secrets(self):
        if not os.path.exists(CONFIG_FILE):
            return False
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as file:
                disk_data = json.load(file)
        except (OSError, json.JSONDecodeError):
            return False

        values = list(disk_data.get("api_keys", {}).values())
        values.append(disk_data.get("serverchan_key", ""))
        return any(
            value and not str(value).startswith(SECRET_PREFIX)
            for value in values
        )

    @staticmethod
    def _atomic_write_json(path, data, backup_existing=True):
        temp_path = f"{path}.{os.getpid()}.tmp"
        backup_path = f"{path}.bak"
        try:
            with open(temp_path, "w", encoding="utf-8") as file:
                json.dump(data, file, indent=4, ensure_ascii=False)
                file.flush()
                os.fsync(file.fileno())

            if backup_existing and os.path.exists(path):
                try:
                    with open(path, "r", encoding="utf-8") as current_file:
                        json.load(current_file)
                    shutil.copy2(path, backup_path)
                except (OSError, json.JSONDecodeError):
                    pass

            os.replace(temp_path, path)
        finally:
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass

    def get(self, key, default=None):
        return self.config.get(key, default)

    def set(self, key, value):
        self.config[key] = value
        self.save_config()

    def update(self, values):
        self.config.update(values)
        self.save_config()

config_mgr = ConfigManager()
