"""Unified JLCPCB/LCSC navigator via jlcsearch.tscircuit.com.

Wraps the jlcsearch API which indexes the full JLCPCB/LCSC parts catalog.
Provides keyword search, parametric filtering, and category discovery.

Commands:
  search <keyword>         — MPN/keyword search
  filter [options]         — parametric search by category, package, etc.
  categories [query]       — list available categories (optionally filtered)
  health                   — API health check
"""

import asyncio
import json
import sys
import time

import httpx

from galleon.cache import get as cache_get, put as cache_put

BASE = "https://jlcsearch.tscircuit.com"
HEADERS = {"User-Agent": "Galleon/0.1 (component-sourcing-agent)"}
MAX_RETRIES = 3
RETRY_DELAYS = [1, 3, 6]  # seconds


# ---------------------------------------------------------------------------
# HTTP client with retry
# ---------------------------------------------------------------------------

async def _request(method: str, url: str, **kwargs) -> httpx.Response:
    """Make an HTTP request with automatic retry on 502/503/504."""
    last_exc = None
    for attempt in range(MAX_RETRIES):
        try:
            async with httpx.AsyncClient(timeout=20, headers=HEADERS) as client:
                resp = await getattr(client, method)(url, **kwargs)
                if resp.status_code in (502, 503, 504) and attempt < MAX_RETRIES - 1:
                    delay = RETRY_DELAYS[attempt]
                    print(
                        json.dumps({"warning": f"Got {resp.status_code}, retrying in {delay}s (attempt {attempt + 1}/{MAX_RETRIES})"}),
                        file=sys.stderr,
                    )
                    await asyncio.sleep(delay)
                    continue
                resp.raise_for_status()
                return resp
        except httpx.HTTPStatusError:
            raise
        except httpx.RequestError as e:
            last_exc = e
            if attempt < MAX_RETRIES - 1:
                await asyncio.sleep(RETRY_DELAYS[attempt])
                continue
            raise
    raise last_exc  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Response normalization
# ---------------------------------------------------------------------------

def _parse_json_str(raw) -> dict | list:
    """Parse a JSON string field, returning empty dict/list on failure."""
    if not raw:
        return {}
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}


def _normalize_component(c: dict) -> dict:
    """Normalize a single jlcsearch component into a unified product dict."""
    extra = _parse_json_str(c.get("extra"))

    # Manufacturer from extra, fallback to top-level
    manufacturer = ""
    if isinstance(extra, dict):
        manufacturer = (extra.get("manufacturer") or {}).get("name", "")

    # LCSC code
    lcsc_num = c.get("lcsc")
    lcsc_code = extra.get("number", "") if isinstance(extra, dict) else ""
    if not lcsc_code and isinstance(lcsc_num, int):
        lcsc_code = f"C{lcsc_num}"

    # JLCPCB pricing (from top-level price field — JSON string of tiers)
    jlcpcb_pricing = []
    price_tiers = _parse_json_str(c.get("price"))
    if isinstance(price_tiers, list):
        for t in price_tiers:
            qty = t.get("qFrom") or t.get("min_qty")
            price = t.get("price")
            if qty is not None and price is not None:
                jlcpcb_pricing.append({"qty": qty, "unit_price": float(price)})

    # LCSC pricing (from extra.prices — authoritative)
    lcsc_pricing = []
    if isinstance(extra, dict):
        for t in extra.get("prices", []):
            min_qty = t.get("min_qty")
            price = t.get("price")
            if min_qty is not None and price is not None:
                lcsc_pricing.append({"qty": min_qty, "unit_price": float(price)})

    # Attributes and metadata from extra
    attrs = extra.get("attributes", {}) if isinstance(extra, dict) else {}
    description = ""
    if isinstance(extra, dict):
        description = extra.get("description", "")
    if not description:
        description = c.get("description", "")

    datasheet = ""
    if isinstance(extra, dict):
        datasheet = (extra.get("datasheet") or {}).get("pdf", "")
    if not datasheet:
        datasheet = c.get("datasheet", "")

    product_url = extra.get("url", "") if isinstance(extra, dict) else ""
    if not product_url and lcsc_code:
        product_url = f"https://www.lcsc.com/product-detail/{lcsc_code}.html"
    category = ""
    if isinstance(extra, dict):
        cat = extra.get("category", {})
        category = cat.get("name2", cat.get("name1", ""))

    return {
        "mpn": c.get("mfr", ""),
        "manufacturer": manufacturer,
        "lcsc_code": lcsc_code,
        "description": description,
        "category": category,
        "stock": extra.get("quantity", c.get("stock", 0)) if isinstance(extra, dict) else c.get("stock", 0),
        "package": c.get("package", ""),
        "basic": bool(c.get("basic", 0)),
        "datasheet_url": datasheet,
        "product_url": product_url,
        "jlcpcb_pricing": jlcpcb_pricing,
        "lcsc_pricing": lcsc_pricing,
        "attributes": attrs,
    }


