# AGENTS.md — oh-brother

> AI agent guidance. Read before writing code.

## What this is

Cross-platform Python 3 CLI to update Brother printer firmware.
SNMP discovery → XML query to Brother's Japan server → HTTP download → TCP 9100
or FTP upload.

- **Repo:** https://github.com/rvasilev/oh-brother
- **Upstream:** https://github.com/CauldronDevelopmentLLC/oh-brother (commit
  `a9c8b10`) — author Joseph Coffman. The GPLv2 header in the source is
  preserved byte-identical; changing it would be a licence breach, not a style
  nit. Header = lines 1–15 of `oh_brother.py`; `head -15 oh_brother.py | md5sum`
  must equal **`37f53a239b86f53d385d45047b8ceb07`**. If that changes, attribution
  was altered — restore it rather than reformatting around it.
- **License:** GPLv2 (`GPL-2.0-only` in packaging, which matches the header)
- **Lines:** ~1,180 in a single file (`oh_brother.py`)
- **Version:** `0.4.0`, read at runtime via `importlib.metadata` (never a
  duplicated literal) with a `0.0.0+source` fallback when run uninstalled
- **Installed as** the `oh-brother` console script via `pyproject.toml`
- **Branch:** `harden`

## Architecture

```
User → CLI args (IP, --password, --category, --model, --fw-version, --community,
        --test, --beta, --yes, --reflash, --verbose)
  │
  ├─[1] SNMP walk (BROTHER_SNMP_OID) via pysnmp 7.x async v1arch
  │     → _snmp_walk_table() → asyncio.run() wrapper → parse_snmp_table()
  │     → model, serial, spec, firmwares
  │     Bounded: SNMP_TIMEOUT × (SNMP_RETRIES + 1) per request, whole stage
  │     capped by SNMP_DEADLINE. Raises SnmpError, never sys.exit().
  │
  ├─[2] XML POST — build_firmware_xml() → Brother API (BROTHER_API_URL)
  │     → _http_post() with error handling → parse_brother_response()
  │     → version check, firmware URL (PATH)
  │     → VCHECK=1 or no-PATH → _try_version_fallback()
  │
  ├─[3] URL validation — _validate_firmware_url()
  │     checks: scheme (http/https), domain labels
  │     (brother.co.jp/.com/.eu), extension (.djf/.upd)
  │
  ├─[4] HTTP GET firmware blob via urllib with error handling
  │     → written to <name>.part; bounded by Content-Length and
  │       DOWNLOAD_HARD_CAP → _verify_firmware_integrity() (Content-Length
  │       match + 100KB minimum) → os.replace() into firmware_backups/
  │
  └─[5] Upload to printer
        ├─ TCP port 9100 — _tcp_upload() with sendfile retry loop + socket budget
        └─ FTP (when --password set — admin password as username)
        then _verify_flash() polls SNMP until the expected version comes back
```

Between firmware categories the printer is **polled** with
`_wait_for_printer_ready()` until it answers SNMP again — not slept at for a
guessed interval.

## Structure

### Pipeline

| Function | Purpose |
|---|---|
| `parse_snmp_table(table, verbose=False)` | SNMP walk result → dict with serial, model, spec, firmwares |
| `build_firmware_xml(model, spec, cat, version, beta=False)` | Build the Brother API XML request → bytes |
| `parse_brother_response(xml_bytes)` | Brother API XML → dict with version_check, firmware_url |
| `_http_post(url, data, hdrs, timeout=30)` | POST to the Brother API with error categorization (HTTP, SSL, timeout, DNS) |
| `_http_request(url, data=None, hdrs=None, timeout=30)` | Unified GET/POST, same error handling as `_http_post` |
| `update_firmware(cat, version)` | Orchestration: XML build → POST → parse → fallback → validate → download → verify → upload. Returns an exit code |
| `main()` | Entry point: argparse → gates → SNMP walk → parse → per-category loop → worst-wins aggregation. Returns `int` |

### Version handling

