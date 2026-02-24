# Vasco — Component Sourcing Agent

## Project Overview
Vasco automates electronic component sourcing. Claude Code acts as the orchestrator, invoking navigator modules that return JSON results to stdout.

## Navigator Invocation
Each navigator is a standalone Python module invoked via `python -m`:

```bash
# LCSC — official API, requires .env with LCSC_API_KEY + LCSC_API_SECRET
python -m vasco.navigators.lcsc search "<keyword>"
python -m vasco.navigators.lcsc search "<keyword>" --in-stock --exact
python -m vasco.navigators.lcsc details "<LCSC code e.g. C15742>"
python -m vasco.navigators.lcsc categories
python -m vasco.navigators.lcsc category <category_id> --in-stock

# jlcsearch — fallback, no auth, can be flaky (502s from Cloudflare)
python -m vasco.navigators.jlcsearch search "<keyword>"
python -m vasco.navigators.jlcsearch filter --category "Optocouplers - Phototransistor Output" --package SOP-4
python -m vasco.navigators.jlcsearch categories opto
python -m vasco.navigators.jlcsearch health

# DigiKey — requires .env with DIGIKEY_CLIENT_ID + DIGIKEY_CLIENT_SECRET
python -m vasco.navigators.digikey search "<keyword>"
python -m vasco.navigators.digikey details "<DigiKey product number>"
```

## Sourcing Workflow
1. **LCSC official API first** — reliable, real-time data, parametric search via categories
2. **jlcsearch as fallback** — free, no auth, but flaky. Use for parametric filtering when LCSC category IDs aren't known
3. **DigiKey for cross-reference** — authoritative specs, wider selection, 1000 searches/day limit

## Finding Alternates
To find drop-in replacements:
1. Get the target part's specs via `lcsc details <code>`
2. Find its category ID from the attributes
3. Browse that category with `lcsc category <id> --in-stock` to find alternates
4. Or use jlcsearch `filter --category "<name>" --package "<pkg>"` for parametric search

## JSON Output Contract
Every navigator returns:
```json
{
  "source": "lcsc|jlcsearch|digikey",
  "query": "search term",
  "total_count": 42,
  "products": [
    {
      "mpn": "STM32F405RGT6",
      "manufacturer": "STMicroelectronics",
      "lcsc_code": "C15742",
      "stock": 12345,
      "package": "LQFP-64(10x10)",
      "pricing": [{"qty": 100, "unit_price": 0.85}],
      "attributes": {"key": "value"},
      "datasheet_url": "..."
    }
  ]
}
```
Errors go to stderr as `{"error": "..."}` with exit code 1.

## Rate Limits
- **LCSC**: 1000 searches/day, 200/minute
- **DigiKey**: 1000 searches/day on free tier
- **jlcsearch**: No known limits, but unreliable

## Caching
All results cached in SQLite (`vasco_cache.db`) with 24hr TTL.
