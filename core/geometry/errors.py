from typing import Any


class GeomError(Exception):
    """Kernel/sketch failure with a stable API code. Args are positional so it pickles across workers."""

    def __init__(
        self, code: str, message: str, http: int = 422, details: dict[str, Any] | None = None
    ):
        super().__init__(code, message, http, details)
        self.code, self.message, self.http, self.details = code, message, http, details