| Function | Purpose |
|---|---|
| `_version()` | Installed version via `importlib.metadata`; `0.0.0+source` fallback |
| `_version_tuple(v)` | Dotted numeric string → comparable tuple, or `None` if not fully numeric |
| `_decrement_version(v)` | Decrement the minor for the API fallback trick. Zero-padding preserved (`2.10`→`2.09`); a `.00` minor borrows from the major (`3.00`→`2.99`) |
| `_parse_artifact_version(filename)` | `D02FZM_124Q_crypt.djf` → `1.24`; `None` for letter-only names (LZ*/D00*) |
| `_try_version_fallback(version, cat, url, hdrs)` | Decrement + re-query for a firmware URL (shared by both fallback paths) |

### Safety, validation and retention

| Function | Purpose |
|---|---|
| `_validate_firmware_url(url)` | `(is_valid, error)` — scheme, domain *labels*, extension |
| `_verify_firmware_integrity(filepath, content_length=None)` | Content-Length match + 100KB minimum |
| `_tcp_upload(filename, ip, sock)` | TCP 9100 upload, offset-tracked sendfile retry → tri-state |
| `_firmware_backup_path(model, version, filename)` | Where a verified image is retained |
| `_remove_quietly(path)` | Delete, ignoring failure (never fails the primary operation) |
| `_retained_message(path)` | "the image is still at …" |
| `_incomplete_message(path)` | `TRANSFER INCOMPLETE — DO NOT POWER OFF; reflash from …` |
| `_interrupt_during_upload(path)` | Ctrl-C warning for the upload window |
| `_interrupt_during_verification(path, expected)` | Ctrl-C message for the verification window |

### Post-flash verification and readiness

| Function | Purpose |
|---|---|
| `_query_printer_version(ip, community, cat)` | One walk for a category's version; `None` when the printer is silent (still rebooting) |
| `_verify_flash(ip, community, cat, expected, timeout, poll)` | `('ok' \| 'mismatch' \| 'unverified', actual)` |
| `_printer_ready(ip, community)` | `True` when SNMP answers — read-only, safe immediately after a flash |
| `_wait_for_printer_ready(ip, community, timeout, poll)` | Seconds waited, or `None` at the deadline |

### SNMP

| Function | Purpose |
|---|---|
| `_snmp_walk_table(ip, community, oid)` | Async pysnmp 7.x walk wrapper (called via `asyncio.run()`). **Raises `SnmpError`** |
| `SnmpError(Exception)` | Carries `exit_code`, so the caller learns *why* rather than getting a traceback |
| `prompt(msg)` | `input()` only when stdin is a TTY — and nothing in the flash path depends on that no-op any more |

**Module constants (30):** `BROTHER_API_URL`, `BROTHER_SNMP_OID`,
`FW_VERSION_SENTINEL`, the `EXIT_*` codes, `UPLOAD_OK`/`UPLOAD_FAILED`/
`UPLOAD_INCOMPLETE`, `UPLOAD_SOCKET_TIMEOUT`, `UPLOAD_STALL_DEADLINE`,
`FTP_TIMEOUT`, `SNMP_TIMEOUT`/`SNMP_RETRIES`/`SNMP_DEADLINE`,
`FLASH_VERIFY_TIMEOUT`/`FLASH_VERIFY_POLL`, `BACKUP_DIRNAME`,
`DOWNLOAD_CHUNK`/`DOWNLOAD_HARD_CAP`, `READY_TIMEOUT`/`READY_POLL`, plus the
`reqInfo` XML template. Every timeout is a named constant with the arithmetic
written out in a comment — do not inline a duration.

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

- **Python 3.10+** — `requires-python = ">=3.10"`. CI runs 3.10 and 3.13. Both
  legs have been run locally and pass; do not assume 3.10 is untested, and do
  not "modernise" syntax past 3.10.
- **pysnmp `>=7.1.0,<8`** — async v1arch API (`walk_cmd`, `SnmpDispatcher`,
  `UdpTransportTarget.create()`). The `<8` cap is deliberate; the project has
  already absorbed two breaking major migrations.
- **`asyncio.run()` wrapper** — the CLI stays synchronous despite async SNMP
- **Procedural style** — pure functions, module-level globals (`args`, `model`,
  `spec`, `firmInfo`, `serial`)
