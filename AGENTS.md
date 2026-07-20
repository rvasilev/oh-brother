# AGENTS.md — oh-brother

> AI agent guidance for working on oh-brother. Read before writing code.

## What this is

A cross-platform Python 3 CLI that updates Brother printer firmware. SNMP discovery → XML query to Brother's Japan server → HTTP download → TCP 9100 or FTP upload.

- **Repo:** https://github.com/rvasilev/oh-brother
- **Upstream:** https://github.com/josephcoffland/oh-brother (Cauldron Development LLC)
- **License:** GPLv2
- **Lines:** ~300, single file (`oh-brother.py`)

## Architecture

```
User → CLI args (IP, --password, --category, --model, --test, --beta)
  │
  ├─[1] SNMP walk (OID 1.3.6.1.4.1.2435.2.4.3.99.3.1.6.1.2)
  │     → model, serial, spec, firmware IDs & versions
  │
  ├─[2] XML POST to https://firmverup.brother.co.jp/kne_bh7_update_nt_ssl/ifax2.asmx/fileUpdate
  │     → version check, firmware download URL
  │
  ├─[3] HTTP GET firmware blob (.djf/.upd)
  │
  └─[4] Upload to printer
        ├─ TCP port 9100 (default, passwordless — Raw Port must be enabled)
        └─ FTP (when --password set — admin password as username)
```

## Key external systems

| System | Protocol | Notes |
|---|---|---|
| Brother firmware server | HTTPS XML | `firmverup.brother.co.jp`, requires `User-Agent: BrHttpc/1.00` |
| Printer SNMP | UDP 161 | Community string (default `public`), Brother enterprise OID |
| Printer raw port | TCP 9100 | Must be enabled in printer web UI |
| Printer FTP | TCP 21 | Admin password sent as FTP username (Brother quirk) |

## Code conventions

- **Python 3.11** target (script uses `pysnmp.entity.rfc3413.oneliner` — removed in PySNMP ≥6.2, and PySNMP <6 doesn't work on Python 3.12)
- **Procedural style** — no classes, functions only where reuse needed
- **No external HTTP libraries** — stdlib `urllib` only
- **XML parsing** — stdlib `xml.etree.ElementTree`
- **Imports at top** — single block, no lazy imports

## Testing

- **No test suite exists.** Testing requires a physical Brother printer on the network.
- `--test` flag downloads firmware but skips upload — safe to run without risk.
- Before testing upload: verify Raw Port (9100) is enabled or know the admin password.
- Roman's test printer: Brother HL-L2865DW at `192.168.88.65`

## Known issues

1. **Zero error handling** — any network/SNMP/XML/FTP failure is an unhandled crash
2. **`sslwrap` hack** — monkey-patches `ssl.wrap_socket`; the SSLv3 comment is stale (code uses `PROTOCOL_TLS_CLIENT`)
3. **`socket.sendfile()`** is Linux-only — breaks the cross-platform claim
4. **No retry logic** — all operations are one-shot
5. **No dependency hashes** — `requirements.txt` uses version ranges only
6. **FTP auth is `user=password`** — Brother convention, but `ConnectionRefusedError` is the only FTP error caught
7. **No progress bars** — dot-printing for download progress, nothing for upload

## Before shipping changes

```bash
# Verify imports work
python3 -c "from pysnmp.entity.rfc3413.oneliner import cmdgen; print('OK')"

# Syntax check
python3 -m py_compile oh-brother.py

# Dry-run against test printer (safe, no upload)
./oh-brother.py --test 192.168.88.65
```

## Privacy

No PII. Printer IPs in docs/commits are fine — they're local-network addresses.
