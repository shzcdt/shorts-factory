"""Configuration loading from YAML."""

from pathlib import Path

import yaml

DEFAULT_CONFIG_PATH = Path("config.yaml")


class Config:
    """Typed wrapper around the YAML configuration file."""

    def __init__(self, data: dict) -> None:
        self.data = data
        self.paths = data.get("paths", {})
        self.video = data.get("video", {})
        self.logging = data.get("logging", {})
        self.review = data.get("review", {})

    @classmethod
    def load(cls, path: Path | str = DEFAULT_CONFIG_PATH) -> "Config":
        """Load configuration from a YAML file.

        Args:
            path: Path to the configuration file.

        Returns:
            Parsed Config instance.
        """
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return cls(data)

    def get_path(self, key: str) -> Path:
        """Return a configured path by key, falling back to the key itself."""
        return Path(self.paths.get(key, key))

    def ensure_dirs(self) -> None:
        """Create all working directories referenced in the configuration."""
        for key in ("inbox", "clips", "review", "published", "logs"):
            self.get_path(key).mkdir(parents=True, exist_ok=True)
        self.get_path("db").parent.mkdir(parents=True, exist_ok=True)
