# CLAUDE.md

oh-brother — Brother printer firmware updater. Single-file Python 3 CLI (~300 lines).

## Key file

- `oh-brother.py` — the entire program. 300 lines, procedural, no classes.

## Protocol

1. **SNMP discovery** — walks `1.3.6.1.4.1.2435.2.4.3.99.3.1.6.1.2` to get MODEL/SERIAL/SPEC/FIRMID/FIRMVER from printer
2. **XML POST** to `https://firmverup.brother.co.jp/kne_bh7_update_nt_ssl/ifax2.asmx/fileUpdate` with `User-Agent: BrHttpc/1.00` — sends model/spec/firmware info, gets back VERSIONCHECK + PATH
3. **HTTP GET** firmware .djf/.upd from the PATH URL
4. **Upload** via TCP 9100 (default, `socket.sendfile()`) or FTP (`ftplib`, password-as-username)

## XML request format

```xml
<REQUESTINFO>
  <FIRMUPDATETOOLINFO>
    <FIRMCATEGORY>MAIN</FIRMCATEGORY>  <!-- cat if != 'FIRM', else 'MAIN' -->
    <OS>WIN_NATIVE</OS>
    <INSPECTMODE>0</INSPECTMODE>       <!-- 1 for --beta -->
  </FIRMUPDATETOOLINFO>
  <FIRMUPDATEINFO>
    <MODELINFO>
      <NAME>HL-L2865DW</NAME>
      <SPEC>...</SPEC>
      <DRIVER>EWS</DRIVER>
      <FIRMINFO>
        <FIRM>
          <ID>MAIN</ID>                <!-- cat if != 'IFAX', else 'MAIN' -->
          <VERSION>B0000000000</VERSION>
        </FIRM>
      </FIRMINFO>
    </MODELINFO>
    <DRIVERCNT>1</DRIVERCNT>
    <LOGNO>2</LOGNO>
    <NEEDRESPONSE>1</NEEDRESPONSE>
  </FIRMUPDATEINFO>
</REQUESTINFO>
```

## Dependencies

```
pysnmp >=4, <=6.1.4  (oneliner module removed in 6.2; <6 breaks on Python 3.12)
stdlib: urllib, xml.etree, socket, ftplib, ssl, argparse, getpass
```

## Quirks

- `SELIALNO` is a Brother typo — do not "fix" it
- FIRMCATEGORY mapping: `FIRM→MAIN`, `IFAX→MAIN` for the ID sub-element
- `ssl.wrap_socket` is monkey-patched despite the SSLv3 comment being stale
- FTP sends password as the username (`FTP(host, user=password)`) — Brother convention
