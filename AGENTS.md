# AGENTS.md — oh-brother

> AI agent guidance. Read before writing code.

## What this is

Cross-platform Python 3 CLI to update Brother printer firmware. SNMP discovery → XML query to Brother's Japan server → HTTP download → TCP 9100 or FTP upload.

- **Repo:** https://github.com/rvasilev/oh-brother
- **Upstream:** https://github.com/CauldronDevelopmentLLC/oh-brother (commit `a9c8b10`) — author Joseph Coffman. The GPLv2 header in the source is preserved byte-identical; changing it would be a licence breach, not a style nit.
- **License:** GPLv2
- **Lines:** ~890, single file (`oh_brother.py`), installed as the `oh-brother` console script via `pyproject.toml`
- **Branch:** `harden`

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
- **Firmware downloads** land as `<name>.part` in CWD, then are `os.replace()`d into `firmware_backups/<MODEL>/<version>/` (in CWD) after the integrity check
- **Import-safe** — `if __name__ == '__main__':` guard
- **Test imports** use `importlib.util.spec_from_file_location` (loaded by file path; the module is `oh_brother.py`)

## Safety features

| Feature | Function | Guards against |
|---|---|---|
| URL validation | `_validate_firmware_url()` | Wrong-domain firmware, non-http schemes, non-firmware files |
| Firmware integrity | `_verify_firmware_integrity()` | Truncated downloads (Content-Length check), corrupt/error pages (100KB minimum) |
| Sendfile retry | `_tcp_upload()` | Short TCP writes (offset retry loop), connection drops (zero-return detection) |
| Upload cooldown | `main()` loop | Printer reboot race when multiple firmwares pending (30s delay) |
| HTTP error handling | `_http_post()` / `_http_request()` | 503/504 server errors, SSL cert failures, timeouts, DNS errors |
| Upload socket budget | `UPLOAD_SOCKET_TIMEOUT` (300s) + byte-progress accounting | Indefinite TCP upload hangs. The 60s predecessor bounded the whole `sendfile()` call, not each packet, so a throttling printer aborted a flash mid-write. A timeout *after* bytes were accepted is `UPLOAD_INCOMPLETE` — retained, never a silent failure |
| FTP timeout | `FTP_TIMEOUT` (30s) | `storbinary` blocking forever |
| Image retention | `_firmware_backup_path()`, `_retained_message()` | Losing the only copy of an image needed to retry. Downloads land in `<name>.part` and are `os.replace()`d into `firmware_backups/<MODEL>/<version>/` only after the integrity check; nothing is left under a real firmware name and the image is deleted only after a verified flash |
| Post-flash verification | `_verify_flash()` | Trusting "the socket did not raise". TCP 9100 is fire-and-forget — a completed write is not an accepted image |

## CLI reference

```
./oh_brother.py [OPTIONS] <printer IP>

  -t, --test       Check firmware availability (no upload)
  -c, --category   Force a specific firmware category (MAIN, SUB1, etc.)
  -m, --model      Force a specific printer model
  -f, --version    Force a specific firmware version (requires --category)
  -v, --verbose    Verbose output (SNMP dump, XML request/response)
  --beta           Query for beta firmware (INSPECTMODE=1)
  -p, --password   Upload via FTP using printer admin password
  -C, --community  SNMP community string (default: public)
  -y, --yes        Skip all confirmation prompts (non-interactive mode)
  --reflash        Re-apply the current version even when the printer reports it
                   as up to date. Also what makes `--test` fetch a backup.
  --version        Print the installed version (from package metadata)
```

## Flashing gates (default-deny)

The tool never attempts an upload without explicit intent. All of these are
enforced *before* a socket is opened.

| Gate | Behaviour when not satisfied |
|---|---|
| `VERSIONCHECK=1` (already current) | Terminal. No download, no upload, exit 3. `--reflash` opts back in. |
| stdin is not a TTY and `--yes` absent | Exit 9 (REFUSED) — a cron job cannot silently reflash. Checked before the download, so a refusal does not waste a 15 MB fetch. |
| Artifact version older than installed | Exit 9. There is deliberately **no** `--allow-downgrade` flag; for a printer whose firmware is the vendor's database of record, a downgrade is never the right call. |
| Artifact version unparseable | Warns loudly and proceeds — a downgrade cannot be proven. |
| Model/spec mismatch, or bad firmware host | Exit 5. The host check compares domain *labels*, not string suffixes. |

