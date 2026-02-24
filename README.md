# Vasco

Electronic component sourcing agent — Claude Code orchestrates navigator modules that query supplier APIs and return structured JSON results.

## How it works

Vasco provides standalone navigator modules for three component suppliers. Claude Code acts as the orchestrator, invoking navigators via `python -m` and using the JSON output to compare parts, find alternates, and make sourcing decisions.

**Sourcing priority:**
1. **LCSC** (official API) — reliable, real-time data, parametric search via categories
2. **jlcsearch** (fallback) — free, no auth, useful for parametric filtering when LCSC category IDs aren't known
3. **DigiKey** (cross-reference) — authoritative specs, wider selection

## Setup

Requires Python 3.11+.

```bash
pip install -e .
```

Create a `.env` file with your API credentials:

```env
# LCSC (required for LCSC navigator)
LCSC_API_KEY=your_key
LCSC_API_SECRET=your_secret

# DigiKey (required for DigiKey navigator)
DIGIKEY_CLIENT_ID=your_client_id
DIGIKEY_CLIENT_SECRET=your_client_secret

# jlcsearch requires no authentication
```

## Usage

### LCSC

```bash
python -m vasco.navigators.lcsc search "STM32F405"
python -m vasco.navigators.lcsc search "STM32F405" --in-stock --exact
python -m vasco.navigators.lcsc details "C15742"
python -m vasco.navigators.lcsc categories
python -m vasco.navigators.lcsc category 312 --in-stock
```

### jlcsearch

```bash
python -m vasco.navigators.jlcsearch search "ESP32"
python -m vasco.navigators.jlcsearch filter --category "Optocouplers - Phototransistor Output" --package SOP-4
python -m vasco.navigators.jlcsearch categories opto
python -m vasco.navigators.jlcsearch health
```

### DigiKey

```bash
python -m vasco.navigators.digikey search "LM358"
python -m vasco.navigators.digikey details "296-1395-5-ND"
```

## License

MIT — see [LICENSE](LICENSE).
