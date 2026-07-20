# AGENTS.md — oh-brother

> AI agent guidance for working on oh-brother. Read before writing code.

## What this is

A cross-platform Python 3 CLI that updates Brother printer firmware. SNMP discovery → XML query to Brother's Japan server → HTTP download → TCP 9100 or FTP upload.

- **Repo:** https://github.com/rvasilev/oh-brother
- **Upstream:** https://github.com/josephcoffland/oh-brother (Cauldron Development LLC)
- **License:** GPLv2
- **Lines:** ~320, single file (`oh-brother.py`)

## Architecture

```
User → CLI args (IP, --password, --category, --model, --test, --beta, --dry-run, --yes)
  │
  ├─[1] SNMP walk (OID 1.3.6.1.4.1.2435.2.4.3.99.3.1.6.1.2)
  │     → model, serial, spec, firmware IDs & versions
  │
  ├─[2] XML POST to https://firmverup.brother.co.jp/kne_bh7_update_nt_ssl/ifax2.asmx/fileUpdate
  │     → version check, firmware download URL (PATH element)
  │
  ├─[3] HTTP GET firmware blob (.djf/.upd) → temp dir
  │     ⚠ PATH not returned for newer models (HL-L2865DW, HL-L2xxx series)
  │
  └─[4] Upload to printer
        ├─ TCP port 9100 (default, passwordless — Raw Port must be enabled)
        └─ FTP (when --password set — admin password as username, timeout=30s)
```

## Key external systems

| System | Protocol | Notes |
|---|---|---|
| Brother firmware API | HTTPS XML | `firmverup.brother.co.jp`, requires `User-Agent: BrHttpc/1.00` |
| Brother firmware CDN | HTTP | `update-akamai.brother.co.jp/CS/` — `.djf` / `.upd` files |
| Printer SNMP | UDP 161 | Community string (default `public`), Brother enterprise OID |
| Printer raw port | TCP 9100 | Must be enabled in printer web UI |
| Printer FTP | TCP 21 | Admin password sent as FTP username (Brother quirk) |

## Known API gap — newer models

The `fileUpdate` API returns `VERSIONCHECK` but **no `PATH`** for the HL-L2865DW and likely the entire HL-L2xxx/L3xxx series. The official Windows Firmware Update Tool (`BrMain4905.exe`) is a RAR5 SFX containing a .NET `FirmwareUpdater.exe` downloader — no embedded firmware.

**What we know:**
- Same API endpoints confirmed in `BrUpdSys.xml` inside the tool
- `verCheck` endpoint exists but doesn't list HL-L2865DW
- Firmware files use CDN path `http://update-akamai.brother.co.jp/CS/`
- Old naming: `lzXXXX_X.djf` / `.upd` — new naming: `D00XXX_X.djf`

**Remaining unknown:** Actual firmware filename/URL for HL-L2865DW. Next step: decompile `FirmwareUpdater.exe` (.NET assembly) or capture its network traffic via Wine + mitmproxy.

**Reference files:** `/tmp/BrMain4905.exe` (21 MB), `/tmp/BrMain4905_overlay.rar` (20 MB extracted overlay)

## Code conventions

- **Python 3.11** target (`pysnmp.entity.rfc3413.oneliner` — removed in PySNMP ≥6.2)
- **Procedural style** — no classes, functions only where reuse needed
- **No external HTTP libraries** — stdlib `urllib` only
- **XML parsing** — stdlib `xml.etree.ElementTree`
- **Firmware downloads** go to `tempfile.gettempdir()` — never CWD
- **`with` context managers** for all file and socket operations

## CLI reference

```
./oh-brother.py [OPTIONS] <printer IP>

  -t, --test       Check firmware availability (no download, no upload)
  -n, --dry-run    Same as --test (no download, no upload)
  -y, --yes        Skip all confirmation prompts (non-interactive)
  -c, --category   Force a specific firmware category (MAIN, SUB1, etc.)
  -m, --model      Force a specific printer model
  -f, --version    Force a specific firmware version (requires --category)
  -v, --verbose    Verbose output (SNMP dump, XML request/response)
  --beta           Query for beta firmware (INSPECTMODE=1)
  -p, --password   Upload via FTP using printer admin password
  -C, --community  SNMP community string (default: public)
```

## Testing

- **No test suite.** Testing requires a physical Brother printer on the network.
- `--test` / `--dry-run` stop after API version check — safe, no download.
- Roman's test printer: **Brother HL-L2865DW** at `192.168.88.65`, SPEC `0906`, MAIN `1.24`

```bash
# Syntax + import check
python3 -m py_compile oh-brother.py
python3 -c "from pysnmp.entity.rfc3413.oneliner import cmdgen; print('OK')"

# Safe firmware check
./oh-brother.py --dry-run --yes 192.168.88.65

# Verbose mode to inspect API responses
./oh-brother.py --dry-run --yes --verbose 192.168.88.65
```

## Changelog (fork)

| Commit | What |
|---|---|
| `d5699b5` | Removed dead `ssl.wrap_socket` monkey-patch |
| `1e94d6f` | Documented why `OS>WIN_NATIVE` is hardcoded |
| `fb49072` | Cross-platform upload (`.sendall()` loop), honest success message |
| `0e59792` | FTP catches all errors with timeout, `--test` stops before download, temp dir downloads |
| `fc6c7be` | `getaddrinfo()` in try block, `with` for sendfile open |
| `c2377af` | XML parse error handling from Brother API |
| `92147cf` | `-n`/`--dry-run` flag |
| `ed67bb5` | `with open()` + empty-file check, HTTP timeouts + error handling, `-y`/`--yes` flag |
