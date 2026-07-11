"""Release pipeline error types."""


class GateError(Exception):
    """A release gate refused to let the pipeline proceed."""
