<div dir="rtl" markdown="1">

# راهنمای فارسی iranquant

`iranquant` یک کتابخانهٔ **ناهمگام (async)** برای دریافت دادهٔ بازار سرمایهٔ
ایران از **TSETMC** و **کدال** است. یک API همگام (sync) ساده نیز دارد که در
اسکریپت‌ها و محیط‌های Jupyter/Colab بدون خطای event loop کار می‌کند. خروجی
می‌تواند **Pandas** یا **Polars** باشد.

## نصب

```bash
pip install iranquant
```

برای نصب از مخزن و همراه با ابزار توسعه و مستندات:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,docs]"
```

نیازمندی‌ها: پایتون ۳٫۱۰ یا بالاتر، `httpx`، `pydantic>=2.7`، `tenacity`،
`jdatetime`، `pandas`، `polars` و `pyarrow`.

## جست‌وجوی نماد و کد نماد (insCode)

```python
import iranquant as iq

results = iq.search("فولاد")
full_name = results.iloc[0]["company_name"]

code = iq.get_inscode("فولاد")   # "46348559193224090"
```

نکته‌ها:

- حروف عربی (ي، ك، ة) و ارقام فارسی به‌صورت خودکار یکسان‌سازی می‌شوند.
- اگر ورودی عددی باشد (مثل `46348559193224090`)، بدون تماس شبکه‌ای برگردانده
  می‌شود.
- نمادهای دارای `flow` فعال (۱، ۲ یا ۴) در اولویت هستند و در صورت تساوی،
  جدیدترین `lastDate` انتخاب می‌شود.
- نتیجه‌ها در حافظهٔ همان فرایند cache می‌شوند. برای پاک‌سازی:

```python
from iranquant.market.symbol import clear_cache
clear_cache()
```

## تاریخچهٔ روزانه و تعدیل قیمت

```python
daily = iq.history("فولاد", adjust="all")
full = iq.full_history("فولاد")            # + دادهٔ حقیقی/حقوقی
clients = iq.client_type("فولاد")          # فقط حقیقی/حقوقی
```

ستون‌های `history`:

| ستون | نوع | معنی |
| --- | --- | --- |
| `date` | `datetime.date` | تاریخ میلادی |
| `jdate` | `str` | تاریخ شمسی `YYYY-MM-DD` |
| `open` | `float` | قیمت باز شدن |
| `high` | `float` | بیشترین قیمت |
| `low` | `float` | کمترین قیمت |
| `close` | `float` | آخرین قیمت معامله |
| `final_price` | `float` | قیمت پایانی |
| `prev_price` | `float` | قیمت پایانی روز قبل (تعدیل‌نشده) |
| `volume` | `float` | حجم معاملات |
| `value` | `float` | ارزش معاملات (ریال) |
| `trades` | `float` | تعداد معاملات |

حالت‌های تعدیل:

| مقدار `adjust` | رفتار |
| --- | --- |
| `"none"` | قیمت‌های خام TSETMC |
| `"all"` | تعدیل کامل عقب‌رو برای سود نقدی و افزایش سرمایه |
| `"capital_only"` | تعدیل عقب‌رو فقط برای افزایش سرمایه |

فرمول تعدیل کامل (`all`): برای هر روز `i` نسبت
`r[i] = prev_price[i+1] / final_price[i]` و ضریب تجمعی
`F[i] = F[i+1] * r[i]` محاسبه می‌شود (روز آخر ضریب ۱ دارد) و همهٔ ستون‌های
قیمتی در آن ضرب می‌شوند.

فرمول تعدیل افزایش سرمایه (`capital_only`): برای هر رویداد از
`numberOfShareOld` به `numberOfShareNew`، ردیف‌های **قبل از** تاریخ رویداد در
نسبت `old / new` ضرب می‌شوند.

```python
raw = iq.history("فولاد", adjust="none")
adj = iq.history("فولاد", adjust="all")
cap = iq.history("فولاد", adjust="capital_only")
```

## پول هوشمند و قدرت حرکت

```python
flow = iq.money_flow("فولاد")            # buy power + smart money
power = iq.movement_power("فولاد")       # امتیاز چندبازه‌ای

print(flow[["jdate", "buyer_power_ratio",
            "smart_money_entry", "smart_money_exit"]].tail())
