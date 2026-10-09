from typing import Any


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(code, message, status)
        self.status, self.code, self.message = status, code, message


def body(data: Any = None, error: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"success": error is None, "data": data, "error": error}


def pick(obj: Any, *names: str) -> dict[str, Any]:
    return {n: getattr(obj, n) for n in names}
