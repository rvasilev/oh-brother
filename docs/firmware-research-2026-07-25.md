# Brother Printer Firmware — Complete Research Notes

**Date:** 2026-07-25  
**Researcher:** Roman Vasilev + Hermes Agent (deepseek-v4-pro)  
**Tools:** oh-brother, firmverup API, Brother CDN, SNMP, PJL header analysis

---

## 1. Firmware Distribution Architecture

### 1.1 API Endpoint
```
POST https://firmverup.brother.co.jp/kne_bh7_update_nt_ssl/ifax2.asmx/fileUpdate
Headers: Content-Type: text/xml, User-Agent: BrHttpc/1.00
```

The API accepts XML with MODEL, SPEC, SERIAL, and current VERSION. Three response modes:
- **VERSIONCHECK=0** → PATH returned (firmware URL available)
- **VERSIONCHECK=1** → firmware current, no PATH (unless you use the fallback trick)
- **VERSIONCHECK=2** → update exists but no PATH (API gap for deprecated/newer models)

### 1.2 Version-Fallback Trick (Key Discovery)
When the API returns VCHECK=1 (already current) or no PATH, send an OLDER version number. The API returns VCHECK=0 with the PATH to the CURRENT firmware.

Example: HL-L2865DW on v1.24 → send v1.23 → API returns `D02FZM_124Q_crypt.djf`, v1.24.

This works because the API checks "is there a newer version than what you sent?" — not "are you on the latest?" Sending a lower version always triggers the PATH response.

### 1.3 "series" Suffix Behavior
Some models require `NAME="<model> series"` (with literal " series" appended):

| Requires "series" | Does NOT require "series" |
|---|---|
| D01 color lasers (DCP-L3560CDW, MFC-L3760CDW, MFC-L8390CDW) | D02 mono lasers (HL-L2xxx, DCP-L2640DW) |
| D01 mono lasers (HL-L5210DN, HL-L6210DW) | D03 inkjets (DCP-J*, MFC-J*) |
| D00 older models (HL-L2375DW, HL-L2350DW) | D01 CS_ inkjets (MFC-J6540DW, etc.) |

No consistent rule — always try both.

### 1.4 Firmware CDN
```
http://update-akamai.brother.co.jp/CS/<filename>
```
Unauthenticated HTTP. Filename format varies by generation. Range requests supported — download 500 bytes to read PJL header for engine identification without downloading full firmware.

### 1.5 Origin Server (non-SNMP lookup)
```
https://origin.supportbrothercom.brother.co.jp/g/b/downloadlist.aspx?c=gb&lang=en&prod=<product_id>&os=10080&type3=375
```
Server-side rendered download pages. Product IDs use `_eu`, `_all`, or `_us` suffixes. Shows the Windows Firmware Update Tool download ID (`dlf*`), not the `.djf` filename directly.

---

## 2. Firmware Generation Taxonomy

### LZ Prefix (~pre-2020)
Mono and early color lasers. `.djf` or `.upd` extension. Non-encrypted.
- `LZ2751_L.upd` — MFC-9120CN, MFC-9320CW (shared)
- `LZ4266_Z.upd` — MFC-9140CDN, MFC-9330CDW, MFC-9340CDW
- `LZ5185_Y/Z` — HL-L2340DW (~6 MB)
- `LZ5186_B through H` — HL-L2300D (~1 MB)
- `LZ5413_N.upd` — MFC-9142CDN, MFC-9332CDW, MFC-9342CDW
- `LZ5013_Q/S` — MFC-J5620DW (engine: 8CA-U27-001), ~23.5 MB
- `LZ5172_U` — HL-1210W

