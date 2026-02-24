# Vasco Operational Knowledge

Sourcing patterns, output requirements, and API quirks learned through use.

## Output Requirements
- **DigiKey**: ALWAYS include the DigiKey part number (`digikey_pn`) when listing parts — it's distinct from MPN
- **LCSC**: ALWAYS include the LCSC code (e.g. C49678) when listing parts
- **JLCPCB**: Flag whether parts are "Basic" or "Extended" (affects assembly fees)

## Sourcing Decision Patterns
- Start with LCSC/jlcsearch (free, no auth) before burning DigiKey quota
- JLCPCB "Basic" parts have lower assembly fees than "Extended"
- User orders in small quantities (~25 pcs). Always show pricing at 10-25 qty, not 1K
- Many DigiKey parts are tape & reel only (MOQ 1000-3000) — always check for cut-tape/tube variants (different DigiKey PN, same MPN)
- DigiKey tape & reel pricing (MOQ 1000+) is not useful for small orders — show cut-tape/tube prices
- LCSC/JLCPCB is typically 3-5x cheaper than DigiKey for small qty
- When cross-referencing: show LCSC price @ 5+ qty, DigiKey price @ 10+ qty (tube)

## API Quirks

### LCSC Official API
- Old API (`wwwapi.lcsc.com`) is dead; official API is at `ips.lcsc.com`
- Auth: `signature = sha1("key={key}&nonce={nonce}&secret={secret}&timestamp={ts}")`
  - nonce = 16 hex chars random string
  - timestamp must be within 60s of server time
  - secret is NOT sent as a query param, only used for signing
- Rate limit error codes: 437 = per-minute exceeded, 438 = per-day exceeded
- Response format: `{"success": true, "code": 200, "message": "", "result": {...}}`

### jlcsearch (tscircuit)
- Must set custom User-Agent (default `python-httpx` blocked by Cloudflare). Intermittent 502s — retry logic required (3 attempts, 1/3/6s backoff)
- Response quirks: `price` is a JSON string of tier array, `extra` is a JSON string, `basic` is 0/1 integer
- `lcsc` field is integer without C prefix
- Health endpoint is `/health` (NOT `/api/health`)

### DigiKey
- Token expires ~10 minutes, cached to `.digikey_token.json` with 30s safety margin
- Token endpoint: `POST https://api.digikey.com/v1/oauth2/token` (form-encoded, client_credentials grant)

## Known Footprint Gotchas
- TLP187 is 6-SOP (6 pins, 4 active). LTV-352T is 4-SOP. Same function on pins 1-4 but different land pattern — NOT a drop-in without PCB change
