"""Контракт отчёта: общий язык конвейера, веб-формы и замеров.

Меняешь поле — предупреди команду: на него завязаны все три части.
"""

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, model_validator

MAX_TEXT_CHARS = 10_000


class CheckMode(str, Enum):
    local_frp = "local_frp"               # Только предзагруженные файлы + локальная БД SQLite FTS5 (0 ₽)
    education_searxng = "education"       # Локальная БД FTS5 + локальный SearXNG по доменам РФ (0 ₽)
    journalism_full = "journalism_full"   # Полный каскад включая платный Yandex Search (только для редакции)


class Label(str, Enum):
    confirmed = "confirmed"  # Подтверждено · зелёный: релевантный источник или проверенный расчёт
    clarify = "clarify"  # Уточнить · жёлтый: неоднозначность, мало подтверждения, конфликт источников
    error = "error"  # Ошибка · красный: доказанное опровержение, ошибка данных, логики, арифметики
    opinion = "opinion"  # Оценка · синий: мнение, реклама, прогноз; фактическое ядро — отдельной меткой
    editorial = "editorial"  # Редакционные данные · фиолетовый: свой источник, оригинал не передан


class Source(BaseModel):
    url: str
    quote: str = Field(description="Фрагмент источника, который поддерживает именно этот вывод")
    kind: str | None = Field(
        default=None,
        description="Категория источника: official, media, ledger, user_attachment, reference, other",
    )
    location: str | None = Field(
        default=None,
        description="Локация цитаты в документе (страница, абзац или символьное смещение)",
    )


class Claim(BaseModel):
    start: int | None = Field(default=None, ge=0, description="Позиция в исходном тексте, символы")
    end: int | None = Field(default=None, ge=0, description="Конечная позиция в исходном тексте, символы")
    text: str = Field(description="Ровно text[start:end] исходника (или текст утверждения без привязки)")
    label: Label
    explanation: str
    sources: list[Source] = []
    suggestion: str | None = Field(None, description="Предлагаемая правка; факты в ней не менять")
    origin: Literal["stated", "unclear"] | None = Field(
        None,
        description="Редакционное происхождение фрагмента, независимо от цвета: stated — источник указан, "
        "unclear — похоже на собственные сведения редакции, происхождение уточнить. "
        "У editorial обязательно; у clarify/error — подпись «редакционный источник»",
    )
    kind: str | None = Field(None, description="Тип утверждения: web_checkable, opinion, editorial, etc.")
    sources_unavailable: bool = Field(False, description="Признак недоступности источников (сбой провайдеров поиска)")


    @model_validator(mode="after")
    def _editorial_has_origin(self):
        if self.label is Label.editorial and self.origin is None:
            raise ValueError("фиолетовая метка без origin: источник указан (stated) или происхождение уточнить (unclear)?")
        return self


class Costs(BaseModel):
    """Таблица расходов. None = неизвестно, и это НЕ ноль (требование ТЗ)."""

    provider: str | None = None
    model: str | None = None
    settings: dict = {}
    started_at: datetime
    duration_s: float | None = None
    model_seconds: float = 0.0  # время на вызовы модели; для своей GPU это и есть цена
    model_requests: int = 0
    retries: int = 0
    input_tokens: int | None = 0
    cached_input_tokens: int | None = 0
    output_tokens: int | None = 0
    reasoning_tokens: int | None = 0
    search_requests: int = 0
    free_search_requests: int = 0
    paid_search_requests: int = 0
    cache_hits: int = 0
    local_fts_hits: int = 0
    fetch_requests: int = 0
    http_requests_by_provider: dict[str, int] = Field(default_factory=dict)
    model_cost_usd: float | None = None
    tools_cost_usd: float | None = None
    total_cost_usd: float | None = None


class CheckRequest(BaseModel):
    text: str = Field(
        min_length=1,
        max_length=MAX_TEXT_CHARS,
        description="Текст статьи или новости для проверки (до 10 000 символов)",
        examples=["Минпросвещения утвердило новый график каникул на 2026/2027 учебный год."],
    )
    note: str = Field(
        "",
        description="Служебное пояснение: источники редакции, момент проверки",
        examples=["Официальный релиз"],
    )
    mode: CheckMode = Field(
        default=CheckMode.education_searxng,
        description="Режим проверки: local_frp (локальный 0 ₽), education (SearXNG 0 ₽), journalism_full (полный каскад)",
    )
    reference_datetime: datetime | None = Field(
        default=None,
        description="Точка отсчёта времени для проверки относительных дат (по умолчанию текущий момент)",
    )



class Report(BaseModel):
    status: Literal["ok", "partial", "failed"]
    summary: str
    text: str = Field(description="Исходник без изменений")
    claims: list[Claim]
    limitations: list[str] = []
    costs: Costs
    reference_datetime: datetime | None = None
    claim_failures: dict[str, str] = Field(
        default_factory=dict,
        description="Сбои провайдеров поиска и загрузки по утверждениям",
    )

    @model_validator(mode="after")
    def _spans_match_text(self):
        for c in self.claims:
            if c.start is not None and c.end is not None:
                if self.text[c.start : c.end] != c.text:
                    raise ValueError(f"span {c.start}:{c.end} не совпадает с текстом утверждения: {c.text!r}")
        return self
