#!/usr/bin/env python3
#
# Oh Brother, Brother printer firmware update program
# Copyright (C) 2015-2023 Cauldron Development LLC
# Author Joseph Coffland
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 2
# of the License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.

from pysnmp.hlapi.v1arch import (
    walk_cmd, CommunityData, UdpTransportTarget,
    ObjectType, ObjectIdentity, SnmpDispatcher,
)
import urllib.request, urllib.error, urllib.parse
import xml.etree.ElementTree as ET
import argparse
import re
import asyncio
import sys
import socket
import ssl
import os
import time
from ftplib import FTP, all_errors
from urllib.parse import urlparse


# Yes indeed, "SELIALNO"
# (as used both in this here document and in script parts below)
# is a spelling issue crime committed by original vendor parts
# and thus expected to remain exactly as wrongly written.
# Thus it obviously should *not* be "corrected" here.
reqInfo = '''
<REQUESTINFO>
  <FIRMUPDATETOOLINFO>
    <FIRMCATEGORY></FIRMCATEGORY>
    <OS>WIN_NATIVE</OS>
    <INSPECTMODE></INSPECTMODE>
  </FIRMUPDATETOOLINFO>
  <FIRMUPDATEINFO>
    <MODELINFO>
      <NAME></NAME>
      <SPEC></SPEC>
      <DRIVER>EWS</DRIVER>
      <FIRMINFO>
        <FIRM></FIRM>
      </FIRMINFO>
    </MODELINFO>
    <DRIVERCNT>1</DRIVERCNT>
    <LOGNO>2</LOGNO>
    <NEEDRESPONSE>1</NEEDRESPONSE>
  </FIRMUPDATEINFO>
</REQUESTINFO>
'''

BROTHER_API_URL = (
    'https://firmverup.brother.co.jp/'
    'kne_bh7_update_nt_ssl/ifax2.asmx/fileUpdate'
)
BROTHER_SNMP_OID = '1.3.6.1.4.1.2435.2.4.3.99.3.1.6.1.2'

# Exit-code contract for update_firmware()/main().
EXIT_OK         = 0
EXIT_ERROR      = 1
EXIT_USAGE      = 2
EXIT_CURRENT    = 3
EXIT_PRINTER    = 4
EXIT_VENDOR     = 5
EXIT_DOWNLOAD   = 6
EXIT_UPLOAD     = 7
EXIT_UNVERIFIED = 8
EXIT_REFUSED    = 9

# Upload outcome classification (see _tcp_upload / update_firmware).
# A boolean True still means a clean transfer; UPLOAD_INCOMPLETE marks a
# transfer that stopped after the printer had already accepted some bytes.
UPLOAD_OK = True
UPLOAD_FAILED = False
UPLOAD_INCOMPLETE = 'incomplete'

# Upload timing budgets (seconds).
UPLOAD_SOCKET_TIMEOUT = 300       # bounds one sendfile() call
UPLOAD_STALL_DEADLINE = 3600      # bounds the whole transfer
FTP_TIMEOUT = 30                  # bounds a hung FTP session

# Post-flash verification window (seconds). A Brother laser reboots after a
# flash and SNMP is typically unavailable for 60-120 seconds.
FLASH_VERIFY_TIMEOUT = 300
FLASH_VERIFY_POLL = 5

# Local recovery-image directory.
BACKUP_DIRNAME = 'firmware_backups'


def parse_snmp_table(table, verbose=False):
    """Parse SNMP walk result table into model/serial/spec/firmware info.
    
    table: list of list of (oid, value) tuples from SNMP walk
    Returns: dict with keys: serial, model, spec, firmwares (list of {cat, version})
    """
    if verbose:
        print(table)
    
    serial = None
    model = None
    spec = None
    firmId = None
    firmwares = {}
    
    for row in table:
        for name, value in row:
            value = str(value)
            if value.find('=') != -1:
                name, value = value.split('=', 1)
                value = value.strip(' "\r\n')
                if name == 'MODEL':
                    model = value
                if name == 'SERIAL':
                    serial = value
                if name == 'SPEC':
                    spec = value
                if name == 'FIRMID':
                    firmId = value
                if name == 'FIRMVER' and firmId and value:
                    firmwares[firmId] = {'cat': firmId, 'version': value}
    
    return {
        'serial': serial,
        'model': model,
        'spec': spec,
        'firmwares': list(firmwares.values()),
    }


