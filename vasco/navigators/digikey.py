"""DigiKey navigator — DigiKey API v4 with OAuth2 client credentials.

Requires DIGIKEY_CLIENT_ID and DIGIKEY_CLIENT_SECRET in .env file.
Register at https://developer.digikey.com/

Commands:
  search <keyword> [options]     Keyword/MPN search with optional filters
  details <product_number>       Full product details + parameters (includes parameter_id/value_id)
  filter [options]               Parametric search within a category (View Similar)
  substitutions <product_number> Find alternates/substitutions
  categories                     List all product categories
  manufacturers                  List all manufacturers
"""

import asyncio
import json
import os
import sys
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv

from vasco.cache import get as cache_get, put as cache_put

load_dotenv()

SOURCE = "digikey"
BASE_URL = "https://api.digikey.com/products/v4"
TOKEN_URL = "https://api.digikey.com/v1/oauth2/token"
TOKEN_PATH = Path(__file__).resolve().parent.parent.parent / ".digikey_token.json"
SAFETY_MARGIN = 30  # seconds before expiry to refresh

MAX_RETRIES = 3
RETRY_DELAYS = [1, 2, 4]


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def _get_credentials() -> tuple[str, str]:
    client_id = os.environ.get("DIGIKEY_CLIENT_ID", "")
    client_secret = os.environ.get("DIGIKEY_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        raise RuntimeError(
            "DIGIKEY_CLIENT_ID and DIGIKEY_CLIENT_SECRET must be set in .env. "
            "Register at https://developer.digikey.com/"
        )
    return client_id, client_secret


def _load_cached_token() -> dict | None:
    if not TOKEN_PATH.exists():
        return None
    try:
        data = json.loads(TOKEN_PATH.read_text())
        if time.time() < data.get("expires_at", 0) - SAFETY_MARGIN:
            return data
    except (json.JSONDecodeError, KeyError):
        pass
    return None


def _save_token(token_data: dict) -> None:
    TOKEN_PATH.write_text(json.dumps(token_data, indent=2))


async def _get_token() -> str:
    """Get a valid OAuth2 access token, refreshing if needed."""
    cached = _load_cached_token()
    if cached:
        return cached["access_token"]

    client_id, client_secret = _get_credentials()
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            TOKEN_URL,
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "grant_type": "client_credentials",
            },
        )
        resp.raise_for_status()
        data = resp.json()

    data["expires_at"] = time.time() + data.get("expires_in", 600)
    _save_token(data)
    return data["access_token"]


