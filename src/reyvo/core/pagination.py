from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class Pagination:
    limit: int = 50
    offset: int = 0

    @property
    def capped_limit(self) -> int:
        return max(1, min(self.limit, 100))
