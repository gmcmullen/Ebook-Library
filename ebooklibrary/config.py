"""Runtime configuration: paths, concurrency and HTTP politeness settings."""
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Set

SUPPORTED_EXTENSIONS: Set[str] = {
    '.pdf', '.epub', '.mobi', '.azw3', '.azw',
    '.lit', '.fb2', '.txt', '.rtf', '.doc', '.docx',
}


@dataclass
class Config:
    """Everything the scanner needs to know about where things live and how fast to go."""

    base_dir: Path

    # Concurrency
    num_workers: int = field(default_factory=lambda: min(32, (os.cpu_count() or 1) * 2))
    batch_size: int = 10

    # HTTP politeness. Google Books rate-limits unauthenticated traffic aggressively,
    # so the default interval is deliberately conservative and adapts upward on 429.
    min_request_interval: float = 0.5
    max_retries: int = 4
    retry_delay: float = 3.0
    request_timeout: float = 10.0

    # Cooldown applied to a host after its retries are exhausted, instead of
    # disabling that host for the whole run.
    host_cooldown: float = 120.0

    # Optional Google Books API key (env: GOOGLE_BOOKS_API_KEY). Supplying one
    # lifts the per-IP quota that otherwise starves large libraries of subjects.
    google_books_api_key: str = field(
        default_factory=lambda: os.environ.get("GOOGLE_BOOKS_API_KEY", "")
    )

    # Save books.json every N books during long resumable operations.
    save_interval: int = 25

    def __post_init__(self):
        self.base_dir = Path(self.base_dir).expanduser().resolve()

    @property
    def library_dir(self) -> Path:
        return self.base_dir / "Library"

    @property
    def covers_dir(self) -> Path:
        return self.library_dir / "covers"

    @property
    def books_file(self) -> Path:
        return self.library_dir / "books.json"

    @property
    def html_file(self) -> Path:
        return self.library_dir / "books.html"

    def ensure_dirs(self) -> None:
        self.library_dir.mkdir(parents=True, exist_ok=True)
        self.covers_dir.mkdir(parents=True, exist_ok=True)
