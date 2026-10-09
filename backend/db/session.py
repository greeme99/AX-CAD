import os
from collections.abc import Iterator
from functools import lru_cache
from pathlib import Path

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session


def load_env() -> None:
    """Fill os.environ from the repo-root .env without overriding (and never printing) values."""
    f = Path(__file__).resolve().parents[2] / ".env"
    if f.is_file():
        for line in f.read_text(encoding="utf-8").splitlines():
            k, sep, v = line.partition("=")
            if sep and not k.lstrip().startswith("#"):
                os.environ.setdefault(k.strip(), v.strip().strip("\"'"))


@lru_cache
def engine() -> Engine:
    load_env()
    url = os.environ["DATABASE_URL"]
    return create_engine(
        url.replace("postgresql://", "postgresql+psycopg://", 1), pool_pre_ping=True
    )


def get_db() -> Iterator[Session]:
    with Session(engine()) as db:  # uncommitted work is rolled back on close
        yield db
