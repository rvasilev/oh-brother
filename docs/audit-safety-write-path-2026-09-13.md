# Safety & Reliability Audit — Firmware WRITE Path

**Target:** `oh-brother.py` (606 lines, GPLv2 fork of CauldronDevelopmentLLC/oh-brother)
**Printer under test:** Brother HL-L2865DW @ 192.168.88.65, MAIN v1.24Q
**Audit date:** 2026-09-13
**Lens:** adversarial safety/reliability, unattended operation (cron/CI, no TTY, no operator)
**Question:** every path that bricks hardware, aborts mid-write, flashes the wrong/older firmware, or reports success when nothing was written.

**Method:** full source read + 6 empirical probes against the module (pysnmp mocked, no
network, no printer) + a raw CPython `socket.sendfile` timeout experiment. Test suite
baseline: `74 passed, 1 skipped`.

---

## 0. Empirical evidence (reproduced in this audit)

| Probe | Result |
|---|---|
| VCHECK=1 → end-to-end | `Firmware already up to date` → `Found firmware URL via version fallback` → download → **TCP 9100 write executed**, `update_firmware()` returned `True` |
| `_validate_firmware_url('http://evilbrother.com/CS/x.djf')` | **`True` (passes)** |
| `_validate_firmware_url('http://notbrother.com/CS/x.djf')` | **`True` (passes)** |
| `prompt()` with non-TTY stdin | **0 `input()` calls** — confirmation silently skipped |
| upload failure through `main()` | prints `No firmware update was needed`, **exit code 0** |
| one `sock.sendfile()` call, socket timeout 1.5s, 32 MB file, peer not draining | **`TimeoutError` raised after 1.50 s, 0 bytes sent** — the timeout bounds the *whole call*, not each packet |
| `_decrement_version('2.10.5')` / `('3.00')` | `'2.9.5'` (zero-padding lost) / `None` (fallback impossible) |
| filename → version parse | `D02FZM_124Q_crypt.djf`→`1.24`, `D02FZS_129Q`→`1.29`, `D01XB2_131S`→`1.31`; `D00KJY_F`/`LZ2751_L` → unparseable |

---

## 1. Ranked defects

Severity: R1–R4 CRITICAL (brick / unrecoverable), R5–R9 HIGH (false success / unattended
hazard), R10–R14 MEDIUM-HIGH/MEDIUM, R15–R19 LOW.

### R1 [CRITICAL] VCHECK=1 still writes firmware — "already current" is not a gate
`oh-brother.py:371-378`. On `version_check == '1'` the code prints "Firmware already up
to date", calls `_try_version_fallback()` (re-queries with version N-1 to obtain a URL),
and then **falls through** to download + upload. The fallback URL's version is never
compared to the installed version.
**Failure:** unattended cron re-flashes the current image on every run (verified, PROBE 5);
if the printer is *ahead* of the vendor API's DB (beta/factory/regional build, API lag), the
returned artifact is **older than installed** and is flashed anyway → downgrade. `-c`/`-f`
force it unconditionally.
**Minimal fix (ordering, not rewrite):** make the fallback URL *non-authorizing*. When
`version_check == '1'` and neither `--reflash` nor `--allow-downgrade` is set: print
`already current`, `return NOOP` — before any download. Only when an explicit flag is
present may the fallback run, and then it is still subject to R7's version check.

### R2 [CRITICAL] The confirmation prompt silently no-ops when stdin is not a TTY
`oh-brother.py:183-186` (`prompt`), used at `:446-447`, `:484-485`, `:537-538`, `:582-583`.
`prompt()` only calls `input()` **if `sys.stdin.isatty()`**; otherwise it returns and the
program continues. Verified: 0 input calls with non-TTY stdin.
**Failure:** `oh-brother.py 192.168.88.65` under cron/CI/systemd flashes with **no
confirmation at all**, even *without* `--yes`. This is the single most important defect for
the stated threat model ("run unattended by someone who is not watching") and it compounds
R1 into a silent, repeating brick loop.
**Minimal fix:** the upload gate must be `args.yes or isatty-and-confirm`. If
`not args.yes and not sys.stdin.isatty()` → print `refusing to flash unattended; pass --yes`
and `return FAILED` before opening any socket.

