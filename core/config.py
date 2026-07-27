import os
import json
import logging

CONFIG_FILE = "wble_config.json"
STATE_FILE = "wble_state.json"

DEFAULT_CONFIG = {
    "api_keys": {
        "openai": "",
        "groq": "",
        "gemini": "",
        "kimi": ""
    },
    "download_dir": os.path.join(os.getcwd(), "WBLE_Downloads"),
    "max_file_size_mb": 50,
    "scan_interval_hours": 1,
    "auto_start": False,
    "blacklisted_courses": [],
    "theme": "dark"
}

class ConfigManager:
    def __init__(self):
        self.config = self.load_config()
        self.state = self.load_state()
        
    def load_config(self):
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    # Merge with default to ensure all keys exist
                    for k, v in DEFAULT_CONFIG.items():
                        if k not in data:
                            data[k] = v
                    return data
            except Exception as e:
                logging.error(f"Failed to load config: {e}")
        return DEFAULT_CONFIG.copy()

    def save_config(self):
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(self.config, f, indent=4, ensure_ascii=False)

    def load_state(self):
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def save_state(self):
        with open(STATE_FILE, 'w', encoding='utf-8') as f:
            json.dump(self.state, f, indent=4, ensure_ascii=False)

    def get(self, key, default=None):
        return self.config.get(key, default)

    def set(self, key, value):
        self.config[key] = value
        self.save_config()

config_mgr = ConfigManager()
