# Codal Reports

Codal report pages embed their tables as a single JavaScript object:

```html
<script>
  var datasource = {"sheets": [ ... ]};
</script>
```

`iranquant` extracts that object without HTML scraping, then reconstructs each
visible table into a DataFrame. No BeautifulSoup, no fragile CSS selectors.

## Searching announcements

```python
import iranquant as iq

# Everything for a symbol
everything = iq.codal_search("فولاد", letter_type=-1, page=1)

# Monthly activity reports (گزارش فعالیت ماهانه)
monthly = iq.monthly_reports("فولاد")

# Interim financial statements
statements = iq.financial_statements("فولاد")

print(monthly.Total, len(monthly.Letters))
letter = monthly.Letters[0]
print(letter.Title, letter.PublishDateTime)
print(letter.Url)          # absolute URL
```

`LetterType=58` selects monthly activity reports and `LetterType=6` selects
interim financial statements. Every returned `CodalLetter` has its `Url`,
`ExcelUrl` and `PdfUrl` resolved to absolute links.

Search parameters:

| Argument | Default | Meaning |
| --- | --- | --- |
| `symbol` | `None` | Persian ticker filter |
| `letter_type` | `-1` | Codal letter type (`58`, `6`, …) |
| `category` | `-1` | Codal category |
| `page` | `1` | One-based page |
| `audited` | `True` | Include audited reports |
| `not_audited` | `True` | Include unaudited reports |
| `mains` | `True` | Include parent-company reports |
| `childs` | `True` | Include subsidiary reports |

## Extracting tables from a report

```python
letter = iq.monthly_reports("فولاد").Letters[0]
tables = iq.codal_report(letter)          # or a URL string
```

The return value is a dictionary keyed by `"<sheet title> - <table title>"`:

```python
for name, table in tables.items():
    print(name, table.shape)
```

You can also work in two explicit steps:

```python
from iranquant.codal import extract_datasource_json, parse_codal_tables

html = "<script>var datasource = {\"sheets\": []};</script>"
datasource = extract_datasource_json(html)
tables = parse_codal_tables(datasource, engine="pandas")
```

### How values are cleaned

- Invisible cells (`isVisible=false`) are dropped.
- Header rows are detected from `rowType`/`cellGroupName` hints or a text-only
  first row; multi-row headers are joined with `" - "`.
- Persian/Arabic digits become ASCII.
- Thousands separators (`٬`, `,`) are removed and `٫` becomes `.`.
- Accounting parentheses become negative numbers: `(1,200)` → `-1200.0`.
- Unicode minus (`−`) becomes `-`.
- Columns with mixed text and numbers are normalized to strings; otherwise
  numeric cells become `float`.
- Duplicate column labels receive `_2`, `_3`, … suffixes; duplicate table keys
  receive `" (2)"`, `" (3)"`, ….

## Automated monthly sales analysis

`iranquant` ships a built-in analyzer for the standard
`گزارش فعالیت ماهانه - تولید و فروش` table. It locates the current-month,
year-to-date and prior-year blocks, prefers the final `جمع` row (falling back
to summing `جمع فروش داخلی`, `جمع فروش صادراتی` and
`جمع درآمد ارائه خدمات`) and computes MoM/YoY growth:

```python
import iranquant as iq

summary = iq.monthly_sales_summary("فولاد")

print(summary["period_end"])            # "1405-07-30"
print(summary["current_month_sales"])   # million Rials
print(summary["ytd_sales"])
print(summary["prev_ytd_sales"])
print(summary["mom_change_pct"])        # from the previous monthly letter
print(summary["yoy_change_pct"])
print(summary["summary_df"])            # only the جمع/مجموع rows
```

`monthly_sales_summary` fetches both the latest report and the previous distinct
monthly period, so `mom_change_pct` is a true month-over-month comparison rather
than a within-report guess. Values are reported in **million Rials**, the unit
printed by Codal.

If you already have tables in hand, call the pure analyzer:

```python
tables = iq.parse_codal_report(letter)   # alias of iq.codal_report
summary = iq.analyze_monthly_sales(tables)
```

The analyzer accepts Pandas or Polars tables:

```python
tables = iq.parse_codal_report(letter, engine="polars")
summary = iq.analyze_monthly_sales(tables)
```

### CodalSearchResponse container behavior

Search results are sized, iterable and indexable, expose lowercase aliases and
can be exported to either dataframe engine:

```python
reports = iq.monthly_reports("فولاد")

print(len(reports), reports.total, reports.page)
for letter in reports:
    print(letter.TracingNo, letter.Title)

latest = reports[0]
frame = reports.to_dataframe()                 # pandas
frame_pl = reports.to_dataframe("polars")     # polars
```

### Manual extraction

Because sheet and column titles vary between issuers, you can still match them
defensively:

```python
import pandas as pd
import iranquant as iq

reports = iq.monthly_reports("فولاد")
if not reports.Letters:
    raise SystemExit("no monthly reports found")

latest = reports.Letters[0]
tables = iq.codal_report(latest)

# Find the first table whose title mentions sales/production.
candidates = {
    name: frame
    for name, frame in tables.items()
    if any(word in name for word in ("فروش", "تولید", "درآمد"))
}
if not candidates:
    raise SystemExit("no sales table found in this report")

name, frame = next(iter(candidates.items()))
print("using table:", name)

# Sum every column that is fully numeric after coercion.
numeric = frame.apply(pd.to_numeric, errors="coerce")
totals = numeric.sum(numeric_only=True)
print(totals)
```

To accumulate history across months, loop over `reports.Letters`, parse each
report and keep the total you need:

```python
rows = []
for letter in reports.Letters:
    tables = iq.codal_report(letter)
    for name, frame in tables.items():
        if "فروش" not in name:
            continue
        numeric = frame.apply(pd.to_numeric, errors="coerce")
        totals = numeric.sum(numeric_only=True)
        row = {"date": letter.PublishDateTime, "letter": letter.Title, **totals.to_dict()}
        rows.append(row)

history = pd.DataFrame(rows).sort_values("date")
print(history.tail())
```

!!! tip
    Use `engine="polars"` for `codal_report` and `parse_codal_tables` when you
    plan to aggregate large multi-year report sets; the reconstruction and
    cleaning rules are identical.

## Error handling

```python
from iranquant import NetworkError, ParseError

try:
    tables = iq.codal_report("/Report/123")
except NetworkError:
    ...   # report page unreachable
except ParseError:
    ...   # no `var datasource` block or malformed JSON
```
