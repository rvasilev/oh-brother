# AGENTS.md — oh-brother

> AI agent guidance. Read before writing code.

## What this is

Cross-platform Python 3 CLI to update Brother printer firmware. SNMP discovery → XML query to Brother's Japan server → HTTP download → TCP 9100 or FTP upload.

- **Repo:** https://github.com/rvasilev/oh-brother
- **Upstream:** https://github.com/CauldronDevelopmentLLC/oh-brother (commit `a9c8b10`)
- **License:** GPLv2
- **Lines:** 606, single file (`oh-brother.py`)
- **Branch:** `master`

## Architecture

```
User → CLI args (IP, --password, --category, --model, --test, --beta, --yes)
  │
  ├─[1] SNMP walk (BROTHER_SNMP_OID) via pysnmp 7.x async v1arch
  │     → _snmp_walk_table() → asyncio.run() wrapper → parse_snmp_table()
  │     → model, serial, spec, firmwares
  │
  ├─[2] XML POST — build_firmware_xml() → Brother API (BROTHER_API_URL)
  │     → _http_post() with error handling → parse_brother_response()
  │     → version check, firmware URL (PATH)
  │     → VCHECK=1 or no-PATH → _try_version_fallback()
  │
  ├─[3] URL validation — _validate_firmware_url()
  │     checks: scheme (http/https), domain (brother.co.jp/.com/.eu),
  │     extension (.djf/.upd)
  │
  ├─[4] HTTP GET firmware blob via urllib with error handling
  │     → _verify_firmware_integrity() — Content-Length check + 100KB minimum
  │
  └─[5] Upload to printer
        ├─ TCP port 9100 — _tcp_upload() with sendfile retry loop + socket timeout
        └─ FTP (when --password set — admin password as username)
```

## Refactored structure

| Function | Purpose |
|---|---|
| `parse_snmp_table(table, verbose=False)` | Parse SNMP walk result → dict with serial, model, spec, firmwares |
| `build_firmware_xml(model, spec, cat, ver, beta=False)` | Build XML request for Brother API → bytes |
| `parse_brother_response(xml_bytes)` | Parse Brother API XML → dict with version_check, firmware_url |
| `_decrement_version(version_str)` | Decrement minor version for API fallback trick |
| `_validate_firmware_url(url)` | Validate firmware download URL (scheme, domain, extension) |
| `_tcp_upload(filename, ip, sock)` | TCP 9100 upload with sendfile offset retry loop |
| `_verify_firmware_integrity(filepath, content_length)` | Verify downloaded firmware (Content-Length match, 100KB minimum) |
| `_http_post(url, data, hdrs, timeout)` | POST to Brother API with error categorization (HTTP, SSL, timeout, DNS) |
| `_http_request(url, data, hdrs, timeout)` | Unified GET/POST with same error handling as _http_post |
| `_try_version_fallback(version, cat, url, hdrs)` | Decrement version + query API for firmware URL (shared by both fallback paths) |
| `update_firmware(cat, version)` | Orchestration: XML build → POST → parse → fallback → validate → download → verify → upload |
| `_snmp_walk_table(ip, community, oid)` | Async pysnmp 7.x walk wrapper (via asyncio.run() in main) |
| `main()` | Entry point: argparse → SNMP walk → parse → update loop with cooldown |

**Module constants:** `BROTHER_API_URL`, `BROTHER_SNMP_OID`, `reqInfo` (XML template)

Module is import-safe: `if __name__ == '__main__':` guard.

## Key external systems

| System | Protocol | Notes |
|---|---|---|
| Brother firmware API | HTTPS XML | `BROTHER_API_URL`, requires `User-Agent: BrHttpc/1.00` |
| Brother firmware CDN | HTTP | `update-akamai.brother.co.jp/CS/` — `.djf` / `.upd` files |
| Printer SNMP | UDP 161 | Community string (default `public`), `BROTHER_SNMP_OID` |
| Printer raw port | TCP 9100 | Must be enabled in printer web UI |
| Printer FTP | TCP 21 | Admin password sent as FTP username (Brother quirk) |

## Code conventions

- **Python 3.13** (3.10 compatible but untested)
- **pysnmp >=7.1.0** — async v1arch API (`walk_cmd`, `SnmpDispatcher`, `UdpTransportTarget.create()`)
- **`asyncio.run()` wrapper** — CLI stays synchronous despite async SNMP
- **Procedural style** — pure functions, module-level globals (`args`, `model`, `spec`)
- **No external HTTP libraries** — stdlib `urllib` only
- **XML parsing** — stdlib `xml.etree.ElementTree`
- **Firmware downloads** go to CWD — temp dir migration pending
- **Import-safe** — `if __name__ == '__main__':` guard
- **Test imports** use `importlib.util.spec_from_file_location` (filename has hyphen)

## Safety features

| Feature | Function | Guards against |
|---|---|---|
| URL validation | `_validate_firmware_url()` | Wrong-domain firmware, non-http schemes, non-firmware files |
| Firmware integrity | `_verify_firmware_integrity()` | Truncated downloads (Content-Length check), corrupt/error pages (100KB minimum) |
| Sendfile retry | `_tcp_upload()` | Short TCP writes (offset retry loop), connection drops (zero-return detection) |
| Upload cooldown | `main()` loop | Printer reboot race when multiple firmwares pending (30s delay) |
| HTTP error handling | `_http_post()` / `_http_request()` | 503/504 server errors, SSL cert failures, timeouts, DNS errors |
| Socket timeout | `sock.settimeout(60)` | Indefinite TCP upload hangs |

## CLI reference