- **No external HTTP libraries** — stdlib `urllib` only; runtime deps are pysnmp
  and nothing else
- **XML parsing** — stdlib `xml.etree.ElementTree`
- **Do not "fix" `SELIALNO`** in the XML template. That misspelling is the
  vendor's and must stay exactly as written.
- **Downloads** land as `<name>.part` in CWD, then are `os.replace()`d into
  `firmware_backups/<MODEL>/<version>/` (also CWD) after the integrity check
- **Test imports** use `importlib.util.spec_from_file_location` (loaded by file
  path; the module is `oh_brother.py`)

## Safety features

| Feature | Implementation | Guards against |
|---|---|---|
| URL validation | `_validate_firmware_url()` | Wrong-domain firmware, non-http schemes, non-firmware files. Compares domain *labels*, so `evilbrother.com` fails |
| Firmware integrity | `_verify_firmware_integrity()` | Truncated downloads (Content-Length), error pages (100KB minimum) |
| Bounded download | `DOWNLOAD_HARD_CAP` (64 MB) + declared `Content-Length` | A broken or hostile mirror filling the disk. The loop was previously unbounded |
| Sendfile retry | `_tcp_upload()` | Short TCP writes (offset retry), connection drops (zero-return detection) |
| Upload socket budget | `UPLOAD_SOCKET_TIMEOUT` (300s) + byte-progress accounting | Indefinite TCP upload hangs. The 60s predecessor bounded the whole `sendfile()` call, not each packet, so a throttling printer aborted a flash mid-write. A timeout *after* bytes were accepted is `UPLOAD_INCOMPLETE` — retained, never a silent failure |
| FTP timeout | `FTP_TIMEOUT` (30s) | `storbinary` blocking forever. A failing `QUIT` is also caught separately so it cannot discard a completed `STOR` |
| SNMP budget | `SNMP_TIMEOUT`/`SNMP_RETRIES`/`SNMP_DEADLINE` | A three-minute hang on the commonest failure (printer off). The transport's `retries` default was being inherited silently |
| Readiness poll | `_wait_for_printer_ready()` | The printer-reboot race between categories. Replaced a fixed `time.sleep(30)`, which was wrong in both directions |
| Image retention | `_firmware_backup_path()`, `_retained_message()` | Losing the only copy of an image needed to retry. Nothing is ever left under a real firmware name, and the image is deleted only after a verified flash |
| Post-flash verification | `_verify_flash()` | Trusting "the socket did not raise". TCP 9100 is fire-and-forget — a completed write is not an accepted image |

## CLI reference

```
./oh_brother.py [OPTIONS] <printer IP>

  -t, --test        Check firmware availability (no upload). Never deletes the
                    image and never needs consent — it cannot write
  -c, --category    Force a specific firmware category (MAIN, SUB1, etc.)
  -m, --model       Force a specific printer model
  -f, --fw-version  Force a specific firmware version (requires --category)
  -v, --verbose     Verbose output (SNMP dump, XML request/response, full traceback)
  --beta            Query for beta firmware (INSPECTMODE=1). Requires --yes
  -p, --password    Upload via FTP using printer admin password
  -C, --community   SNMP community string (default: public)
  -y, --yes         Skip all confirmation prompts (non-interactive mode)
  --reflash         Re-apply the current version even when the printer reports it
                    as up to date. Also what makes `--test` fetch a backup.
  --version         Print the installed version (from package metadata)
```

## Flashing gates (default-deny)

The tool never attempts an upload without explicit intent. All of these are
enforced *before* a socket is opened, and all but the downgrade check are
enforced before the 15 MB download.

