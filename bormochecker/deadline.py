from __future__ import annotations

import time
from typing import Optional


class Deadline:
    """Сквозной объект контроля бюджета времени (T502 / [P01])."""

    def __init__(self, budget_seconds: float, start_time: Optional[float] = None) -> None:
        self.budget_seconds = max(0.0, float(budget_seconds))
        self.start_time = start_time if start_time is not None else time.monotonic()
        self.end_time = self.start_time + self.budget_seconds

    @classmethod
    def from_seconds(cls, budget_seconds: float) -> Deadline:
        return cls(budget_seconds)

    def remaining(self) -> float:
        """Оставшееся время в секундах (>= 0.0)."""
        return max(0.0, self.end_time - time.monotonic())

    def expired(self) -> bool:
        """Истёк ли дедлайн."""
        return time.monotonic() >= self.end_time

    def timeout(self, cap: Optional[float] = None) -> float:
        """Возвращает min(cap, remaining()). Если cap не задан, возвращает remaining()."""
        rem = self.remaining()
        if cap is None:
            return rem
        return min(float(cap), rem)

    def check(self) -> None:
        """Бросает TimeoutError, если дедлайн истёк."""
        if self.expired():
            raise TimeoutError("Бюджет времени исчерпан (Deadline expired)")
