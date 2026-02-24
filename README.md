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

Run Claude Code from the project directory. Ask it to source components in natural language:

```
Find me an STM32F405 in stock on LCSC
What are some alternatives to C15742?
Search DigiKey for LM358 and compare pricing
Find SOP-4 optocouplers with phototransistor output
Find 100x 0.1uF 0805 capacitors — check both LCSC and DigiKey, compare stock and pricing, and recommend the best option
```

Claude Code reads the `CLAUDE.md` instructions and invokes the navigator modules automatically to search, compare, and recommend parts.

## License

MIT — see [LICENSE](LICENSE).