| Gate | Behaviour when not satisfied |
|---|---|
| `VERSIONCHECK=1` (already current) | Terminal. No download, no upload, exit 3. `--reflash` opts back in. |
| stdin is not a TTY and `--yes` absent | Exit 9 (REFUSED). A cron job cannot silently reflash. |
| Artifact version older than installed | Exit 9. There is deliberately **no** `--allow-downgrade` flag; for a printer whose firmware is the vendor's database of record, a downgrade is never the right call. |
| Artifact version unparseable | Warns loudly and proceeds — a downgrade cannot be proven. Prints the raw filename and both parsed values. |
| Forced category (`-c`) with no installed version on record | Exit 9. Without a known installed version the downgrade check cannot run, so it refuses rather than proceeding unchecked. `-f` supplies one explicitly. |
| `-f` without `-c` | Exit 2 (USAGE). Silently ignoring the flag is worse than refusing it. |
| `--beta` without `--yes` | Exit 9. `--beta` is not its own consent token. `--test` is exempt. |
| Model/spec missing, mismatch, or bad firmware host | Exit 5. Model/spec are not interpolated into the XML as the literal `"None"`. |

## Exit-code contract

Constants at the top of the module. `main()` aggregates worst-wins — an upload
failure is never hidden behind another category's success — and prints
`FAILURE: ...` for anything that is not OK/CURRENT.

| Code | Constant | Meaning |
|---|---|---|
| 0 | `EXIT_OK` | Uploaded and verified, or `--test` fetched and verified an image |
| 1 | `EXIT_ERROR` | Unexpected internal error |
| 2 | `EXIT_USAGE` | Bad arguments (including `-f` without `-c`) |
| 3 | `EXIT_CURRENT` | Printer already current — the healthy cron no-op |
| 4 | `EXIT_PRINTER` | Printer unreachable (SNMP 161, TCP 9100, FTP 21) |
| 5 | `EXIT_VENDOR` | Brother API error / no firmware URL |
| 6 | `EXIT_DOWNLOAD` | Download or integrity check failed |
| 7 | `EXIT_UPLOAD` | Upload failed, rejected, or version mismatch |
| 8 | `EXIT_UNVERIFIED` | Uploaded but the printer did not come back in time |
| 9 | `EXIT_REFUSED` | A safety gate declined (unattended flash, downgrade, `--beta` without `--yes`, forced category whose installed version is unknown) |
| 130 | `EXIT_INTERRUPTED` | Ctrl-C outside the upload window (shell SIGINT convention) |

**Code 8 is not a failure.** A Brother laser reboots for 60–120 s after a flash,
and reporting a false FAILED there is how people end up reflashing a printer
that is already working. `_verify_flash()` returns `ok`/`mismatch`/`unverified`;
an unparseable expected version yields `unverified`, not a false `mismatch`.

## Failure classification

`_snmp_walk_table()` raises `SnmpError(message, exit_code)` rather than calling
`sys.exit(1)`. That distinction is load-bearing:

- printer never answered (off, wrong address, SNMP disabled) → **4** `EXIT_PRINTER`
- printer answered with a protocol-level error → **1** `EXIT_ERROR`

Never collapse those two: "cannot reach it" and "it refused the image" are
different problems with different fixes. A `sys.exit()` inside a library
function also forces every caller to treat `SystemExit` as an error, and hides
the failure from any handler written for `Exception`.

## Ctrl-C handling (R19)

`KeyboardInterrupt` is a `BaseException`, so it sails past `except Exception`.
The right response differs by *where* you interrupt, so there is no single
handler:

| Interrupted during | Meaning | Exit |
|---|---|---|
| the upload | printer may hold a partial image — loud `DO NOT TURN THE PRINTER OFF`, image retained | 7 |
| the post-flash check | upload already succeeded, printer rebooting — unconfirmed, so never claim success | 8 |
| anything else | nothing half-written on the printer — clean message, no traceback | 130 |

A deliberate user cancel must not be reported as `1`/"unexpected internal
error" when the tool returns structured exit codes.

## Testing

**128 tests, 22 classes.** Run: `python3 -m pytest tests/ -q`

