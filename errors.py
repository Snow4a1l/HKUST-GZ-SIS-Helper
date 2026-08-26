"""Expected safety and configuration failures."""


class EnrollClickerError(Exception):
    """Base class for expected application failures."""


class ConfigError(EnrollClickerError):
    """Configuration is missing, malformed, or unsafe."""


class SafetyStop(EnrollClickerError):
    """A page or action guard refused to proceed."""


class DuplicateClickPrevented(SafetyStop):
    """A second click was attempted in the same process."""

