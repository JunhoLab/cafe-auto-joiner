class JoinAutomationError(Exception):
    """Base exception for join automation failures."""


class StepExecutionError(JoinAutomationError):
    """Raised when a specific automation step fails."""


class ElementNotFoundError(JoinAutomationError):
    """Raised when a target element cannot be found."""


class CaptchaResolutionError(JoinAutomationError):
    """Raised when captcha exists but cannot be solved."""


class PermanentCaptchaError(CaptchaResolutionError):
    """Raised when retrying the captcha request will not help."""