| Class | Tests | What it covers |
|---|---|---|
| `TestCLI` | 19 (parameterized) | All boolean flags, string args, category+version combo, IP required, `--reflash` |
| `TestSafetyGates` | 9 | Default-deny: already-current terminal, non-TTY refusal, downgrade refusal, no-override-flag |
| `TestValidateFirmwareUrl` | 8 | Valid HTTP/HTTPS, .upd, wrong domain, file://, wrong ext, empty, query params |
| `TestSnmpFailureClassification` | 7 | Walk raises `SnmpError` instead of `sys.exit(1)`; off printer is exit 4, protocol error exit 1; bounded SNMP budget and deadline; rebooting printer tolerated |
| `TestParseSnmpTable` | 7 | Real printer data, multi-FW, ordering edge cases, empty table, verbose |
| `TestMainSmoke` | 7 | SNMP pipeline, model/category override, SNMP errors, category+version, readiness poll between categories |
| `TestFlashHardening` | 7 | Tri-state upload, image retention, partial-file promotion, post-flash verify |
| `TestDecrementVersion` | 7 | Normal, zero-padding preserved (`2.10`→`2.09`), zero minor borrows the major, non-numeric, empty |
| `TestPrinterReadiness` | 6 | R11: readiness poll returns fast when up, bounded deadline when not, SNMP failure reads as not-ready |
| `TestUpdateFirmware` | 6 | VCHECK=1, no-PATH, `--yes` skip, fallback succeed/fail, `--test` stops before upload |
| `TestBuildFirmwareXml` | 6 | XML structure, FIRM→MAIN mapping, IFAX→MAIN mapping, beta flag, bytes output |
| `TestVerifyFirmwareIntegrity` | 5 | Content-Length match/mismatch, too small, minimum pass, size-only |
| `TestParseBrotherResponse` | 5 | Up-to-date, update available, no PATH, no VERSIONCHECK, empty response |
| `TestHttpPost` | 5 | Success, HTTP 503, SSL cert error, timeout, DNS failure |
| `TestForcedCategoryFlags` | 5 | R12: `-f` requires `-c`, `-c` alone keeps the SNMP installed version, unknown category refused, sentinel is not a version |
| `TestTcpUpload` | 3 | Full sendfile, short-write retry, zero-return failure |
| `TestPrinterUnreachable` | 3 | R15: unresolvable/refusing printer is exit 4, not exit 1 |
| `TestInterruptHandling` | 3 | R19: Ctrl-C mid-upload (7, image retained), mid-verify (8), elsewhere (130) |
| `TestBoundedDownload` | 3 | R10: body past Content-Length aborts at the first chunk, hard cap with no header, honest download unaffected |
| `TestBetaGate` | 3 | R13: `--beta` needs `--yes`; `--yes` and `--test` pass the gate |
| `TestFailureTraceback` | 3 | R17: traceback kept, one frame by default, full chain under `--verbose` |
| `TestFtpUploadOutcome` | 1 | R9: a failed `QUIT` must not discard a completed `STOR` |

**Test infrastructure:**

- `tests/conftest.py` — mocks `pysnmp`, `pysnmp.hlapi`, `pysnmp.hlapi.v1arch`
  at `sys.modules` level
- `tests/test_oh_brother.py` — imports the module via `importlib.util` (loaded
  by file path)
- Fixture data captured from a Brother HL-L2865DW printer
- Custom mocks for `_snmp_walk_table` (async → sync table return) and
  `oh.time.sleep` (readiness and deadline paths — the fixed cooldown is gone)

> ⚠️ **pytest alone does not prove pysnmp works.** Because conftest replaces
> pysnmp with a `MagicMock`, the suite reports a clean pass even with pysnmp
> entirely absent. CI therefore also runs, *outside* pytest, both
> `oh-brother --version` and `python -c "from pysnmp.hlapi.v1arch import walk_cmd"`.
> Keep that step: it is what turns a future pysnmp 8.x rename into a red build
> instead of a failure at a user's printer at 3am.

```bash
# Full test run
python3 -m pytest tests/ -q

# With coverage
python3 -m pytest tests/ --cov=oh_brother.py --cov-report=term-missing

# Real printer integration test (safe: downloads + retains, never uploads)
python3 oh_brother.py --test --reflash <printer IP>
```

### Documentation drift

```bash
python3 scripts/check_agents_md.py    # exit 1 + a report if AGENTS.md/README drifted
```