### D00 Prefix (2018–2022)
Color lasers and MFCs. Non-encrypted. Shared-firmware architecture fully established.
- `D00KJY_F/T/W/ZA-ZD` — MFC-L3710CW, L3730CDN, L3750CDW, L3770CDW (**all purged Jul 2026**)
- `D00K1_A/F/H` — SUB1 firmware for MFC-L37xx (A=1.54, H=1.60)
- `D00KK0_Z` — HL-L3290CDW, DCP-L3510CDW
- `D00KK4_G/Y` — HL-L3210CW, HL-L3230CDW
- `D00L6U_J/V/Y` — MFC-L2717DW, MFC-L2710DW
- `D00L6V_X/ZD` — MFC-L2750DW, HL-L2325DW
- `D00L6W_G/J` — MFC-L2750DW SUB1
- `D00L6T_ZJ` — HL-L2350DW, HL-L2375DW (needs "series")
- `D0061G_S/T/U` — MFC-J5330DW (W purged)
- `D00P0W_C.upd` — HL-J6000DW

### D01 Prefix (2022–2023)
Transitional generation. Some CS_ prefix variants. Mixed encrypted/non-encrypted.
- `D01X67_126Q` — MFC-L3780CDW (purged)
- `D01X67_146Z` — DCP-L3560CDW, MFC-L3760CDW, MFC-L8390CDW (engine: FCLFB_TPUSR, 32.6 MB)
- `D01XB2_131S` — HL-L5210DN, HL-L5215DW, HL-L6210DW (needs "series")
- `D018S6_122N` — DCP-T720DW (ink tank)
- CS_ variants:
  - `CS_D01NPF_115J` — MFC-J6540DW (engine: M19REGLOUSA, 22 MB); 118L purged
  - `CS_D01NPG_126S` — MFC-J6940DW
  - `CS_D01NPJ_152R` — MFC-J6955DW
  - `CS_D01NPC_123P` — MFC-J5740DW
  - `CS_D01FEK_143Q` — MFC-J4440DW
  - `CS_D01FEL_143Q` — MFC-J4340DW (sibling of FEK)
  - `CS_D01R89_120N` — MFC-J1010DW
  - `CS_D01R8A_118M` — DCP-J1050DW (sibling of R89)
  - `CS_D01R8C_115K` — DCP-J1140DW (sibling of R89/R8A)

### D02 Prefix (2023+)
Encrypted mono lasers. `_crypt` suffix on some variants. Engine codes use 4-letter platform ID.
- `D02FZM_124Q_crypt.djf` / `D02FZM_124Q.djf` — HL-L2400DW, HL-L2447DW, HL-L2460DW, HL-L2865DW (engine: ELLEPR1LUSA, 15.3 MB)
- `D02FZS_129Q.djf` — DCP-L2640DW, MFC-L2800DW (engine: ELLEFB2NUSA, 15.3 MB)
- `D02WRK_111H.djf` — HL-L1230W, HL-L1232W (engine: ESLPLED_USA, 10.8 MB)

### D03 Prefix (2024+)
Inkjet printers. Largest files (24–43 MB). No "series" suffix needed.
- `D030A1_116L.djf` — DCP-J1260W
- `D030A2_114K.djf` — DCP-J1310DW, DCP-J1360DW (engine: MJ908___JPN, 24.2 MB)
- `D034LT_111H.djf` — DCP-T580DW (ink tank)
- `D034LU_112H.djf` — DCP-T780DW (ink tank)
- `D0356N_112G.djf` — MFC-J6960DW, MFC-J6975DW (engine: M21BASE_USA, 42.8 MB)

---

## 3. Engine Code Patterns

Engine codes follow a platform+variant+region format:
- `ELLEPR1LUSA` — ELLE platform, PR1L variant (HL print-only), USA region
- `ELLEFB2NUSA` — ELLE platform, FB2N variant (DCP/MFC scan-enabled), USA region
- `ESLPLED_USA` — ESLE platform, PLED variant (entry-level), USA region
- `FCLFB_TPUSR` — FCLF platform, B_TP variant (color laser), USR region
- `M19REGLOUSA` — M19 platform, REGLO variant, USA region
- `M21BASE_USA` — M21 platform, BASE variant, USA region
- `MJ908___JPN` — MJ908 platform, JPN region
- `8CA-U27-001` — 8C platform, U27 variant
- `8CH-213-001` — 8C platform, 213 variant