print(power[["jdate", "er_5", "er_20", "er_60",
             "movement_power_score"]].tail())
```

### جریان پول (money flow)

ستون‌های محاسبه‌شده:

- `real_buy_per_capita` و `real_sell_per_capita`: سرانهٔ خرید/فروش حقیقی
  (ریال).
- `real_buy_per_capita_m` و `real_sell_per_capita_m`: همان مقدار به **میلیون
  تومان**.
- `buyer_power_ratio`: نسبت قدرت خریدار حقیقی به فروشنده حقیقی.
- `net_real_value`: جریان خالص پول حقیقی (ریال).
- `ownership_change_pct`: درصد تغییر مالکیت حقیقی نسبت به کل حجم.
- `close_vs_final_pct`: اختلاف آخرین قیمت با قیمت پایانی.
- `vol_ratio`: نسبت حجم به میانگین متحرک حجم.
- `smart_money_entry` و `smart_money_exit`: سیگنال‌های ورود/خروج پول هوشمند.

سیگنال ورود وقتی فعال می‌شود که هم‌زمان `vol_ratio >= 1.8`،
`buyer_power_ratio >= 1.5`، `net_real_value > 0` و `close >= final_price`
باشد. سیگنال خروج با `buyer_power_ratio <= 0.67`، `net_real_value < 0` و
`close <= final_price` فعال می‌شود.

### قدرت حرکت (movement power)

برای هر پنجرهٔ `w` نسبت کارایی علامت‌دار کافمن محاسبه می‌شود:

```text
er_w = sign(close - close.shift(w))
       * abs(close - close.shift(w))
       / rolling_sum(abs(close.diff()), w)
```

با سه پنجره، وزن‌ها `(0.5, 0.3, 0.2)` هستند؛ در غیر این صورت وزن‌ها مساوی‌اند.
امتیاز نهایی با یک ضریب حجم (بین ۰٫۵ تا ۲) و در صورت وجود، ضریب قدرت خریدار
(بین ۰٫۵ تا ۱٫۵) تعدیل می‌شود و در بازهٔ `[-100, +100]` قرار می‌گیرد.

```python
# نسخهٔ Polars
import polars as pl
power = iq.movement_power("فولاد", engine="polars")
strong = power.filter(pl.col("movement_power_score") > 50)
print(strong.select(["jdate", "movement_power_score"]).tail())
```

## فیلتر لحظه‌ای بازار (Screener)

```python
market = iq.market_watch()
screener = iq.MarketScreener(market)

buy_locked = screener.filter_buy_queue()
sell_locked = screener.filter_sell_queue()
surges = screener.filter_positive_tick_surge(min_diff_pct=1.5)

high_volume = screener.scan_market(
    lambda df: df["volume"] > df["volume"].median()
)
```

شرط‌ها:

- `filter_buy_queue()`: صف خرید، یعنی `close >= tmax` و حجم صف خرید (`zmd`)
  بزرگ‌تر از صفر.
- `filter_sell_queue()`: صف فروش، یعنی `close <= tmin` و حجم صف فروش (`zmo`)
  بزرگ‌تر از صفر.
- `filter_positive_tick_surge(min_diff_pct=1.5)`: آخرین قیمت حداقل ۱٫۵ درصد
  بالاتر از قیمت پایانی باشد.
- `scan_market(predicate)`: هر شرط سفارشی روی DataFrame پانداس.

نوع خروجی (Pandas یا Polars) با نوع ورودی یکسان است.

## استخراج جدول‌های کدال

```python
reports = iq.monthly_reports("فولاد")      # LetterType=58
letter = reports.Letters[0]
tables = iq.codal_report(letter)           # URLها خودکار مطلق می‌شوند

for name, table in tables.items():
    print(name, table.shape)               # «شیت - جدول»