def build_firmware_xml(model, spec, category, version, beta=False):
    """Build the XML request body for Brother's firmware update API.
    
    Returns: bytes (UTF-8 encoded XML)
    """
    # Use the module-level reqInfo template
    xml = ET.ElementTree(ET.fromstring(reqInfo))
    
    toolInfo = xml.find('FIRMUPDATETOOLINFO')
    toolInfo.find('FIRMCATEGORY').text = category if category != 'FIRM' else 'MAIN'
    toolInfo.find('INSPECTMODE').text = '1' if beta else '0'
    
    modelInfo = xml.find('FIRMUPDATEINFO/MODELINFO')
    modelInfo.find('NAME').text = model
    modelInfo.find('SPEC').text = spec
    
    firm = modelInfo.find('FIRMINFO/FIRM')
    ET.SubElement(firm, 'ID').text = category if category != 'IFAX' else 'MAIN'
    ET.SubElement(firm, 'VERSION').text = version
    
    return ET.tostring(xml.getroot(), encoding='utf8')


def parse_brother_response(xml_bytes):
    """Parse Brother firmware API XML response.
    
    Returns: dict with keys:
        version_check: str or None — '1' means up to date
        firmware_url: str or None — download URL if update available
    """
    
    try:
        xml = ET.fromstring(xml_bytes)
    except ET.ParseError as e:
        return {'version_check': None, 'firmware_url': None, 'parse_error': str(e)}
    
    version_check = xml.find('FIRMUPDATEINFO/VERSIONCHECK')
    version_check = version_check.text if version_check is not None else None
    
    firmware_url = xml.find('FIRMUPDATEINFO/PATH')
    firmware_url = firmware_url.text if firmware_url is not None else None
    
    return {'version_check': version_check, 'firmware_url': firmware_url}

# Parse args
usage = '%(prog)s [OPTIONS] <printer IP address>'
description = 'A platform independent tool for updating Brother firmwares'

parser = argparse.ArgumentParser(usage = usage, description = description)

parser.add_argument('ip', metavar = 'IP', help = 'printer IP address')
parser.add_argument('-v', '--verbose', action = 'store_true',
                    help = 'Verbose output')
parser.add_argument('-c', '--category',
                    help = 'Force a specific firmware category')
parser.add_argument('-m', '--model',
                    help = 'Force a specific printer model')
parser.add_argument('-C', '--community', default = 'public',
                    help = 'SNMP community (default: %(default)s)')
parser.add_argument('-f', '--fw-version', default = 'B0000000000',
                    help = 'Force a specific firmware version, must be used '
                    'with --category')
parser.add_argument('-t', '--test', action = 'store_true',
                    help = 'Test only, don\'t do upgrades')
parser.add_argument('--beta', action = 'store_true',
                    help = 'Download the latest beta firmware instead of the '
                    'default stable version.')
parser.add_argument('-p', '--password',
                    help = 'Upload firmware via FTP using printer admin password '
                    '(default is passwordless upload via TCP port 9100)')
parser.add_argument('-y', '--yes', action = 'store_true',
                    help = 'Skip all confirmation prompts (non-interactive mode)')
parser.add_argument('--reflash', action = 'store_true',
                    help = 'Re-apply the current firmware version even when the '
                    'printer already reports it as up to date')


def prompt(msg):
    """Show a prompt if stdin is a TTY; otherwise silently skip."""
    if sys.stdin.isatty():
        input(msg)


def _decrement_version(version_str):
    """Decrement the minor version number for API fallback.
    
    When the API returns VCHECK=1 (already current), retrying with
    an older version forces it to return the current firmware PATH.
    Example: '1.24' -> '1.23', '2.10' -> '2.09'.
    
    Returns: str or None if version can't be parsed/decremented.
    """
    try:
        parts = version_str.split('.')
        if len(parts) >= 2:
            minor = int(parts[1])
            if minor > 0:
                parts[1] = str(minor - 1)
                return '.'.join(parts)
    except (ValueError, IndexError):
        pass
    return None


def _version_tuple(version_str):
    """Parse a dotted numeric version into a comparable tuple.

    Returns: tuple of ints, or None if the string is not fully numeric.
    """
    if not version_str:
        return None
    try:
        return tuple(int(part) for part in str(version_str).split('.'))
    except (ValueError, TypeError):
        return None


