"""Pydantic V2 models for Codal search results and datasource tables."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, Literal, overload

from pydantic import BaseModel, ConfigDict, Field, field_validator

from iranquant.core.normalizer import normalize_persian


class CodalBaseModel(BaseModel):
    """Tolerant Codal base model with Persian text normalization."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    @field_validator("*", mode="before")
    @classmethod
    def _normalize_text(cls, value: Any) -> Any:
        if isinstance(value, str):
            return normalize_persian(value)
        return value


class CodalLetter(CodalBaseModel):
    TracingNo: int | None = None
    Symbol: str = ""
    CompanyName: str = ""
    Title: str = ""
    LetterCode: str = ""
    SentDateTime: str = ""
    PublishDateTime: str = ""
    Url: str = ""
    HasHtml: bool = False
    HasExcel: bool = False
    ExcelUrl: str | None = None
    PdfUrl: str | None = None
    UnderSupervision: bool = False


class CodalSearchResponse(CodalBaseModel):
    """A page of Codal search results with Pythonic container behavior.

    The wire-format field names (``Total``, ``Page``, ``Letters``) are kept for
    compatibility, and lowercase aliases (``total``, ``page``, ``letters``) are
    provided for idiomatic code.  The object is also iterable, indexable and
    sized, and can be exported directly to a DataFrame.

    Examples:
        >>> response = iq.monthly_reports("فولاد")
        >>> len(response) == response.total
        True
        >>> response[0].Symbol
        'فولاد'
        >>> response.to_dataframe()[["Symbol", "TracingNo"]].head()
    """

    Total: int = 0
    Page: int = 1
    Letters: list[CodalLetter] = Field(default_factory=list)

    @property
    def total(self) -> int:
        """Total number of matching letters across all pages."""
        return self.Total

    @property
    def page(self) -> int:
        """One-based page number represented by this response."""
        return self.Page

    @property
    def letters(self) -> list[CodalLetter]:
        """The letters on this page (alias of ``Letters``)."""
        return self.Letters

    def __len__(self) -> int:
        """Return the number of letters on this page."""
        return len(self.Letters)

    def __iter__(self) -> Iterator[CodalLetter]:  # type: ignore[override]
        """Iterate over the letters on this page."""
        return iter(self.Letters)

    @overload
    def __getitem__(self, index: int) -> CodalLetter: ...

    @overload
    def __getitem__(self, index: slice) -> list[CodalLetter]: ...

    def __getitem__(self, index: int | slice) -> CodalLetter | list[CodalLetter]:
        """Return one letter or a slice of letters."""
        return self.Letters[index]

    def to_dataframe(
        self,
        engine: Literal["pandas", "polars"] = "pandas",
    ) -> Any:
        """Export the letters to a Pandas or Polars DataFrame.

        Args:
            engine: ``"pandas"`` (default) or ``"polars"``.

        Returns:
            One row per letter with the declared :class:`CodalLetter` fields as
            columns (including nested extra fields is intentionally avoided so
            the schema stays stable).

        Raises:
            ValueError: If ``engine`` is unsupported.

        Examples:
            >>> frame = iq.monthly_reports("فولاد").to_dataframe()
            >>> frame[["Symbol", "TracingNo", "Title"]].head()
        """

        columns = list(CodalLetter.model_fields)
        records = [letter.model_dump(include=set(columns)) for letter in self.Letters]
        if engine == "pandas":
            import pandas as pd

            return pd.DataFrame(records, columns=columns)
        if engine == "polars":
            import polars as pl

            if not records:
                return pl.DataFrame({column: [] for column in columns})
            return pl.DataFrame(records)
        raise ValueError(f"Unsupported dataframe engine: {engine!r}")


class CodalCell(CodalBaseModel):
    rowSequence: int = 0
    columnSequence: int = 0
    value: Any = None
    rowType: str | None = None
    cellGroupName: str | None = None
    isVisible: bool = True


class CodalTable(CodalBaseModel):
    title_Fa: str | None = None
    title_En: str | None = None
    cells: list[CodalCell] = Field(default_factory=list)


class CodalSheet(CodalBaseModel):
    title_Fa: str | None = None
    title_En: str | None = None
    tables: list[CodalTable] = Field(default_factory=list)


class CodalDatasource(CodalBaseModel):
    sheets: list[CodalSheet] = Field(default_factory=list)