```

- `LetterType=58` گزارش فعالیت ماهانه و `LetterType=6` صورت‌های مالی
  میان‌دوره‌ای است.
- داده‌ها از شیء `var datasource = {...}` صفحهٔ گزارش استخراج می‌شوند؛ بدون
  BeautifulSoup و بدون وابستگی به selectorهای HTML.
- سلول‌های نامرئی حذف می‌شوند، سطرهای سرستون تشخیص داده می‌شوند، ارقام فارسی
  به ASCII تبدیل می‌شوند، جداکنندهٔ هزارگان حذف می‌شود، `٫` به نقطه تبدیل
  می‌شود و پرانتز حسابداری `(1,200)` به `-1200.0` تبدیل می‌شود.
- ستون‌های تکراری پسوند `_2`، `_3` و کلیدهای جدول تکراری پسوند ` (2)` می‌گیرند.

### تحلیل خودکار فروش ماهانه

`iranquant` جدول استاندارد «تولید و فروش» را می‌شناسد و فروش ماه جاری،
تجمعی سال مالی، سال قبل و درصد رشد ماهانه/سالانه را برمی‌گرداند (به میلیون
ریال):

```python
import iranquant as iq

summary = iq.monthly_sales_summary("فولاد")
print(summary["period_end"])            # «1405-07-30»
print(summary["current_month_sales"])
print(summary["ytd_sales"])
print(summary["prev_ytd_sales"])
print(summary["mom_change_pct"])        # از گزارش ماه قبل
print(summary["yoy_change_pct"])
print(summary["summary_df"])            # فقط سطرهای جمع/مجموع
```

اگر جدول‌ها را از قبل دارید، از تحلیل‌گر خالص استفاده کنید:

```python
tables = iq.parse_codal_report(letter)   # نام دیگر iq.codal_report
summary = iq.analyze_monthly_sales(tables)
```

تحلیل‌گر هم Pandas و هم Polars را می‌پذیرد و `parse_codal_report` گزینهٔ
`engine="polars"` را نیز پشتیبانی می‌کند.

### امکانات CodalSearchResponse

```python
reports = iq.monthly_reports("فولاد")

print(len(reports), reports.total, reports.page)
for letter in reports:
    print(letter.TracingNo, letter.Title)

latest = reports[0]
frame = reports.to_dataframe()               # Pandas
frame_pl = reports.to_dataframe("polars")   # Polars
```

## پخش زندهٔ بازار

```python
import asyncio
from iranquant import MarketWatcher
from iranquant.stream import BUY_QUEUE_LOCKED, VOLUME_SPIKE

async def main() -> None:
    watcher = MarketWatcher()

    @watcher.on_event(BUY_QUEUE_LOCKED)
    def on_lock(event):
        print("صف خرید قفل شد:", event.symbol,
              event.data["buy_queue_volume"])

    @watcher.on_event(VOLUME_SPIKE)
    async def on_spike(event):
        print("جهش حجم:", event.symbol, event.data["delta"])

    async for event in watcher.watch(
        interval=3.0,
        symbols=["فولاد", "خودرو"],
        only_market_hours=True,     # شنبه تا چهارشنبه، ۹:۰۰ تا ۱۲:۳۰ تهران
    ):
        print(event.event_type, event.symbol)

asyncio.run(main())
```

رویدادها:

| رویداد | شرط |
| --- | --- |
| `BUY_QUEUE_LOCKED` | `close >= tmax` و حجم صف خرید مثبت |
| `BUY_QUEUE_BROKEN` | صف خرید قبلی باز شود |
| `SELL_QUEUE_LOCKED` | `close <= tmin` و حجم صف فروش مثبت |
| `SELL_QUEUE_BROKEN` | صف فروش قبلی باز شود |
| `VOLUME_SPIKE` | افزایش حجم هر پول `>= 1.8x` میانگین افزایش‌های قبلی |

برای بک‌تست بدون شبکه، از `watcher.process_snapshot(snapshot)` استفاده کنید
که همگام و قطعی است.

## Cache محلی Parquet

```python
import iranquant as iq
from iranquant.core.cache import ParquetCache

cache = ParquetCache()                     # ~/.cache/iranquant
combined = cache.update("fold-history",
                        iq.history("فولاد", adjust="all"))