_ARTIFACT_VERSION_RE = re.compile(r'_(\d{3})([A-Za-z])')


def _parse_artifact_version(filename):
    """Extract a firmware version from an artifact filename.

    Brother filenames encode the version as three digits followed by a
    letter, e.g. D02FZM_124Q_crypt.djf -> '1.24'. Letter-only names such
    as D00KJY_F or LZ2751_L do not match.

    Returns: version string like '1.24', or None when unparseable.
    """
    if not filename:
        return None
    match = _ARTIFACT_VERSION_RE.search(filename)
    if not match:
        return None
    digits = match.group(1)
    return '%s.%s' % (digits[0], digits[1:])


def _validate_firmware_url(url):
    """Validate firmware download URL for safety.
    
    Checks: non-empty, http/https scheme, known Brother CDN domain,
    firmware file extension (.djf or .upd).
    
    Returns: (is_valid: bool, error_message: str or None)
    """
    if not url:
        return False, "empty URL"
    parsed = urlparse(url)
    if parsed.scheme not in ('http', 'https'):
        return False, "unexpected scheme: %s" % parsed.scheme
    allowed_domains = ('brother.co.jp', 'brother.com', 'brother.eu')
    host = parsed.hostname or ''
    if not any(host == d or host.endswith('.' + d) for d in allowed_domains):
        return False, "unexpected domain: %s" % parsed.netloc
    path = parsed.path.lower()
    if not (path.endswith('.djf') or path.endswith('.upd')):
        return False, "unexpected file type: %s" % parsed.path
    return True, None


def _tcp_upload(filename, ip, sock):
    """Upload firmware file to printer via TCP port 9100 with retry.

    Uses sendfile with offset tracking for short-write resilience. The socket
    timeout bounds one sendfile() call; a wall-clock deadline bounds the whole
    transfer, so a genuine hang is distinguishable from a slow-but-progressing
    flash. A connection lost after bytes were already accepted is reported as
    incomplete, never as a clean failure.

    Returns: True on success, False on failure, UPLOAD_INCOMPLETE if the
    printer accepted part of the image and then stalled or dropped.
    """
    fw_size = os.path.getsize(filename)
    deadline = time.monotonic() + UPLOAD_STALL_DEADLINE
    with open(filename, 'rb') as fw:
        offset = 0
        while offset < fw_size:
            if time.monotonic() > deadline:
                print('Error: firmware upload exceeded the time budget '
                      '(sent %d of %d bytes)' % (offset, fw_size))
                if offset > 0:
                    return UPLOAD_INCOMPLETE
                return UPLOAD_FAILED
            try:
                sent = sock.sendfile(fw, offset=offset)
            except OSError as e:
                print('Error: firmware upload interrupted: %s' % e)
                if offset > 0:
                    return UPLOAD_INCOMPLETE
                return UPLOAD_FAILED
            if sent == 0:
                print('Error: connection closed during firmware upload '
                      '(sent %d of %d bytes)' % (offset, fw_size))
                if offset > 0:
                    return UPLOAD_INCOMPLETE
                return UPLOAD_FAILED
            offset += sent
    return UPLOAD_OK


def _verify_firmware_integrity(filepath, content_length=None):
    """Verify downloaded firmware file integrity.
    
    Checks: minimum size (100KB), Content-Length match if provided.
    Returns: (is_valid: bool, error_message: str or None)
    """
    MIN_FIRMWARE_SIZE = 102400  # 100KB — no real Brother firmware is smaller
    
    try:
        actual_size = os.path.getsize(filepath)
    except OSError as e:
        return False, "cannot stat file: %s" % e
    
    if actual_size < MIN_FIRMWARE_SIZE:
        return False, "file too small: %d bytes (minimum %d)" % (
            actual_size, MIN_FIRMWARE_SIZE)
    
    if content_length is not None:
        expected_size = int(content_length)
        if actual_size != expected_size:
            return False, "size mismatch: expected %d bytes, got %d" % (
                expected_size, actual_size)
    
    return True, None


def _remove_quietly(path):
    """Best-effort file removal that never masks the original error."""
    try:
        os.remove(path)
    except OSError:
        pass