```
./oh-brother.py [OPTIONS] <printer IP>

  -t, --test       Check firmware availability (no upload)
  -c, --category   Force a specific firmware category (MAIN, SUB1, etc.)
  -m, --model      Force a specific printer model
  -f, --version    Force a specific firmware version (requires --category)
  -v, --verbose    Verbose output (SNMP dump, XML request/response)
  --beta           Query for beta firmware (INSPECTMODE=1)
  -p, --password   Upload via FTP using printer admin password
  -C, --community  SNMP community string (default: public)
  -y, --yes        Skip all confirmation prompts (non-interactive mode)
```

## Testing

**74 tests, 11 classes.** Run: `python3 -m pytest tests/ -v`

| Class | Tests | What it covers |
|---|---|---|
| `TestParseSnmpTable` | 7 | Real printer data, multi-FW, ordering edge cases, empty table, verbose |
| `TestBuildFirmwareXml` | 6 | XML structure, FIRM→MAIN mapping, IFAX→MAIN mapping, beta flag, bytes output |
| `TestParseBrotherResponse` | 5 | Up-to-date, update available, no PATH, no VERSIONCHECK, empty response |
| `TestCLI` | 7 (parameterized) | All boolean flags, string args, category+version combo, IP required |
| `TestMainSmoke` | 6 | SNMP pipeline, model override, category override, SNMP errors, multi-FW cooldown |
| `TestUpdateFirmware` | 5 + 1 skip | VCHECK=1, no-PATH, --yes skip, fallback succeed/fail |
| `TestDecrementVersion` | 5 | Normal, zero minor, 3-part, non-numeric, empty |
| `TestValidateFirmwareUrl` | 8 | Valid HTTP/HTTPS, .upd, wrong domain, file://, wrong ext, empty, query params |
| `TestTcpUpload` | 3 | Full sendfile, short-write retry, zero-return failure |
| `TestVerifyFirmwareIntegrity` | 5 | Content-Length match/mismatch, too small, minimum pass, size-only |
| `TestHttpPost` | 5 | Success, HTTP 503, SSL cert error, timeout, DNS failure |

**Test infrastructure:**
- `tests/conftest.py` — mocks `pysnmp`, `pysnmp.hlapi`, `pysnmp.hlapi.v1arch` at `sys.modules` level
- `tests/test_oh_brother.py` — imports module via `importlib.util` (filename has hyphen)
- Fixture data captured from a Brother HL-L2865DW printer
- `test_test_flag_stops_before_upload` is skipped — HTTP download mocking needs deeper `urllib.request` interception
- Custom mocks for `_snmp_walk_table` (async → sync table return) and `time.sleep` (cooldown verification)

```bash
# Full test run
python3 -m pytest tests/ -v

# With coverage
python3 -m pytest tests/ --cov=oh-brother.py --cov-report=term-missing

# Real printer integration test
python3 oh-brother.py --test --yes <printer IP>
```

## pysnmp migration (completed)

### History

| Phase | Version | API | Notes |
|---|---|---|---|
| Upstream | pysnmp 4.x | `oneliner.cmdgen.nextCmd` | Required `asyncore` (removed Python 3.12) |
| Phase 2 | pysnmp 6.1.4 | `hlapi.walkCmd` (sync) | Last sync hlapi, pinned `==6.1.4` |
| Phase 3 (current) | pysnmp 7.1.x | `v1arch.walk_cmd` (async) | **Current.** `>=7.1.0`, no version pin |

### Current imports

```python
from pysnmp.hlapi.v1arch import (
    walk_cmd, CommunityData, UdpTransportTarget,
    ObjectType, ObjectIdentity, SnmpDispatcher,
)
```

`_snmp_walk_table()` is an async function using `await UdpTransportTarget.create()` and `async for ... in walk_cmd()`. Called via `asyncio.run()` in `main()` — CLI stays synchronous. `lexicographicMode=False` passed as kwarg. `ContextData` not needed for v2c. Return tuple format is identical to 6.x.

## Known issues

| Issue | Status |
|---|---|
| Downloads to CWD not temp dir | Unfixed (low — single-user CLI) |
| FTP no timeout, broad exception catch | Unfixed (medium — TCP 9100 is default) |
| No `--dry-run` flag | Unfixed (low — `--test` downloads then deletes) |
| `--fw-version` default `B0000000000` magic sentinel | Unfixed (low) |
| Model name "series" suffix for D01 color lasers | Unfixed (enhancement — VCHECK=2 not handled) |
| `model`/`spec` can be None → `<NAME>None</NAME>` XML | Unfixed (medium — requires invalid SNMP data) |
| `except Exception` in main() swallows traceback | Unfixed (low — prints to stderr) |
| Firmware deleted on upload failure (can't retry) | Unfixed (medium) |

## Changelog (fork)

| Phase | What |
|---|---|
| Phase 0 | Added `if __name__ == '__main__':` guard, extracted 3 pure functions |
| Phase 0.5 | Added `-y`/`--yes` flag (non-interactive mode) |
| Phase 1 | 31 initial tests (parse, XML, CLI) |
| Phase 1.5 | 45 tests: added `TestMainSmoke`, `TestUpdateFirmware`, collapsed CLI to parameterized |
| Phase 2 | Migrated pysnmp `oneliner` → `hlapi.walkCmd` (6.1.4), Bear review fixes |
| Phase 3 | pysnmp 6.1.4 → 7.x async v1arch migration (current) |
| Phase 4 | Safety fixes: URL validation, sendfile retry, firmware integrity, upload cooldown |
| Phase 5 | HTTP error handling: SSL cert guidance, 503/504 friendly messages, download error wrap |
| Phase 6 | Simplification: fallback dedup, constants, clean imports, indentation |
