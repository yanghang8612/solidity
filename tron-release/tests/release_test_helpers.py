"""Shared test doubles for the release pipeline test suite."""


class FakeRun:
    """Maps an exact argv tuple to canned stdout."""

    def __init__(self, table):
        self.table = {tuple(k): v for k, v in table.items()}
        self.calls = []

    def __call__(self, argv):
        self.calls.append(list(argv))
        key = tuple(argv)
        if key not in self.table:
            raise AssertionError(f"unexpected command: {argv}")
        result = self.table[key]
        if isinstance(result, Exception):
            raise result
        return result