## Exit-code contract

Constants at the top of the module. `main()` aggregates worst-wins — an upload
failure is never hidden behind another category's success — and prints
`FAILURE: ...` for anything that is not OK/CURRENT.

| Code | Constant | Meaning |
|---|---|---|
| 0 | `EXIT_OK` | Uploaded and verified, or `--test` fetched and verified an image |
| 1 | `EXIT_ERROR` | Unexpected internal error |
| 2 | `EXIT_USAGE` | Bad arguments |
| 3 | `EXIT_CURRENT` | Printer already current — the healthy cron no-op |
| 4 | `EXIT_PRINTER` | Printer unreachable |
| 5 | `EXIT_VENDOR` | Brother API error / no firmware URL |
| 6 | `EXIT_DOWNLOAD` | Download or integrity check failed |
| 7 | `EXIT_UPLOAD` | Upload failed, rejected, or version mismatch |
| 8 | `EXIT_UNVERIFIED` | Uploaded but the printer did not come back in time |
| 9 | `EXIT_REFUSED` | A safety gate declined |

**Code 8 is not a failure.** A Brother laser reboots for 60–120 s after a flash,
and reporting a false FAILED there is how people end up reflashing a printer
that is already working. `_verify_flash()` returns `ok`/`mismatch`/`unverified`;
an unparseable expected version yields `unverified`, not a false `mismatch`.

## Testing

**91 tests, 13 classes.** Run: `python3 -m pytest tests/ -q`

| Class | Tests | What it covers |
|---|---|---|
| `TestCLI` | 19 (parameterized) | All boolean flags, string args, category+version combo, IP required, `--reflash` |
| `TestSafetyGates` | 9 | Default-deny: already-current terminal, non-TTY refusal, downgrade refusal, no-override-flag |
| `TestValidateFirmwareUrl` | 8 | Valid HTTP/HTTPS, .upd, wrong domain, file://, wrong ext, empty, query params |
| `TestParseSnmpTable` | 7 | Real printer data, multi-FW, ordering edge cases, empty table, verbose |
| `TestFlashHardening` | 7 | Tri-state upload, image retention, partial-file promotion, post-flash verify |
| `TestUpdateFirmware` | 6 | VCHECK=1, no-PATH, `--yes` skip, fallback succeed/fail, `--test` stops before upload |
| `TestMainSmoke` | 6 | SNMP pipeline, model override, category override, SNMP errors, multi-FW cooldown |
| `TestBuildFirmwareXml` | 6 | XML structure, FIRM→MAIN mapping, IFAX→MAIN mapping, beta flag, bytes output |
| `TestVerifyFirmwareIntegrity` | 5 | Content-Length match/mismatch, too small, minimum pass, size-only |
| `TestParseBrotherResponse` | 5 | Up-to-date, update available, no PATH, no VERSIONCHECK, empty response |
| `TestHttpPost` | 5 | Success, HTTP 503, SSL cert error, timeout, DNS failure |
| `TestDecrementVersion` | 5 | Normal, zero minor, 3-part, non-numeric, empty |
| `TestTcpUpload` | 3 | Full sendfile, short-write retry, zero-return failure |

**Test infrastructure:**
- `tests/conftest.py` — mocks `pysnmp`, `pysnmp.hlapi`, `pysnmp.hlapi.v1arch` at `sys.modules` level

> ⚠️ **pytest alone does not prove pysnmp works.** Because conftest replaces
> pysnmp with a `MagicMock`, the suite reports a clean pass even with pysnmp
> entirely absent. CI therefore also runs, *outside* pytest, both
> `oh-brother --version` and `python -c "from pysnmp.hlapi.v1arch import walk_cmd"`.
> Keep that step: it is what turns a future pysnmp 8.x rename into a red build
> instead of a failure at a user's printer at 3am.

- `tests/test_oh_brother.py` — imports module via `importlib.util` (loaded by file path)
- Fixture data captured from a Brother HL-L2865DW printer
- Custom mocks for `_snmp_walk_table` (async → sync table return) and `time.sleep` (cooldown verification)

```bash
# Full test run
python3 -m pytest tests/ -q

# With coverage
python3 -m pytest tests/ --cov=oh_brother.py --cov-report=term-missing

# Real printer integration test (safe: downloads + retains, never uploads)
python3 oh_brother.py --test --reflash <printer IP>
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