### R3 [CRITICAL] 60 s socket timeout bounds one whole `sendfile()` call → mid-write abort, then the image is deleted
`oh-brother.py:458` (`sock.settimeout(60)` once), `:462-464` (`except OSError`), `:476`
(unconditional `os.remove`). Python's `socket.sendfile` runs its own internal loop and is
bounded by the socket timeout **per call**, not per packet — proven: a single
`sock.sendfile(fw, offset=0)` on a 32 MB file with the peer not draining raised
`TimeoutError` after the timeout with 0 bytes accepted. `socket.timeout is TimeoutError`,
a subclass of `OSError`, so the tool's `except OSError` catches it.
**Failure (the worst outcome — mid-write brick):** a throttling printer or slow WLAN makes
one `sendfile` call exceed 60 s after N MB have already been streamed. The tool prints
`Firmware update aborted`, **deletes the firmware image**, returns `False` → the printer is
left mid-flash with a truncated image and there is no local copy to retry from. No
`shutdown(SHUT_WR)`, no acknowledgement wait.
**Minimal fix:** (a) raise the per-operation budget (e.g. `settimeout(300)`); (b) classify
"timed out / errored after `offset > 0` bytes sent" as `INCOMPLETE`, do **not** delete,
print an explicit `TRANSFER INCOMPLETE — DO NOT POWER OFF; reflash from <path>`;
(c) exit non-zero; (d) add an overall deadline with byte-progress accounting so a real hang
is distinguishable from a slow flash.

### R4 [CRITICAL] Firmware image deleted unconditionally after the upload attempt, and in `--test`
`oh-brother.py:476` (after upload) and `:437` (`--test`). Verified: after a run the `.djf` is
gone.
**Failure:** the only local recovery image is destroyed. A failed/interrupted flash cannot
be retried from the fetched file. `--test` — which the README/AGENTS recommend as the safe
check — also destroys the backup it just downloaded.
**Minimal fix:** retain under `firmware_backups/<MODEL>/<version>/<filename>` (write to
`<name>.part`, `os.replace` after integrity passes); delete only after a *verified* success
(R5). `--test` must never delete.

### R5 [HIGH] No post-upload verification — success == "the socket didn't raise"
`oh-brother.py:460`, `:471-472`, `:481-487`. TCP 9100 is fire-and-forget: the printer can
reject the image (wrong model, corrupt, busy) *after* accepting the bytes, and the tool
still prints `done` and later `Firmware update completed`. Same for FTP (`storbinary`
returning).
**Failure:** the definitive "reports success when nothing was written" path.
**Minimal fix:** after the upload, poll SNMP `FIRMVER` for that category (deadline, e.g.
300 s) and compare against the expected version parsed from the artifact filename (R7);
report `expected=… actual=…`; exit non-zero on mismatch. This is the single highest-value
reliability change.

### R6 [HIGH] Upload failure is reported as "No firmware update was needed", exit 0
`oh-brother.py:478-479` → `:585-598`; exit code is 0 on every path (`:600-602` only exits 1
for an uncaught exception). Verified: a failed upload printed
`No firmware update was needed` and the process exited **0**.
**Failure:** cron/CI cannot tell "nothing to do" from "the flash failed" — silent failure,
green pipeline, unknown printer state.
**Minimal fix:** tri-state return (`NOOP` / `OK` / `FAILED`); `sys.exit(2)` on any `FAILED`;
print `Firmware update completed` only on verified success (R5) and
`No firmware update was needed` only for a genuine no-op.

