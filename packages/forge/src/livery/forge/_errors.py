"""The exceptions every backend raises the same way."""

from __future__ import annotations


class ForgeError(Exception):
    """Raised when a forge operation fails.

    The message says what failed, in the words of the server that
    refused it. A caller that branches on the failure reads the
    attributes, never the message text.

    Attributes:
        status: The HTTP status code, or None when the server never
            answered. An unreachable server is a different decision for
            the caller than any code it could have returned.
        method: The HTTP method of the failing request, when known.
        endpoint: The endpoint of the failing request, when known.
        detail: The response body, verbatim.
    """

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        method: str = "",
        endpoint: str = "",
        detail: str = "",
    ) -> None:
        """Carry *message* plus the request facts a caller branches on."""
        super().__init__(message)
        self.status = status
        self.method = method
        self.endpoint = endpoint
        self.detail = detail


class RateLimited(ForgeError):
    """Raised when the forge refuses a request because its API budget is spent.

    Asking again before the budget returns gets the same answer. A
    caller that can wait (a watch that follows a pull request) sleeps
    until then and continues; one that cannot names the time.

    Attributes:
        reset_at: When the budget returns, as seconds since the epoch;
            None when the forge did not say.
    """

    def __init__(
        self,
        message: str,
        *,
        reset_at: float | None,
        status: int | None = None,
        method: str = "",
        endpoint: str = "",
        detail: str = "",
    ) -> None:
        """Carry *message*, the request facts, and when the budget returns."""
        super().__init__(
            message, status=status, method=method, endpoint=endpoint, detail=detail
        )
        self.reset_at = reset_at


class Unsupported(Exception):
    """Raised when a forge cannot honour a protocol operation.

    The message says why: a server version that predates the
    operation, or a capability the forge declines by name. Kept apart
    from livery.forge.ForgeError because retrying cannot change the
    answer: the forge is reachable, and the operation is one it does
    not offer. Probe with livery.forge.Forge.supports before relying
    on a capability-gated operation.
    """
