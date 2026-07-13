from __future__ import annotations

import collections
from pathlib import Path
from typing import Generic, Hashable, TypeVar


K = TypeVar("K", bound=Hashable)
V = TypeVar("V")


class LruCache(Generic[K, V]):
    def __init__(self, capacity: int) -> None:
        self.capacity = max(1, int(capacity))
        self._values: collections.OrderedDict[K, V] = collections.OrderedDict()

    def get(self, key: K) -> V | None:
        value = self._values.get(key)
        if value is not None:
            self._values.move_to_end(key)
        return value

    def put(self, key: K, value: V) -> None:
        self._values[key] = value
        self._values.move_to_end(key)
        while len(self._values) > self.capacity:
            self._values.popitem(last=False)

    def clear(self) -> None:
        self._values.clear()


def file_fingerprint(path: str | Path | None) -> tuple[str, int, int]:
    if not path:
        return "", 0, 0
    value = Path(path).expanduser()
    try:
        stat = value.stat()
    except OSError:
        return str(value), 0, 0
    return str(value.resolve()), stat.st_mtime_ns, stat.st_size