def _api_headers(token: str) -> dict:
    client_id, _ = _get_credentials()
    return {
        "Authorization": f"Bearer {token}",
        "X-DIGIKEY-Client-Id": client_id,
        "X-DIGIKEY-Locale-Site": "US",
        "X-DIGIKEY-Locale-Language": "en",
        "X-DIGIKEY-Locale-Currency": "USD",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


# ---------------------------------------------------------------------------
# HTTP with retry
# ---------------------------------------------------------------------------

async def _request(method: str, path: str, token: str, **kwargs) -> httpx.Response:
    """Make an API request with retry on transient errors."""
    url = f"{BASE_URL}{path}"
    headers = _api_headers(token)
    last_exc = None
    for attempt in range(MAX_RETRIES):
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await getattr(client, method)(url, headers=headers, **kwargs)
                if resp.status_code in (502, 503, 429) and attempt < MAX_RETRIES - 1:
                    delay = RETRY_DELAYS[attempt]
                    print(
                        json.dumps({"warning": f"Got {resp.status_code}, retrying in {delay}s ({attempt + 1}/{MAX_RETRIES})"}),
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
# Normalization
# ---------------------------------------------------------------------------

def _leaf_category(cat: dict) -> dict:
    """Walk ChildCategories to find the deepest (leaf) category node."""
    children = cat.get("ChildCategories") or []
    for child in children:
        leaf = _leaf_category(child)
        if leaf:
            return leaf
    return cat


def _normalize_product(p: dict) -> list[dict]:
    """Normalize a product from KeywordSearch response.

    Returns one entry per packaging variation (Cut Tape, Tape & Reel, Tube,
    Digi-Reel), each with its own DigiKey PN, pricing, and stock. This
    ensures small-qty cut tape options are never hidden behind reel pricing.
    """
    parameters = {}
    for param in p.get("Parameters", []) or []:
        name = param.get("ParameterText", param.get("Parameter", ""))
        value = param.get("ValueText", param.get("Value", ""))
        if name and value:
            parameters[name] = value

    category = p.get("Category", {}) or {}
    leaf_cat = _leaf_category(category)
    base = {
        "mpn": p.get("ManufacturerProductNumber", ""),
        "manufacturer": (p.get("Manufacturer", {}) or {}).get("Name", ""),
        "description": (p.get("Description", {}) or {}).get("DetailedDescription", ""),
        "category": leaf_cat.get("Name", category.get("Name", "")),
        "category_id": leaf_cat.get("CategoryId", category.get("CategoryId")),
        "datasheet_url": p.get("DatasheetUrl", ""),
        "product_url": p.get("ProductUrl", ""),
        "photo_url": p.get("PhotoUrl", ""),
        "attributes": parameters,
        "series": (p.get("Series", {}) or {}).get("Name", ""),
        "status": (p.get("ProductStatus", {}) or {}).get("Status", ""),
        "rohs": (p.get("Classifications", {}) or {}).get("RohsStatus", ""),
    }

    variations = p.get("ProductVariations", []) or []
    if not variations:
        # Fallback: no variations, use top-level fields
        pricing = []
        for tier in p.get("StandardPricing", []) or []:
            qty = tier.get("BreakQuantity")
            price = tier.get("UnitPrice")
            if qty is not None and price is not None:
                pricing.append({"qty": qty, "unit_price": float(price)})
        return [{
            **base,
            "digikey_pn": p.get("DigiKeyProductNumber", ""),
            "stock": p.get("QuantityAvailable", 0),
            "packaging": (p.get("Packaging", {}) or {}).get("Value", ""),
            "moq": 1,
            "pricing": pricing,
        }]

    results = []
    for var in variations:
        pricing = []
        for tier in var.get("StandardPricing", []) or []:
            qty = tier.get("BreakQuantity")
            price = tier.get("UnitPrice")
            if qty is not None and price is not None:
                pricing.append({"qty": qty, "unit_price": float(price)})

        pkg_type = (var.get("PackageType", {}) or {}).get("Name", "")
        results.append({
            **base,
            "digikey_pn": var.get("DigiKeyProductNumber", ""),
            "stock": var.get("QuantityAvailableforPackageType", p.get("QuantityAvailable", 0)),
            "packaging": pkg_type,
            "moq": var.get("MinimumOrderQuantity", 1),
            "pricing": pricing,
        })

    return results


def _make_envelope(products_raw: list, query: str, total: int | None = None) -> dict:
    products = []
    for p in products_raw:
        products.extend(_normalize_product(p))
    return {
        "source": SOURCE,
        "query": query,
        "total_count": total if total is not None else len(products),
        "products": products,
    }


# ---------------------------------------------------------------------------
# API commands
# ---------------------------------------------------------------------------

async def search(
    keyword: str,
    limit: int = 50,
    offset: int = 0,
    category_id: int | None = None,
    manufacturer_id: int | None = None,
    in_stock: bool = False,
    sort_by: str | None = None,
) -> dict:
    """Keyword search with optional filtering.

    Args:
        keyword: MPN, description, or keyword (max 250 chars)
        limit: Results per page (1-50)
        category_id: Filter to a specific category (from categories command)
        manufacturer_id: Filter to a specific manufacturer
        in_stock: Only return in-stock parts
        sort_by: Sort field (Price, QuantityAvailable, Manufacturer, etc.)
    """
    cache_key = f"search:{keyword}:{limit}:{offset}:{category_id}:{manufacturer_id}:{in_stock}"
    cached = await cache_get(SOURCE, cache_key)
    if cached is not None:
        return cached

    token = await _get_token()

    payload: dict = {
        "Keywords": keyword,
        "Limit": limit,
        "Offset": offset,
    }

    filters: dict = {
        "MarketPlaceFilter": "ExcludeMarketPlace",
    }
    if category_id is not None:
        filters["CategoryFilter"] = [{"Id": str(category_id)}]
    if manufacturer_id is not None:
        filters["ManufacturerFilter"] = [{"Id": str(manufacturer_id)}]
    if in_stock:
        filters["SearchOptions"] = ["InStock"]

    payload["FilterOptionsRequest"] = filters

    if sort_by:
        payload["SortOptions"] = {"Field": sort_by, "SortOrder": "Ascending"}

    resp = await _request("post", "/search/keyword", token, json=payload)
    raw = resp.json()

    products_raw = raw.get("Products", [])
    total = raw.get("ProductsCount", len(products_raw))

    result = _make_envelope(products_raw, keyword, total)

    # Include filter options in response so caller can narrow searches
    filter_opts = raw.get("FilterOptions", {})
    if filter_opts:
        result["filter_options"] = {
            "manufacturers": [
                {"id": m.get("Id"), "name": m.get("Value"), "count": m.get("ProductCount")}
                for m in (filter_opts.get("Manufacturers", []) or [])
            ],
            "categories": [
                {"id": c.get("Id"), "name": c.get("Value"), "count": c.get("ProductCount")}
                for c in (filter_opts.get("TopCategories", []) or [])
            ],
        }

    await cache_put(SOURCE, cache_key, result)
    return result


async def details(product_number: str) -> dict:
    """Get full product details including all parameters.

    The response includes a top-level `parameters` list with raw parameter_id
    and value_id fields needed to construct parametric_filter() calls.
    """
    cache_key = f"details:{product_number}"
    cached = await cache_get(SOURCE, cache_key)
    if cached is not None:
        return cached

    token = await _get_token()
    resp = await _request("get", f"/search/{product_number}/productdetails", token)
    raw = resp.json()

    # v4 productdetails wraps the product under a "Product" key
    product = raw.get("Product", raw) if isinstance(raw, dict) else {}

    result = _make_envelope([product] if product else [], product_number)

    # Preserve parameter IDs for the parametric filter workflow
    params_raw = product.get("Parameters", []) or []
    result["parameters"] = [
        {
            "name": p.get("ParameterText", p.get("Parameter", "")),
            "value": p.get("ValueText", p.get("Value", "")),
            "parameter_id": p.get("ParameterId"),
            "value_id": p.get("ValueId"),
        }
        for p in params_raw
        if p.get("ParameterText") or p.get("Parameter")
    ]
    # Top-level category_id for convenience (already in products[0] but easier to access here)
    result["category_id"] = (product.get("Category", {}) or {}).get("CategoryId")

    await cache_put(SOURCE, cache_key, result)
    return result


async def parametric_filter(
    category_id: int,
    param_filters: list[dict],
    in_stock: bool = False,
    limit: int = 50,
    keyword: str = "",
) -> dict:
    """Parametric search — find parts matching specific attribute values.

    Equivalent to DigiKey's "View Similar" UI: same category, selected params.
    param_filters comes from the parameters[] field in a details() response:
      [{"parameter_id": int, "value_id": str}, ...]
    """
    cache_key = (
        f"filter:{category_id}:"
        + ",".join(
            f"{f['parameter_id']}:{f['value_id']}"
            for f in sorted(param_filters, key=lambda x: x["parameter_id"])
        )
        + f":{in_stock}:{keyword}"
    )
    cached = await cache_get(SOURCE, cache_key)
    if cached is not None:
        return cached

    token = await _get_token()

    filters: dict = {
        "MarketPlaceFilter": "ExcludeMarketPlace",
        "CategoryFilter": [{"Id": str(category_id)}],
        "ParametricFilters": [
            {"ParameterId": f["parameter_id"], "ValueIds": [str(f["value_id"])]}
            for f in param_filters
            if f.get("parameter_id") is not None and f.get("value_id") is not None
        ],
    }
    if in_stock:
        filters["SearchOptions"] = ["InStock"]

    payload = {
        "Keywords": keyword,
        "Limit": limit,
        "Offset": 0,
        "FilterOptionsRequest": filters,
    }

    resp = await _request("post", "/search/keyword", token, json=payload)
    raw = resp.json()

    products_raw = raw.get("Products", [])
    total = raw.get("ProductsCount", len(products_raw))
    result = _make_envelope(products_raw, keyword or f"category:{category_id}", total)
    result["filter_applied"] = param_filters

    await cache_put(SOURCE, cache_key, result)
    return result


async def substitutions(product_number: str) -> dict:
    """Find substitute/alternate products."""
    cache_key = f"subs:{product_number}"
    cached = await cache_get(SOURCE, cache_key)
    if cached is not None:
        return cached

    token = await _get_token()
    resp = await _request("get", f"/search/{product_number}/substitutions", token)
    raw = resp.json()

    products_raw = raw if isinstance(raw, list) else raw.get("Products", raw.get("Substitutions", []))
    result = _make_envelope(products_raw, f"substitutions:{product_number}")
    await cache_put(SOURCE, cache_key, result)
    return result


async def list_categories() -> dict:
    """List all DigiKey product categories."""
    cached = await cache_get(SOURCE, "categories")
    if cached is not None:
        return cached

    token = await _get_token()
    resp = await _request("get", "/search/categories", token)
    raw = resp.json()

    cats = raw if isinstance(raw, list) else raw.get("Categories", [])
    result = {"source": SOURCE, "categories": cats, "total_count": len(cats)}
    await cache_put(SOURCE, "categories", result)
    return result


async def list_manufacturers() -> dict:
    """List all DigiKey manufacturers."""
    cached = await cache_get(SOURCE, "manufacturers")
    if cached is not None:
        return cached

    token = await _get_token()
    resp = await _request("get", "/search/manufacturers", token)
    raw = resp.json()

    mfrs = raw if isinstance(raw, list) else raw.get("Manufacturers", [])
    result = {"source": SOURCE, "manufacturers": mfrs, "total_count": len(mfrs)}
    await cache_put(SOURCE, "manufacturers", result)
    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _error(msg: str) -> None:
    print(json.dumps({"error": msg}), file=sys.stderr)
    sys.exit(1)


USAGE = """Usage: python -m vasco.navigators.digikey <command> [args]

Commands:
  search <keyword> [options]       Keyword/MPN search
    --limit N                      Results per page (1-50, default 50)
    --category-id N                Filter by category ID
    --manufacturer-id N            Filter by manufacturer ID
    --in-stock                     Only in-stock parts
    --sort <field>                 Sort by: Price, QuantityAvailable, Manufacturer

  details <product_number>         Full product details + parameters
                                   (response includes parameters[] with parameter_id/value_id)

  filter [options]                 Parametric search (View Similar)
    --category-id N                Category to search within (required)
    --param <id>:<value_id>        Attribute filter (repeatable)
    --in-stock                     Only in-stock parts
    --limit N                      Results per page (1-50, default 50)
    --keyword "..."                Optional keyword to narrow results further

  substitutions <product_number>   Find alternates/substitutions
  categories                       List all product categories
  manufacturers                    List all manufacturers

Examples:
  python -m vasco.navigators.digikey search "TLP187"
  python -m vasco.navigators.digikey search "optocoupler" --category-id 48 --in-stock
  python -m vasco.navigators.digikey details "TLP187(E(T-ND"
  python -m vasco.navigators.digikey substitutions "TLP187(E(T-ND"
  python -m vasco.navigators.digikey categories

  # After: python -m vasco.navigators.digikey details "296-LM358DR-ND"
  # Pick parameter_id and value_id from the returned parameters[] field, then:
  python -m vasco.navigators.digikey filter --category-id 2985 --param 1989:2 --param 16:SOP-8 --in-stock"""


async def _main() -> None:
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(USAGE)
        sys.exit(0)

    command = args[0]

    try:
        if command == "search":
            if len(args) < 2:
                _error("Missing keyword. Usage: search <keyword>")
            keyword = args[1]
            limit = 50
            category_id = None
            manufacturer_id = None
            in_stock = False
            sort_by = None
            i = 2
            while i < len(args):
                if args[i] == "--limit" and i + 1 < len(args):
                    limit = int(args[i + 1])
                    i += 2
                elif args[i] == "--category-id" and i + 1 < len(args):
                    category_id = int(args[i + 1])
                    i += 2
                elif args[i] == "--manufacturer-id" and i + 1 < len(args):
                    manufacturer_id = int(args[i + 1])
                    i += 2
                elif args[i] == "--in-stock":
                    in_stock = True
                    i += 1
                elif args[i] == "--sort" and i + 1 < len(args):
                    sort_by = args[i + 1]
                    i += 2
                else:
                    _error(f"Unknown argument: {args[i]}")
            result = await search(
                keyword, limit=limit, category_id=category_id,
                manufacturer_id=manufacturer_id, in_stock=in_stock, sort_by=sort_by,
            )
            print(json.dumps(result, indent=2))

        elif command == "details":
            if len(args) < 2:
                _error("Missing product number.")
            print(json.dumps(await details(args[1]), indent=2))

        elif command == "filter":
            category_id = None
            param_filters = []
            in_stock = False
            limit = 50
            keyword = ""
            i = 1
            while i < len(args):
                if args[i] == "--category-id" and i + 1 < len(args):
                    category_id = int(args[i + 1])
                    i += 2
                elif args[i] == "--param" and i + 1 < len(args):
                    pid, vid = args[i + 1].split(":", 1)
                    param_filters.append({"parameter_id": int(pid), "value_id": vid})
                    i += 2
                elif args[i] == "--in-stock":
                    in_stock = True
                    i += 1
                elif args[i] == "--limit" and i + 1 < len(args):
                    limit = int(args[i + 1])
                    i += 2
                elif args[i] == "--keyword" and i + 1 < len(args):
                    keyword = args[i + 1]
                    i += 2
                else:
                    _error(f"Unknown argument: {args[i]}")
            if category_id is None:
                _error("--category-id is required for filter command.")
            result = await parametric_filter(
                category_id, param_filters, in_stock=in_stock, limit=limit, keyword=keyword
            )
            print(json.dumps(result, indent=2))

        elif command == "substitutions":
            if len(args) < 2:
                _error("Missing product number.")
            print(json.dumps(await substitutions(args[1]), indent=2))

        elif command == "categories":
            print(json.dumps(await list_categories(), indent=2))

        elif command == "manufacturers":
            print(json.dumps(await list_manufacturers(), indent=2))

        else:
            _error(f"Unknown command: {command}. Run with --help for usage.")

    except RuntimeError as e:
        _error(str(e))
    except httpx.HTTPStatusError as e:
        _error(f"HTTP {e.response.status_code}: {e}")
    except httpx.RequestError as e:
        _error(f"Request failed: {e}")


if __name__ == "__main__":
    asyncio.run(_main())