Fails if a documented claim no longer matches the source: test and class counts,
the per-class table, exit codes, CLI flags, the constant count, the module version,
the GPL header hash, or a gate that is documented but not implemented. It also
greps for superseded phrasing. This file rots quietly otherwise — a stale
Known-issues table sends the next reader to work that is already done.

It is deliberately **not** wired into CI: run it when you touch this file, the
README, or anything that changes a count, an exit code or a flag.

### Verifying a fix

Prove a new test is RED against the unfixed source, and **read the failure
line** — a test that fails on a missing constant proves only that the symbol is
new. `assert 3 == 9` is evidence; `AttributeError: no attribute 'X'` is not.
When a test could pass for two reasons, assert the mechanism (a read count, a
call that must happen) rather than the exit code, and pick an input that
isolates the defect.

## pysnmp migration (completed)

### History

| Phase | Version | API | Notes |
|---|---|---|---|
| Upstream | pysnmp 4.x | `oneliner.cmdgen.nextCmd` | Required `asyncore` (removed Python 3.12) |
| Phase 2 | pysnmp 6.1.4 | `hlapi.walkCmd` (sync) | Last sync hlapi, pinned `==6.1.4` |
| Phase 3 (current) | pysnmp 7.1.x | `v1arch.walk_cmd` (async) | **Current.** `>=7.1.0,<8` |

### Current imports

```python
from pysnmp.hlapi.v1arch import (
    walk_cmd, CommunityData, UdpTransportTarget,
    ObjectType, ObjectIdentity, SnmpDispatcher,
)
```

`_snmp_walk_table()` is an async function using `await
UdpTransportTarget.create()` and `async for ... in walk_cmd()`. Called via
`asyncio.run()` — the CLI stays synchronous. `lexicographicMode=False` passed as
a kwarg; `ContextData` not needed for v2c. Return tuple format is identical to
6.x.

**Pass `timeout` and `retries` explicitly.** The transport defaults to
`(timeout=1, retries=5)` and a walk inherits both, so stating only `timeout`
leaves a silent 6× multiplier. When a budget is a product
(`timeout × (retries + 1)`), assert the product in a test.

## Known issues

| Issue | Status |
|---|---|
| Downloads to CWD, not a temp dir | By design — that path *is* the retained recovery image (`firmware_backups/<MODEL>/<version>/`) |
| No reachability preflight before the ~15 MB download (R16) | Unfixed (low — the image is retained, so it is waste, not loss) |
| Unused `ip` parameter in `_tcp_upload` (R18) | Unfixed (cosmetic) |
| Model name "series" suffix for D01 color lasers | Unfixed (enhancement — VCHECK=2 not handled) |
| No `--json` output | Deferred until a second consumer actually exists |
| Full `logging` refactor, full type hints, `--log-file`, `--dry-run` | Deferred by decision — exit codes already carry the cron signal |

## Changelog (fork)

| Phase | What |
|---|---|
| Phase 0 | Added `if __name__ == '__main__':` guard, extracted 3 pure functions |
| Phase 0.5 | Added `-y`/`--yes` flag (non-interactive mode) |
| Phase 1 | 31 initial tests (parse, XML, CLI) |
| Phase 1.5 | 45 tests: added `TestMainSmoke`, `TestUpdateFirmware`, collapsed CLI to parameterized |
| Phase 2 | Migrated pysnmp `oneliner` → `hlapi.walkCmd` (6.1.4), Bear review fixes |
| Phase 3 | pysnmp 6.1.4 → 7.x async v1arch migration |
| Phase 4 | Safety fixes: URL validation, sendfile retry, firmware integrity, upload cooldown |
| Phase 5 | HTTP error handling: SSL cert guidance, 503/504 friendly messages, download error wrap |
| Phase 6 | Simplification: fallback dedup, constants, clean imports, indentation |
| Phase 7 | Production hardening (branch `harden`): default-deny flashing, frozen exit-code contract, image retention, post-upload verification, packaging + CI, Ctrl-C handling, bounded SNMP/upload/download budgets, readiness polling, and the R9/R10/R11/R12/R13/R14/R17 follow-ups |