### R7 [HIGH] No artifact↔printer identity check; `model`/`spec` may be `None` → literal `"None"` in the XML
`oh-brother.py:122-123` (`modelInfo.find('NAME').text = model`), `:551-554`, `:350`.
If SNMP returns a partial table, the request literally contains `<NAME>None</NAME>`;
`_validate_firmware_url()` and `_verify_firmware_integrity()` will still pass whatever the
API returns, and it gets flashed. There is **no** comparison of the artifact to model,
spec, category, or installed version (contains R1's downgrade gap).
**Failure:** flash another model's / an older firmware → brick.
**Minimal fix:** abort before the vendor query if `not model or not spec` (unless the user
explicitly forced them); parse the artifact version from the filename
(`_(\d{3})([A-Za-z])` → major.minor, e.g. `124Q`→`1.24`) and **block upload when it is
strictly older than the installed SNMP version** unless `--allow-downgrade`; when the
version is unparseable (LZ*/D00* letter-only names), require an explicit opt-in rather than
assuming it is safe. Optionally add a platform-code↔model-family allow-list.

### R8 [HIGH] Domain allow-list is a bare string suffix → `evilbrother.com` passes
`oh-brother.py:223-225`. `parsed.netloc.endswith(d)` matches any name *ending with* the
string; verified `http://evilbrother.com/CS/x.djf` and `http://notbrother.com/CS/x.djf`
both validate. The CDN is plain HTTP (`:408`) and there is no hash/signature check.
**Failure:** an attacker controlling any registrable name ending in `brother.com` who can
MITM DNS/HTTP serves an arbitrary `.djf` that the tool downloads and flashes.
**Minimal fix:** `host = parsed.hostname; ok = host == d or host.endswith('.' + d)`;
prefer/require `https` for the download; add optional `--sha256 <hex>` pin and a PJL/engine
sanity read of the first 512 bytes (research doc §5).

### R9 [HIGH — unattended] FTP path: no timeout, bare `except Exception`, transfer==success
`oh-brother.py:466-474`. `FTP(args.ip, user=args.password)` with default (infinite)
timeout; `except Exception` swallows everything; `success = True` the moment `storbinary`
returns, with no verification. Password is sent cleartext as the FTP username.
**Failure:** a hung printer stalls the cron run indefinitely (no watchdog); errors vanish;
an unverified STOR is reported as a successful flash.
**Minimal fix:** `FTP(args.ip, user=args.password, timeout=30)`; catch specific exceptions;
treat STOR as *transfer* success only and verify via SNMP (R5); set a connect timeout.

### R10 [MEDIUM-HIGH] Download loop is unbounded and never enforces Content-Length incrementally
`oh-brother.py:417-425`. The write loop runs until the server sends EOF; there is no cap
and no early abort when the received bytes exceed the declared `Content-Length`. The
read/write loop is also not wrapped in `try/except`, so a mid-download error escapes to
`main()`'s broad handler leaving a partial file **under the real firmware name**.
**Failure:** a hostile/broken HTTP source (R8 makes this reachable) exhausts the disk; a
partial artifact named like real firmware is a foot-gun for later manual flashing. (While
building this audit, a mock body that never returned EOF wrote until `ENOSPC` — the code
path has no bound.)
**Minimal fix:** wrap the loop; `if written > int(content_length) or written > HARD_CAP:
abort`; write to `.part` + `os.replace` only after `_verify_firmware_integrity()` passes.

### R11 [MEDIUM-HIGH] Fixed 30 s cooldown between categories; printer is rebooting; only after "success"
`oh-brother.py:589-592` (and `:483` which tells the user to wait for the reboot). A Brother
laser frequently needs 60-120 s+ to finish and come back. The sleep is also skipped unless
the previous `update_firmware()` returned `True`.
**Failure:** a second category's SNMP/TCP step hits a still-rebooting printer.
**Minimal fix:** replace the fixed `time.sleep(30)` with a readiness poll (TCP 9100 connect
and/or SNMP walk succeeds), bounded by a deadline (e.g. 300 s).

### R12 [MEDIUM] `--fw-version` sentinel; `-f` without `-c` silently ignored; `-c` discards the installed version
`oh-brother.py:168-170`, `:561-562`. Default `'B0000000000'` is sent as the "installed"
version; with `-c`, `firmInfo` is replaced so the SNMP installed version is thrown away and
no comparison is possible.
**Failure:** vendor request keyed on a fake version; downgrade/forced flash with no check.
**Minimal fix:** make `-f` require `-c` (argparse error otherwise); with `-c` and no `-f`,
look up the installed version for that category from the SNMP map and abort if unknown.

### R13 [MEDIUM] `--beta` flashes beta firmware with no extra gate
`oh-brother.py:173-175`, `:119` (`INSPECTMODE=1`), `:334`/`:350`. `--beta` is enough to
download and flash pre-release firmware.
**Minimal fix:** require `--yes` (or an explicit confirm) for `--beta`; never auto-flash beta
unattended.

### R14 [MEDIUM] `_decrement_version` loses zero-padding and returns `None` for `X.00`
`oh-brother.py:189-207`. Verified `'2.10.5' → '2.9.5'` (padding lost, may not match the API)
and `'3.00'`/`'1.0'` → `None` (fallback silently impossible).
**Failure:** printers on a `.00` version can never fetch firmware and report
"No firmware update was needed"; malformed decremented versions may be rejected.
**Minimal fix:** zero-pad to the original width (`f"{minor-1:0{len(parts[1])}d}"`), and either
support a major-bump fallback or fail loudly.

### R15 [LOW-MEDIUM] `socket.getaddrinfo` outside the try
`oh-brother.py:455` — `ai = socket.getaddrinfo(...)` sits **before** the `try:` at `:456`.
**Failure:** DNS/offline raises out of `update_firmware`, bypassing the success/failure
logic and cleanup; the downloaded artifact leaks.
**Minimal fix:** move inside the `try` (or its own) and return `FAILED`.

### R16 [LOW-MEDIUM] No reachability preflight before downloading ~15 MB
`oh-brother.py:403-427` then `:454`. The tool downloads the full image before discovering the
printer is unreachable (and then deletes it).
**Minimal fix:** probe TCP 9100 (or FTP login) before the download.

### R17 [LOW] Top-level `except Exception` prints only `str(e)`, no traceback
`oh-brother.py:600-602`. Diagnosis-hostile for unattended failures.
**Minimal fix:** keep a traceback (`--verbose` → full, else short).

### R18 [LOW] `_tcp_upload(filename, ip, sock)` has an unused `ip` parameter
`oh-brother.py:232-248`. Cosmetic.

### R19 [LOW-MEDIUM] No SIGINT/SIGTERM handling
Interruption mid-write leaves a truncated image, no state flag, no recovery guidance.
**Minimal fix:** install handlers that set a flag and print
`transfer interrupted — printer needs reflash; image retained at <path>`.

---

## 2. Adjudication — REFUSE vs `--reflash` opt-in (the CRITICAL-1 behaviour question)

**Recommendation: REFUSE by default, with an explicit `--reflash` opt-in — and keep
`--allow-downgrade` strictly separate.**

Default-deny is the only defensible default for a tool whose stated deployment is unattended
cron/CI. The version-fallback trick (research doc §1.2) is a clever *read* mechanism for
recovering a download URL; it must never, on its own, authorise a *write*. "The API gave me a
URL" is not user intent.

**If the tool merely REFUSES (no flag at all):**
- ✅ No unattended reflash of current firmware; no accidental downgrade; cron becomes a real
  no-op when there is nothing to do.
- ❌ You lose the ability to re-apply firmware after a partial/failed flash of the *same*
  version, and to force a reflash after a botched update — which is exactly the recovery
  scenario R3/R4 create. You would have to patch the source to recover. That is the one real
  cost, and it is why the flag is needed.

**If the tool keeps today's implicit behaviour (fallback authorises upload):**
- ❌ Every cron run reflashes the printer for zero benefit, each run a fresh brick window.
- ❌ Downgrade is reachable with no comparison and no warning.
- ❌ `--test` (the documented safe check) is the only thing that stops it — meaning the
  "safe" workflow and the "actually-flashes" workflow differ by one flag, silently.

**Concrete decision:**
```
if result['version_check'] == '1' and not (args.reflash or args.allow_downgrade):
    print('Firmware already up to date (no reflash requested)')
    return 'NOOP'            # no download, no upload, no deletion
```
- `--reflash`: allow the fallback + upload, but require parsed artifact version **==**
  installed (a true reflash).
- `--allow-downgrade`: additionally permit artifact version **<** installed; must be a
  separate, louder flag (and implies `--reflash`).
- `--reflash` must not weaken R2 (still needs `--yes`/TTY confirm) or R5 (still verifies).

This keeps "refuse" (the safe default) *and* preserves reflash-after-failure recovery — the
best of both, at the cost of one flag.

---

## 3. Tests that prove each fix (RED before the fix)

Every test below fails against the current source; the current suite has no coverage for any
of these. File: `tests/test_oh_brother.py`.

| # | Test (to add) | Fix proved | RED now because |
|---|---|---|---|
| T1 | `test_vcheck1_does_not_upload_without_reflash` | R1 | upload currently runs (PROBE 5) |
| T2 | `test_reflash_flag_enables_current_version_upload` | R1 | flag doesn't exist |
| T3 | `test_downgrade_blocked_when_artifact_older_than_installed` | R7 | no version comparison exists |
| T4 | `test_image_retained_on_upload_failure` | R4 | `os.remove` at `:476` |
| T5 | `test_test_mode_retains_artifact` | R4 | `os.remove` at `:437` |
| T6 | `test_upload_failure_exits_nonzero` | R6 | always exit 0 (PROBE 6) |
| T7 | `test_upload_failure_message_is_not_no_update_needed` | R6 | prints exactly that (PROBE 6) |
| T8 | `test_non_tty_without_yes_refuses_to_flash` | R2 | `prompt()` no-ops (PROBE 2) |
| T9 | `test_validate_url_rejects_suffix_domain` (`evilbrother.com`, `notbrother.com`) | R8 | both return `True` (PROBE 1) |
| T10 | `test_missing_model_or_spec_aborts_before_api` | R7 | emits `<NAME>None</NAME>` |
| T11 | `test_sendfile_timeout_after_progress_marked_incomplete` | R3 | OSError → `False` → delete |
| T12 | `test_post_upload_version_verified_via_snmp` | R5 | no verification exists |
| T13 | `test_download_exceeding_content_length_aborts` | R10 | loop is unbounded (observed ENOSPC) |
| T14 | `test_beta_requires_explicit_confirmation` | R13 | `--beta` is sufficient |
| T15 | `test_cooldown_polls_readiness_not_fixed_sleep` | R11 | `time.sleep(30)` |
| T16 | `test_decrement_version_preserves_zero_padding` (`2.10.5`→`2.09.5`) | R14 | returns `2.9.5` (PROBE 4) |
| T17 | `test_ftp_uses_timeout` | R9 | no timeout passed |
| T18 | `test_getaddrinfo_failure_returns_failed_not_crash` | R15 | call sits outside `try` |

**Existing tests that encode the bug (must be rewritten, not just added):**

- `tests/test_oh_brother.py:563 test_vcheck1_fallback_succeeds` — asserts the fallback +
  download path runs on VCHECK=1 (it only stops because `test=True`). This test **pins
  CRITICAL-1 as intended behaviour**; it must be updated to pass `--reflash`, with a companion
  T1 asserting the no-flag case does not reach download.
- `tests/test_oh_brother.py:450 test_version_up_to_date` — still passes after the fix (a
  refusal returns without uploading); keep, but assert no download occurred.
- `tests/test_oh_brother.py:500 test_test_flag_stops_before_upload` — skipped; the nearest
  thing to coverage of the write gate and it does not run.

---

## 4. Fix order (minimal, single-file, stdlib-only)

1. **R2 + R6** — upload requires `--yes` or TTY; tri-state return; non-zero exit. Smallest
   diff, largest unattended-risk reduction.
2. **R1 + R7** — default-refuse on `already current`; abort on missing model/spec; add the
   `_(\d{3})` version parse and the `--reflash` / `--allow-downgrade` flags.
3. **R4** — retain the image (`.part` + `os.replace`, backups dir), delete only on verified
   success; `--test` never deletes.
4. **R3 + R5** — generous timeout + incomplete-transfer classification, and post-upload SNMP
   verification.
5. **R8 + R9 + R10** — host-label allow-list, FTP timeout, bounded download.
6. R11–R19 as follow-ups.

R1–R3 and R5–R6 are the change set that turns "probably flashed" into "verified flashed, or
failed loudly, with the recovery image still on disk". Until R1+R2 land, this tool should not
run under cron.