print(combined.tail())
```

- مکان پیش‌فرض: `~/.cache/iranquant/` (قابل تغییر با متغیر محیطی
  `IRANQUANT_CACHE_DIR` یا آرگومان `directory`).
- `update` ردیف‌های جدید را ادغام می‌کند، رکورد تکراری هر تاریخ را با جدیدترین
  جایگزین می‌کند و خروجی را بر اساس تاریخ مرتب می‌کند.
- توابع بازار به‌صورت خودکار cache نمی‌نویسند؛ `ParquetCache` یک ابزار مستقل
  است تا کلیدها و سیاست نگهداری را خودتان کنترل کنید.

## خط فرمان (CLI)

با نصب بسته، فرمان `iranquant` ثبت می‌شود و خروجی را با جدول‌های رنگی Rich
نمایش می‌دهد:

```bash
iranquant search فولاد
iranquant history فولاد --days 15 --adjust all
iranquant screener --mode buy-queue --limit 20
iranquant screener --mode tick-surge
iranquant indices
iranquant codal فولاد
iranquant codal فولاد --parse-latest
```

گزینهٔ `codal --parse-latest` جدول‌های گزارش و خلاصهٔ فروش ماهانه (به میلیون
ریال، همراه با رشد ماهانه و سالانه) را چاپ می‌کند.

## شبکه، VPN و Split-Tunneling در ایران

دامنه‌های داخلی (`cdn.tsetmc.com`، `tsetmc.com`، `codal.ir` و
`search.codal.ir`) معمولاً وقتی از VPN عبور نکنند سریع‌تر و پایدارتر هستند.
`iranquant` بر همین اساس طراحی شده است.

!!! tip "متغیرهای پروکسی محیطی به‌صورت پیش‌فرض نادیده گرفته می‌شوند"
    `AsyncHTTPClient` به‌طور پیش‌فرض از `trust_env=False` استفاده می‌کند تا
    `HTTP_PROXY`/`HTTPS_PROXY` که VPN یا ابزارهای شرکتی تنظیم کرده‌اند،
    ترافیک دامنه‌های داخلی را از یک پورت HTTP راه دور عبور ندهد.

### گزینهٔ ۱: پروکسی صریح (فقط در صورت نیاز)

```python
import iranquant as iq

async with iq.AsyncIranQuant(proxy="http://127.0.0.1:9090") as client:
    df = await client.history("فولاد")
```

### گزینهٔ ۲: Split-Tunneling (پیشنهادی برای سرورها)

در کلاینت VPN خود ترافیک مقاصد داخلی را از تونل خارج کنید. برای نمونه در
WireGuard با `AllowedIPs = 0.0.0.0/0, ::/0` می‌توانید مسیرهای داخلی را با
`ip route add <domestic CIDR> via <local-gateway>` مستثنی کنید، یا در اپ VPN
گزینهٔ «split tunneling / bypass LAN and domestic sites» را فعال کنید.
حداقل این مقاصد را مستثنی کنید:

- `cdn.tsetmc.com`
- `tsetmc.com`
- `codal.ir` و `*.codal.ir`
- سرور DNS شما، اگر از طریق تونل کوئری می‌شود

### گزینهٔ ۳: اجرا روی سرور داخل ایران

اگر سرور شما داخل ایران است، `proxy=None` را نگه دارید و اتصال مستقیم برقرار
کنید. بررسی گواهی TLS ابتدا برای `*.tsetmc.com` و `*.codal.ir` انجام می‌شود.
در صورت شکست بررسی، اگر `allow_tls_fallback=True` باشد یک بار روی ترانسپورت
بدون بررسی گواهی تلاش دوباره می‌شود و نخستین بارگذاری هشدار `UserWarning`
می‌دهد. برای جلوگیری از این بازگشت، از
`IranQuantConfig(allow_tls_fallback=False)` یا
`IRANQUANT_ALLOW_TLS_FALLBACK=false` استفاده کنید.

!!! warning "ساعت بازار"
    جلسات TSETMC شنبه تا چهارشنبه، ۹:۰۰ تا ۱۲:۳۰ به وقت تهران است. برای جلوگیری
    از درخواست‌های بی‌مورد خارج از این بازه، از `only_market_hours=True` استفاده
    کنید.

## خطاها

```python
import iranquant as iq

try:
    df = iq.history("نماد_ناموجود")
except iq.SymbolNotFoundError:
    ...   # نماد پیدا نشد
except iq.NetworkError:
    ...   # خطای شبکه
except iq.ParseError:
    ...   # دادهٔ دریافتی قابل تجزیه نبود
```

همهٔ خطاها از `iranquant.IranQuantError` ارث‌بری می‌کنند.

</div>