def _firmware_backup_path(model_name, version, filename):
    """Build the retained recovery path for a downloaded firmware image.

    Layout: firmware_backups/<MODEL>/<version>/<filename>. Components are
    sanitized so hostile model/version strings cannot escape the directory.
    """
    def _sanitize(part):
        cleaned = re.sub(r'[^A-Za-z0-9._-]+', '_', str(part or 'unknown'))
        return cleaned.strip('._') or 'unknown'

    return os.path.join(
        BACKUP_DIRNAME, _sanitize(model_name), _sanitize(version),
        os.path.basename(filename),
    )


def _retained_message(path):
    """Operator-facing notice that the image survived a failed flash."""
    return (
        'Firmware image retained at: %s\n'
        'Do NOT power off the printer. Re-run this tool to retry from the '
        'retained image once power and network are stable.'
        % os.path.abspath(path)
    )


def _incomplete_message(path):
    """Explicit warning for a transfer cut off after partial acceptance."""
    return ('TRANSFER INCOMPLETE — DO NOT POWER OFF; reflash from %s'
            % os.path.abspath(path))


def _query_printer_version(ip, community, cat):
    """Read one firmware category's version with a single SNMP walk.

    Returns the version string, or None when the printer cannot be reached
    (still rebooting) or does not report the category.
    """
    try:
        table = asyncio.run(_snmp_walk_table(ip, community, BROTHER_SNMP_OID))
    except (Exception, SystemExit):
        return None
    info = parse_snmp_table(table)
    for fw in info['firmwares']:
        if fw['cat'] == cat:
            return fw['version']
    return None


def _verify_flash(ip, community, cat, expected_version,
                  timeout=None, poll=None):
    """Poll the printer until it is back, then compare firmware versions.

    A Brother laser reboots after a flash, so readiness is polled within a
    bounded window before the version is read.

    Returns (status, actual) where status is one of:
        'ok'         — a completed read matched the expected version
        'mismatch'   — a completed read disagreed with the expected version
        'unverified' — the printer did not come back within the deadline
    """
    if timeout is None:
        timeout = FLASH_VERIFY_TIMEOUT
    if poll is None:
        poll = FLASH_VERIFY_POLL

    expected = _version_tuple(expected_version)
    if expected is None:
        return 'unverified', None

    deadline = time.monotonic() + timeout
    while True:
        actual = _query_printer_version(ip, community, cat)
        if actual is not None:
            if _version_tuple(actual) == expected:
                return 'ok', actual
            return 'mismatch', actual
        if time.monotonic() >= deadline:
            return 'unverified', actual
        time.sleep(poll)


def _http_post(url, data, hdrs, timeout=30):
    """POST data to a URL with comprehensive error handling.
    
    Returns: (response_bytes, None) on success, (None, error_message) on failure.
    Handles: HTTP errors (4xx/5xx), SSL certificate errors, timeouts, DNS failures.
    """
    return _http_request(url, data, hdrs, timeout=timeout)


def _http_request(url, data=None, hdrs=None, timeout=30):
    """HTTP request (POST if data provided, GET otherwise) with error handling.
    
    Returns: (response_bytes, None) on success, (None, error_message) on failure.
    """
    try:
        req = urllib.request.Request(url, data, hdrs) if data else \
             urllib.request.Request(url, headers=hdrs or {})
        response = urllib.request.urlopen(req, timeout=timeout)
        return response.read(), None
    except urllib.error.HTTPError as e:
        return None, (
            "HTTP %d (%s) from Brother server — "
            "the firmware service may be temporarily unavailable. "
            "Try again later." % (e.code, e.reason)
        )
    except urllib.error.URLError as e:
        reason = e.reason
        if isinstance(reason, ssl.SSLCertVerificationError):
            return None, (
                "SSL certificate verification failed — your Python install "
                "may be missing CA certificates.\n"
                "  macOS: run /Applications/Python*/Install Certificates.command\n"
                "  Linux: install ca-certificates package\n"
                "  Or: pip install certifi"
            )
        if isinstance(reason, socket.timeout):
            return None, (
                "Connection timed out — check your network and "
                "try again. The Brother firmware server may be slow."
            )
        return None, (
            "Network error: %s — check your internet connection "
            "and try again." % reason
        )