def _make_envelope(components: list[dict], query: str, source: str = "jlcsearch") -> dict:
    products = [_normalize_component(c) for c in components]
    return {
        "source": source,
        "query": query,
        "total_count": len(products),
        "products": products,
    }


# ---------------------------------------------------------------------------
# API commands
# ---------------------------------------------------------------------------

async def search(keyword: str, limit: int = 20) -> dict:
    """Keyword/MPN search via /api/search."""
    cache_key = f"search:{keyword}:{limit}"
    cached = await cache_get("jlcsearch", cache_key)
    if cached is not None:
        return cached

    resp = await _request(
        "get", f"{BASE}/api/search",
        params={"q": keyword, "limit": limit, "full": "true"},
    )
    data = resp.json()
    items = data.get("components", []) if isinstance(data, dict) else []

    result = _make_envelope(items, keyword)
    await cache_put("jlcsearch", cache_key, result)
    return result


async def filter_parts(
    subcategory: str | None = None,
    package: str | None = None,
    search_term: str | None = None,
    limit: int = 50,
) -> dict:
    """Parametric search via /components/list.json.

    Filters by category, package, and/or keyword. This is the right
    endpoint for finding alternates (e.g. all SOP-4 optocouplers).
    """
    params: dict = {"limit": limit, "full": "true"}
    if subcategory:
        params["subcategory_name"] = subcategory
    if package:
        params["package"] = package
    if search_term:
        params["search"] = search_term

    query_desc = " ".join(f"{k}={v}" for k, v in params.items() if k not in ("limit", "full"))
    cache_key = f"filter:{json.dumps(params, sort_keys=True)}"
    cached = await cache_get("jlcsearch", cache_key)
    if cached is not None:
        return cached

    resp = await _request("get", f"{BASE}/components/list.json", params=params)
    data = resp.json()
    items = data.get("components", []) if isinstance(data, dict) else []

    result = _make_envelope(items, query_desc)
    await cache_put("jlcsearch", cache_key, result)
    return result


async def categories(query: str | None = None) -> dict:
    """List available categories, optionally filtered by keyword."""
    cache_key = f"categories:{query or 'all'}"
    cached = await cache_get("jlcsearch", cache_key)
    if cached is not None:
        return cached

    resp = await _request("get", f"{BASE}/categories/list.json")
    data = resp.json()
    cats = data.get("categories", data) if isinstance(data, dict) else data

    if query and isinstance(cats, list):
        q = query.lower()
        cats = [c for c in cats if q in json.dumps(c).lower()]

    result = {"categories": cats, "total_count": len(cats)}
    await cache_put("jlcsearch", cache_key, result)
    return result


async def health() -> dict:
    """Check API health."""
    try:
        resp = await _request("get", f"{BASE}/health")
        data = resp.json()
        return {"status": "ok" if data.get("ok") else "error", "code": resp.status_code}
    except Exception as e:
        return {"status": "error", "detail": str(e)}


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------

def _error(msg: str) -> None:
    print(json.dumps({"error": msg}), file=sys.stderr)
    sys.exit(1)


USAGE = """Usage: python -m galleon.navigators.jlcsearch <command> [args]

Commands:
  search <keyword>                              Search by MPN or keyword
  filter --category <name> [--package <pkg>]    Parametric search
  categories [query]                            List categories
  health                                        Check API status

Examples:
  python -m galleon.navigators.jlcsearch search "TLP187"
  python -m galleon.navigators.jlcsearch filter --category "Optocouplers - Phototransistor Output" --package SOP-4
  python -m galleon.navigators.jlcsearch categories opto
  python -m galleon.navigators.jlcsearch health"""


async def _main() -> None:
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(USAGE)
        sys.exit(0)

    command = args[0]

    if command == "health":
        print(json.dumps(await health(), indent=2))
        return

    if command == "search":
        if len(args) < 2:
            _error("Missing keyword. Usage: search <keyword>")
        print(json.dumps(await search(args[1]), indent=2))
        return

    if command == "categories":
        query = args[1] if len(args) > 1 else None
        print(json.dumps(await categories(query), indent=2))
        return

    if command == "filter":
        subcategory = None
        package = None
        search_term = None
        limit = 50
        i = 1
        while i < len(args):
            if args[i] in ("--category", "-c") and i + 1 < len(args):
                subcategory = args[i + 1]
                i += 2
            elif args[i] in ("--package", "-p") and i + 1 < len(args):
                package = args[i + 1]
                i += 2
            elif args[i] in ("--search", "-s") and i + 1 < len(args):
                search_term = args[i + 1]
                i += 2
            elif args[i] in ("--limit", "-l") and i + 1 < len(args):
                limit = int(args[i + 1])
                i += 2
            else:
                _error(f"Unknown argument: {args[i]}")
        if not subcategory and not package and not search_term:
            _error("filter requires at least --category, --package, or --search")
        print(json.dumps(await filter_parts(subcategory, package, search_term, limit), indent=2))
        return

    _error(f"Unknown command: {command}. Run with --help for usage.")


if __name__ == "__main__":
    asyncio.run(_main())
