"""Pydantic V2 models for the TSETMC JSON payloads.

Field names mirror the wire format so nothing is lost or renamed at the parsing
boundary.  Standardized column names are produced later by
:mod:`iranquant.market`.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

from iranquant.core.normalizer import normalize_persian


class TSETMCBaseModel(BaseModel):
    """Base model tolerant to extra, newly-added TSETMC fields.

    Every string field is passed through :func:`normalize_persian`, so Arabic
    variants of ``ک``/``ی`` and Persian/Arabic digits never reach user-facing
    frames or models.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    @field_validator("*", mode="before")
    @classmethod
    def _normalize_text(cls, value: object) -> object:
        if isinstance(value, str):
            return normalize_persian(value)
        return value


class InstrumentSearchItem(TSETMCBaseModel):
    insCode: str
    lVal18AFC: str
    lVal30: str = ""
    cIsin: str | None = None
    zTitad: float = 0.0
    flow: int = 1
    cgrValCot: str = ""
    lastDate: int | None = None
    faraDesc: str = ""


class InstrumentSearchResponse(TSETMCBaseModel):
    instrumentSearch: list[InstrumentSearchItem] = Field(default_factory=list)


class ClosingPriceDaily(TSETMCBaseModel):
    insCode: str
    dEven: int
    hEven: int = 0
    pClosing: float = 0.0
    iClose: bool = False
    yClose: bool = False
    pDrCotVal: float = 0.0
    zTotTran: float = 0.0
    qTotTran5J: float = 0.0
    qTotCap: float = 0.0
    priceChange: float = 0.0
    priceMin: float = 0.0
    priceMax: float = 0.0
    priceYesterday: float = 0.0
    priceFirst: float = 0.0
    last: bool = False
    id: int = 0


class ClosingPriceDailyResponse(TSETMCBaseModel):
    closingPriceDaily: list[ClosingPriceDaily] = Field(default_factory=list)


class ClientTypeRecord(TSETMCBaseModel):
    recDate: int
    insCode: str = ""
    buy_I_Volume: float = 0.0
    buy_N_Volume: float = 0.0
    buy_I_Value: float = 0.0
    buy_N_Value: float = 0.0
    buy_N_Count: int = 0
    buy_I_Count: int = 0
    sell_I_Volume: float = 0.0
    sell_N_Volume: float = 0.0
    sell_I_Value: float = 0.0
    sell_N_Value: float = 0.0
    sell_N_Count: int = 0
    sell_I_Count: int = 0


class ClientTypeResponse(TSETMCBaseModel):
    clientType: list[ClientTypeRecord] = Field(default_factory=list)


class InstrumentShareChange(TSETMCBaseModel):
    dEven: int
    numberOfShareNew: float = 0.0
    numberOfShareOld: float = 0.0


class InstrumentShareChangeResponse(TSETMCBaseModel):
    instrumentShareChange: list[InstrumentShareChange] = Field(default_factory=list)


class InstrumentDPS(TSETMCBaseModel):
    dEven: int
    priceAfterTax: float = 0.0


class InstrumentDPSResponse(TSETMCBaseModel):
    instrumentDPS: list[InstrumentDPS] = Field(default_factory=list)


class BestLimit(TSETMCBaseModel):
    n: int = 0
    qmd: float = 0.0
    zmd: float = 0.0
    pmd: float = 0.0
    pmo: float = 0.0
    zmo: float = 0.0
    qmo: float = 0.0


class MarketWatchItem(TSETMCBaseModel):
    insCode: str
    lva: str = ""
    lvc: str = ""
    eps: float | None = None
    pe: float | None = None
    pmd: float = 0.0
    pmo: float = 0.0
    pcl: float = 0.0
    pdv: float = 0.0
    pvc: float | None = None
    py: float = 0.0
    pmn: float = 0.0
    pmx: float = 0.0
    pf: float = 0.0
    ztt: float = 0.0
    # Current servers send ``qtj``; older ones sent ``qtt``.
    qtj: float | None = None
    qtt: float | None = None
    qtc: float = 0.0
    bv: float = 0.0
    # Current servers send ``pMax``/``pMin``; older ones sent ``ps``/``pi``.
    pMax: float | None = None
    pMin: float | None = None
    ps: float | None = None
    pi: float | None = None
    ztd: float = 0.0
    blDs: list[BestLimit] = Field(default_factory=list)

    @field_validator("pe", "eps", mode="before")
    @classmethod
    def _clean_numeric_placeholder(cls, value: object) -> object:
        if isinstance(value, str):
            text = value.strip().replace(",", "")
            try:
                return float(text)
            except ValueError:
                return None
        return value


class MarketWatchResponse(TSETMCBaseModel):
    marketwatch: list[MarketWatchItem] = Field(default_factory=list)


class ClosingPriceInfo(TSETMCBaseModel):
    insCode: str = ""
    blDs: list[BestLimit] = Field(default_factory=list)


class ClosingPriceInfoResponse(TSETMCBaseModel):
    closingPriceInfo: ClosingPriceInfo | None = None


class BestLimitItem(TSETMCBaseModel):
    """One level of ``GET /api/BestLimits/{insCode}``."""

    number: int = 0
    qTitMeDem: float = 0.0
    zOrdMeDem: float = 0.0
    pMeDem: float = 0.0
    pMeOf: float = 0.0
    zOrdMeOf: float = 0.0
    qTitMeOf: float = 0.0


class BestLimitResponse(TSETMCBaseModel):
    bestLimits: list[BestLimitItem] = Field(default_factory=list)


class TradeIntraDay(TSETMCBaseModel):
    """One intraday OHLCV bar from ``GET /api/Trade/GetTradeIntraDay/{insCode}``."""

    hEven: int = 0
    minPrice: float = 0.0
    maxPrice: float = 0.0
    openPrice: float = 0.0
    closePrice: float = 0.0
    volume: float = 0.0


class TradeIntraDayResponse(TSETMCBaseModel):
    tradeIntraDay: list[TradeIntraDay] = Field(default_factory=list)


class TradeHistoryResponse(TSETMCBaseModel):
    tradeHistory: list[dict[str, object]] = Field(default_factory=list)