def _try_version_fallback(version, cat, url, hdrs):
    """Try to get firmware URL by requesting an older version.
    
    Decrements the version number and queries the Brother API.
    Returns firmware URL string on success, None on failure.
    """
    fallback_ver = _decrement_version(version)
    if not fallback_ver:
        return None
    if args.verbose:
        print('Retrying with version %s to get firmware URL...' % fallback_ver)
    req = build_firmware_xml(model, spec, cat, fallback_ver, beta=args.beta)
    resp, err = _http_post(url, req, hdrs)
    if resp is None:
        print('Error on fallback: %s' % err)
        return None
    if args.verbose:
        print('fallback response: %s' % resp)
    result = parse_brother_response(resp)
    return result.get('firmware_url')


def update_firmware(cat, version):
  global args

  # R7: never interpolate model/spec=None into the vendor XML.
  forced = (getattr(args, 'category', None) and
            getattr(args, 'fw_version', None) and
            args.fw_version != 'B0000000000')
  if not forced and (not model or not spec):
    print('REFUSING to query the vendor server: missing model or spec '
          '(model=%r, spec=%r).' % (model, spec))
    print('Re-run with --model and valid SNMP data, or force -c/-f explicitly.')
    return EXIT_REFUSED

  print('Updating %s version %s' % (cat, version))

  requestInfo = build_firmware_xml(model, spec, cat, version, beta=args.beta)

  if args.verbose: print('request: %s' % requestInfo)

  # Request firmware data
  url = BROTHER_API_URL
  hdrs = {'Content-Type': 'text/xml', 'User-Agent': 'BrHttpc/1.00'}

  print('Looking up printer firmware info at vendor server...')
  sys.stdout.flush()

  response, http_err = _http_post(url, requestInfo, hdrs)
  if response is None:
    print('Error: %s' % http_err)
    return EXIT_VENDOR

  print('done')

  if args.verbose: print('response: %s' % response)

  result = parse_brother_response(response)
  if result['version_check'] == '1':
    print('Firmware already up to date')
    if not getattr(args, 'reflash', False):
      # R1: already current is terminal unless --reflash was given.
      return EXIT_CURRENT
    firmwareURL = _try_version_fallback(version, cat, url, hdrs)
    if firmwareURL:
      print('Found firmware URL via version fallback')
    else:
      return EXIT_VENDOR
  elif result['firmware_url'] is None:
    print('No firmware update info path found '
          '(newer Brother models require version fallback)')
    firmwareURL = _try_version_fallback(version, cat, url, hdrs)
    if firmwareURL:
      print('Found firmware URL via version fallback')
    else:
      return EXIT_VENDOR
  else:
    firmwareURL = result['firmware_url']

  # Validate firmware URL before downloading
  valid, err = _validate_firmware_url(firmwareURL)
  if not valid:
    print('Error: firmware URL validation failed: %s' % err)
    print('URL: %s' % firmwareURL)
    return EXIT_VENDOR

  # Extract filename from URL (strip query parameters)
  filename = os.path.basename(urlparse(firmwareURL).path)
  if not filename:
    print('Error: could not extract filename from firmware URL')
    return EXIT_VENDOR

  # R7: refuse a known-older artifact; warn loudly when unparseable.
  artifact_version = _parse_artifact_version(filename)
  installed = _version_tuple(version)
  artifact = _version_tuple(artifact_version)
  if artifact_version is None or installed is None or artifact is None:
    print('WARNING: could not verify firmware version from artifact name!')
    print('         raw artifact filename: %s' % filename)
    print('         parsed artifact version: %r (installed: %r)'
          % (artifact_version, version))
    print('         Proceeding without a downgrade check — verify manually!')
  elif artifact < installed:
    print('REFUSING to flash: artifact version %s is OLDER than the installed '
          'version %s.' % (artifact_version, version))
    print('Artifact: %s' % filename)
    print('This looks like a downgrade; there is no override flag.')
    return EXIT_REFUSED

  # R2: refuse to flash unattended with no explicit consent, BEFORE the
  # ~15 MB download. --test is non-destructive and needs no consent.
  if not args.test and not args.yes and not sys.stdin.isatty():
    print('REFUSING to flash firmware unattended.')
    print('No --yes flag was given and stdin is not a terminal.')
    print('Re-run with --yes for unattended use, or from an interactive terminal.')
    return EXIT_REFUSED

  # Download firmware
  print('Downloading firmware file %s from vendor server...' % filename)
  sys.stdout.flush()

  part_filename = filename + '.part'

  try:
    req = urllib.request.Request(firmwareURL)
    response = urllib.request.urlopen(req, timeout=30)
  except urllib.error.HTTPError as e:
    print('Error: HTTP %d (%s) from Brother CDN — try again later.' % (e.code, e.reason))
    return EXIT_DOWNLOAD
  except urllib.error.URLError as e:
    print('Error: download failed — %s' % e.reason)
    return EXIT_DOWNLOAD

  content_length = response.headers.get('Content-Length')

  try:
    with open(part_filename, 'wb') as f:
      while True:
        block = response.read(102400)
        if not block: break
        f.write(block)
        sys.stdout.write('.')
        sys.stdout.flush()
  except OSError as e:
    print()
    print('Error: firmware download interrupted — %s' % e)
    _remove_quietly(part_filename)
    return EXIT_DOWNLOAD

  print('done')

  # Verify before promoting the partial file to a real firmware name.
  valid, err = _verify_firmware_integrity(part_filename, content_length=content_length)
  if not valid:
    print('Error: firmware integrity check failed: %s' % err)
    _remove_quietly(part_filename)
    return EXIT_DOWNLOAD

  # Promote the verified image into its retained recovery location.
  backup_path = _firmware_backup_path(model, artifact_version or version, filename)
  try:
    os.makedirs(os.path.dirname(backup_path), exist_ok=True)
    os.replace(part_filename, backup_path)
  except OSError as e:
    print('Error: could not store firmware backup: %s' % e)
    _remove_quietly(part_filename)
    return EXIT_DOWNLOAD
  filename = backup_path

  if args.test:
    print('Test mode: no upload attempted.')
    print('Firmware image retained at: %s' % os.path.abspath(filename))
    return EXIT_OK

  print('About to upload the firmware to printer.')
  print('This is a dangerous action since it is potentially destructive.')
  print('Thus please double-check / review to ensure that:')
  print('- firmware file version is compatible with your hardware')
  print('- network connection is reliable (prefer wired connection to WLAN)')
  print('- power is reliable')
  if not args.yes:
    prompt('Press Ctrl-C to prevent upgrade or Enter to continue...')

  # Upload firmware to printer
  print('Now uploading firmware to printer (DO NOT REMOVE POWER!)...')
  sys.stdout.flush()

  upload_result = UPLOAD_FAILED
  if args.password is None:
    ai = socket.getaddrinfo(args.ip, 9100, proto=socket.SOL_TCP)[0]
    try:
      with socket.socket(ai[0], ai[1], ai[2]) as sock:
        sock.settimeout(UPLOAD_SOCKET_TIMEOUT)
        sock.connect(ai[4])
        upload_result = _tcp_upload(filename, args.ip, sock)

    except OSError as e:
      print('Firmware update aborted due to error while uploading')
      print(e)
  else:
    try:
      ftp = FTP(args.ip, user = args.password, timeout = FTP_TIMEOUT) # Yes send password as user
      with open(filename, 'rb') as fw:
        ftp.storbinary('STOR ' + os.path.basename(filename), fw)
      ftp.quit()
      # A completed STOR proves the transfer only, not that the printer
      # accepted the image; the verification step below is authoritative.
      upload_result = UPLOAD_OK
    except all_errors as e:
      print('Firmware update aborted due to error while uploading')
      print(e)

  if upload_result == UPLOAD_INCOMPLETE:
    print(_incomplete_message(filename))
    return EXIT_UPLOAD

  if upload_result is not True:
    print('Firmware upload failed — the printer did not accept the image.')
    print(_retained_message(filename))
    return EXIT_UPLOAD

  print('done')
  print()
  print('Wait for printer to finish updating and reboot before continuing.')

  # TCP 9100 is fire-and-forget: confirm the printer came back on the
  # expected version instead of trusting "the socket did not raise".
  status, actual = _verify_flash(
      args.ip, getattr(args, 'community', 'public'), cat, artifact_version)

  if status == 'unverified':
    print('Uploaded, but the version could not be verified because the '
          'printer did not come back in time.')
    print('expected=%s actual=%s' % (artifact_version, actual))
    print(_retained_message(filename))
    return EXIT_UNVERIFIED

  if status == 'mismatch':
    print('Firmware verification FAILED: expected=%s actual=%s'
          % (artifact_version, actual))
    print(_retained_message(filename))
    return EXIT_UPLOAD

  print('Firmware verified: expected=%s actual=%s' % (artifact_version, actual))
  _remove_quietly(filename)
  return EXIT_OK