---

## 4. Shared-Firmware Architecture

Brother uses identical firmware binaries across model families, differentiated by hex Customizing codes at flash time:

| Firmware | Models | Engine |
|---|---|---|
| D02FZM_124Q | HL-L2400DW, L2447DW, L2460DW, L2865DW | ELLEPR1LUSA |
| D02FZS_129Q | DCP-L2640DW, MFC-L2800DW | ELLEFB2NUSA |
| D00KJY_F | MFC-L3710CW, L3730CDN, L3750CDW, L3770CDW | (color laser) |
| D01X67_146Z | DCP-L3560CDW, MFC-L3760CDW, MFC-L8390CDW | FCLFB_TPUSR |
| D01XB2_131S | HL-L5210DN, L5215DW, L6210DW | (mono laser) |
| D02WRK_111H | HL-L1230W, L1232W | ESLPLED_USA |
| D030A2_114K | DCP-J1310DW, J1360DW | MJ908___JPN |
| D0356N_112G | MFC-J6960DW, J6975DW | M21BASE_USA |

**Key insight:** The ELLE platform has only TWO firmware variants — FZM (HL print-only) and FZS (DCP/MFC scan-enabled). The MFC fax functionality is enabled via Customizing code, not a separate firmware build.

---

## 5. Methodology Reference

### To find firmware for any Brother model:
1. POST to `fileUpdate` with `<NAME>MODEL</NAME>`, `<SPEC>0001</SPEC>`, `<VERSION>1.00</VERSION>`
2. If VCHECK=2 with no PATH → retry with `NAME="MODEL series"`
3. If VCHECK=1 or no PATH → decrement version and retry
4. If VCHECK=0 → PATH contains the firmware filename

### To verify firmware identity:
```bash
curl -r 0-500 http://update-akamai.brother.co.jp/CS/<filename> | head -c 500
```
PJL header contains engine code after `DATA=`.

### To find older firmware versions:
Brute-force the last letter in the filename (A→Z). Only current versions typically remain — Brother aggressively purges.

### To download firmware for backup:
```bash
curl -O http://update-akamai.brother.co.jp/CS/<filename>
```

---

## 6. Brother Firmware Purging

Confirmed purged from CDN as of 2026-07-25:
- All D00KJY variants (T through ZD) — MFC-L37xx family entirely gone
- D0061G_W — MFC-J5330DW latest version
- CS_D01NPF_118L — MFC-J6540DW v1.18
- D01X67_126Q — MFC-L3780CDW
- All D02FZM letter variants before Q (A through P)

Only the CURRENT version letter remains on CDN for any given firmware code. Old firmware exists only in community archives (Mega.nz, Reddit).

---

## 7. oh-brother Implementation

**Feature:** Version-fallback in `update_firmware()`
**Commit:** e445a2e on master (Jul 2026)
**Tests:** 52 pass, 1 skipped

**Behavior:** When API returns VCHECK=1 or no PATH:
1. Calls `_decrement_version()` to get previous version (1.24→1.23)
2. Retries API with decremented version
3. If PATH returned → downloads firmware (the current version, not the decremented one)
4. If still no PATH → reports error

**Limitations:**
- Only gets current/latest firmware (Brother purges old versions)
- D02+ firmware is encrypted (backup, not analysis)
- Some models deprecated from API entirely (VCHECK=2, no PATH with or without series)

---

## 8. Unresolved / Future Work

1. **Community firmware archive** — coordinate with u/ZaVoQQ's Mega.nz archive
2. **D02/D03 decryption** — requires hardware bootloader extraction (not practical with current tools)
3. **Automated firmware monitoring** — cron job that queries API for new versions
4. **Structured model database** — SQLite/JSON index mapping model→SPEC→firmware→engine
5. **Canonical community reference** — single Reddit post or GH Pages consolidating all findings
