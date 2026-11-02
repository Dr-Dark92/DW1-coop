class WorkbenchError(Exception):
    """An error safe to present to the user."""


class Cancelled(WorkbenchError):
    pass


class DiscFormatError(WorkbenchError):
    pass


class ValidationError(WorkbenchError):
    pass