async def _snmp_walk_table(ip, community, oid):
    """Walk an SNMP OID on a Brother printer using pysnmp 7.x async API.
    
    Returns: list of rows, each row is list of (oid_str, value_str) tuples.
    Raises SystemExit on SNMP errors.
    """
    transport = await UdpTransportTarget.create(
        (ip, 161), timeout=30
    )
    table = []
    async for errorIndication, errorStatus, errorIndex, varBinds in walk_cmd(
        SnmpDispatcher(),
        CommunityData(community),
        transport,
        ObjectType(ObjectIdentity(oid)),
        lexicographicMode=False,
    ):
        if errorIndication:
            print(errorIndication, file=sys.stderr)
            sys.exit(1)
        if errorStatus:
            print('ERROR: %s at %s' % (
                errorStatus.prettyPrint(),
                errorIndex and varBinds[int(errorIndex) - 1] or '?'),
                file=sys.stderr)
            sys.exit(1)
        row = []
        for varBind in varBinds:
            oid_str = str(varBind[0])
            val_str = str(varBind[1]) if varBind[1] is not None else ''
            row.append((oid_str, val_str))
        table.append(row)
    return table


def main() -> int:
    global args, serial, model, spec, firmInfo

    try:
        args = parser.parse_args()

        # Provide information about requirements
        print('You may need to check the following in the printer\'s configuration:')
        print('  - SNMP service is enabled (for fetching model and versions)')
        if args.password:
            print('  - FTP service is enabled (for uploading firmware)')
            print('  - an administrator password is set (for connecting to FTP)')
        if not args.yes:
            prompt('Press Ctrl-C to exit or Enter to continue...')

        # Get SNMP data
        print('Getting SNMP data from printer at %s...' % args.ip)
        sys.stdout.flush()

        table = asyncio.run(_snmp_walk_table(
            args.ip, args.community, BROTHER_SNMP_OID,
        ))

        print('done')

        # Process SNMP data
        info = parse_snmp_table(table, verbose=args.verbose)
        serial = info['serial']
        model = info['model']
        spec = info['spec']
        firmInfo = info['firmwares']

        # Override model
        if args.model: model = args.model

        # Override category and version
        if args.category:
            firmInfo = [{'cat': args.category, 'version': args.fw_version}]

        # Print SNMP info
        print()
        print('    serial =', serial)
        print('     model =', model)
        print('      spec =', spec)
        print('   firmwares')

        for entry in firmInfo:
          print('    category = %(cat)s, version = %(version)s' % entry)

        print()

        codes = []
        num_firmwares = len(firmInfo)
        if num_firmwares > 1:
            print('WARNING: %d firmware updates pending. '
                  'Printer may reboot between updates.' % num_firmwares)
            print('A 30-second delay will be inserted between each update.')
            if not args.yes:
                prompt('Press Ctrl-C to abort or Enter to continue...')

        for i, entry in enumerate(firmInfo):
            print()
            code = update_firmware(entry['cat'], entry['version'])
            codes.append(code)
            if code == EXIT_OK and i < num_firmwares - 1:
                print('Waiting 30 seconds for printer to stabilize...')
                sys.stdout.flush()
                time.sleep(30)

        # Worst-wins: an upload failure must never be hidden behind another
        # category's success.
        failures = [c for c in codes if c not in (EXIT_OK, EXIT_CURRENT)]
        if failures:
            final = max(failures)
        elif EXIT_OK in codes:
            final = EXIT_OK
        else:
            final = EXIT_CURRENT

        print()
        if final == EXIT_OK:
            if args.test:
                print('Firmware image fetched and verified (test mode: '
                      'nothing was uploaded)')
            else:
                print('Firmware update completed')
        elif final == EXIT_CURRENT:
            print('No firmware update was needed')
        elif final == EXIT_UNVERIFIED:
            print('Firmware was uploaded, but the version could not be '
                  'verified (the printer did not come back in time). '
                  'Do not reflash blindly.')
        else:
            print('FAILURE: firmware update did not complete (exit code %d)'
                  % final)
        return final

    except Exception as e:
        print(e, file=sys.stderr)
        return EXIT_ERROR


if __name__ == '__main__':
    sys.exit(main())
