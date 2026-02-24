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
python -m vasco.navigators.digikey filter --category-id <id> --param <param_id>:<value_id> [--param ...] [--in-stock]
```

## Sourcing Workflow
1. **LCSC official API first** — reliable, real-time data, parametric search via categories
2. **jlcsearch as fallback** — free, no auth, but flaky. Use for parametric filtering when LCSC category IDs aren't known
3. **DigiKey for cross-reference** — authoritative specs, wider selection, 1000 searches/day limit

## Finding Alternates

### DigiKey parametric filter (preferred — "View Similar" workflow)

When the user asks for alternates, similars, drop-ins, or substitutes for a part that has a DigiKey PN:

1. Call `python -m vasco.navigators.digikey details "<DigiKey PN>"` and parse `parameters[]` and `category_id` from the response.

2. Pre-select attributes for drop-in compatibility. Default state:
   - **ON by default**: Number of Circuits, Package / Case, Amplifier Type (or equivalent functional type), supply voltage range
   - **OFF by default**: Operating Temperature, Slew Rate, GBW, CMRR, exact Vos/Ib specs, Supplier Device Package

3. Present a numbered toggle list — show ALL parameters, one per line, with [ON]/[OFF] state:
   ```
   Attribute filters for LM358DR (category 32):
   [ON]  1. Number of Circuits: 2  (parameter_id=2094)
   [ON]  2. Package / Case: 8-SOIC (0.154", 3.90mm Width)  (parameter_id=16)
   [ON]  3. Amplifier Type: Standard (General Purpose)  (parameter_id=161)
   [OFF] 4. Operating Temperature: 0°C ~ 70°C (TA)  (parameter_id=252)
   [OFF] 5. Slew Rate: 0.3V/µs  (parameter_id=511)
   ...
   Type numbers to toggle (e.g. "4 6"), or Enter to search.
   ```

4. Accept toggle input, update the display, repeat until the user presses Enter (or types nothing).

5. Build the `filter` command from all ON attributes and run it:
   ```bash
   python -m vasco.navigators.digikey filter \
     --category-id <id> \
     --param <parameter_id>:<value_id> \
     [--param ...] \
     --in-stock
   ```

6. Present results per Output Rules (DigiKey PN column, pricing at 1/10/25/100, cut tape vs reel separated).

**Do not skip the interview and guess a similar MPN from training knowledge.** Always use the parametric filter so results are grounded in live DigiKey data.

### LCSC / jlcsearch alternates
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

Read MEMORY.md for API quirks, footprint gotchas, and additional sourcing context.

Always use the default model for sourcing tasks — never downgrade to a smaller model.

## Output Rules (MANDATORY)
When presenting sourcing results, ALWAYS include these fields:
- **DigiKey**: Include the DigiKey part number (e.g. `296-LM358DR-ND`) in its own column. Users need it to order. It is NOT the same as MPN.
- **LCSC**: Include the LCSC code (e.g. `C49678`) in its own column.
- **JLCPCB**: Flag whether parts are "Basic" or "Extended" (affects assembly fees).
- Show pricing at small quantities (1/10/25/100). Tape & reel MOQ 1000+ pricing is secondary.
- Separate cut-tape/tube variants from tape & reel variants — they have different DigiKey PNs.
