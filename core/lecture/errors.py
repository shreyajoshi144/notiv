"""Domain errors. PermanentError = retrying cannot help (bad input, missing
dependency); anything else raised inside a stage is treated as transient."""


class NotivError(Exception):
    pass


class NotFoundError(NotivError):
    """Missing OR not visible to the caller — deliberately indistinguishable
    so ids can't be probed for existence."""


class ValidationError(NotivError):
    pass


class PermanentError(NotivError):
    pass


class NotReadyError(NotivError):
    """The lecture exists and is visible to the caller but isn't processed yet
    (or failed). `info` carries state/stage/progress/error for clients."""

    def __init__(self, message: str, info: dict):
        super().__init__(message)
        self.info = info


class RateLimitError(NotivError):
    """Too many requests; `retry_after` seconds until the caller may retry."""
    http_status = 429

    def __init__(self, message: str, retry_after: int):
        super().__init__(message)
        self.retry_after = retry_after


class LimiterUnavailable(RateLimitError):
    """The shared rate-limit store is down and NOTIV_RATELIMIT_ON_ERROR=deny: requests are refused (503), not waved through."""
    http_status = 503


class ConflictError(NotivError):
    """The request is valid but conflicts with current state (e.g. item already answered); `code` is machine-readable."""

    def __init__(self, message: str, code: str):
        super().__init__(message)
        self.code = code


class QuotaError(NotivError):
    """A per-user/workspace quota would be exceeded (HTTP 429/413 at the edge). `quota` names which one."""

    def __init__(self, message: str, quota: str, limit=None):
        super().__init__(message)
        self.quota, self.limit = quota, limit
