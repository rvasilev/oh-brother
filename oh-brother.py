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

from pysnmp.hlapi import (
    walkCmd, SnmpEngine, CommunityData, UdpTransportTarget,
    ContextData, ObjectType, ObjectIdentity,
)
import urllib.request, urllib.error, urllib.parse
import xml.etree.ElementTree as ET
import argparse
import sys
import socket
import os
import time
from ftplib import FTP
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
    import xml.etree.ElementTree as ET
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
    import xml.etree.ElementTree as ET
    
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
    if not any(parsed.netloc.endswith(d) for d in allowed_domains):
        return False, "unexpected domain: %s" % parsed.netloc
    path = parsed.path.lower()
    if not (path.endswith('.djf') or path.endswith('.upd')):
        return False, "unexpected file type: %s" % parsed.path
    return True, None


def _tcp_upload(filename, ip, sock):
    """Upload firmware file to printer via TCP port 9100 with retry.
    
    Uses sendfile with offset tracking for short-write resilience.
    Returns: True on success, False on failure.
    """
    fw_size = os.path.getsize(filename)
    with open(filename, 'rb') as fw:
        offset = 0
        while offset < fw_size:
            sent = sock.sendfile(fw, offset=offset)
            if sent == 0:
                print('Error: connection closed during firmware upload '
                      '(sent %d of %d bytes)' % (offset, fw_size))
                return False
            offset += sent
    return True


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


def update_firmware(cat, version):
  global args

  print('Updating %s version %s' % (cat, version))

  requestInfo = build_firmware_xml(model, spec, cat, version, beta=args.beta)

  if args.verbose: print('request: %s' % requestInfo)

  # Request firmware data
  url = 'https://firmverup.brother.co.jp/'
  url += 'kne_bh7_update_nt_ssl/ifax2.asmx/fileUpdate'
  hdrs = {'Content-Type': 'text/xml', 'User-Agent': 'BrHttpc/1.00'}

  print('Looking up printer firmware info at vendor server...')
  sys.stdout.flush()

  req = urllib.request.Request(url, requestInfo, hdrs)
  response = urllib.request.urlopen(req, timeout=30)
  response = response.read()

  print('done')

  if args.verbose: print('response: %s' % response)

  result = parse_brother_response(response)
  if result['version_check'] == '1':
    print('Firmware already up to date')
    # Try version fallback: newer Brother printers return no PATH when
    # already current. Sending an older version forces the API to return
    # the PATH for the current firmware (useful for backup/download).
    fallback_ver = _decrement_version(version)
    if fallback_ver:
      if args.verbose:
        print('Retrying with version %s to get firmware URL...' % fallback_ver)
      fallback_req = build_firmware_xml(model, spec, cat, fallback_ver, beta=args.beta)
      req2 = urllib.request.Request(url, fallback_req, hdrs)
      resp2 = urllib.request.urlopen(req2, timeout=30)
      resp2 = resp2.read()
      if args.verbose: print('fallback response: %s' % resp2)
      result2 = parse_brother_response(resp2)
      if result2['firmware_url']:
        firmwareURL = result2['firmware_url']
        print('Found firmware URL via version fallback')
      else:
        return False
    else:
      return False
  elif result['firmware_url'] is None:
    print('No firmware update info path found '
          '(newer Brother models require version fallback)')
    fallback_ver = _decrement_version(version)
    if fallback_ver:
      if args.verbose:
        print('Retrying with version %s to get firmware URL...' % fallback_ver)
      fallback_req = build_firmware_xml(model, spec, cat, fallback_ver, beta=args.beta)
      req2 = urllib.request.Request(url, fallback_req, hdrs)
      resp2 = urllib.request.urlopen(req2, timeout=30)
      resp2 = resp2.read()
      if args.verbose: print('fallback response: %s' % resp2)
      result2 = parse_brother_response(resp2)
      if result2['firmware_url']:
        firmwareURL = result2['firmware_url']
        print('Found firmware URL via version fallback')
      else:
        return False
    else:
      return False
  else:
    firmwareURL = result['firmware_url']

  # Validate firmware URL before downloading
  valid, err = _validate_firmware_url(firmwareURL)
  if not valid:
    print('Error: firmware URL validation failed: %s' % err)
    print('URL: %s' % firmwareURL)
    return False

  # Extract filename from URL (strip query parameters)
  filename = os.path.basename(urlparse(firmwareURL).path)
  if not filename:
    print('Error: could not extract filename from firmware URL')
    return False

  # Download firmware
  print('Downloading firmware file %s from vendor server...' % filename)
  sys.stdout.flush()

  req = urllib.request.Request(firmwareURL)
  response = urllib.request.urlopen(req, timeout=30)
  content_length = response.headers.get('Content-Length')

  with open(filename, 'wb') as f:
    while True:
      block = response.read(102400)
      if not block: break
      f.write(block)
      sys.stdout.write('.')
      sys.stdout.flush()

  print('done')

  # Verify firmware file integrity
  valid, err = _verify_firmware_integrity(filename, content_length=content_length)
  if not valid:
    print('Error: firmware integrity check failed: %s' % err)
    os.remove(filename)
    return False

  if args.test:
    os.remove(filename)
    return False

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

  success = False
  if args.password is None:
    ai = socket.getaddrinfo(args.ip, 9100, proto=socket.SOL_TCP)[0]
    try:
      with socket.socket(ai[0], ai[1], ai[2]) as sock:
        sock.settimeout(60)
        sock.connect(ai[4])
        success = _tcp_upload(filename, args.ip, sock)

    except OSError as e:
      print('Firmware update aborted due to error while uploading')
      print(e)
  else:
    try:
      ftp = FTP(args.ip, user = args.password) # Yes send password as user
      with open(filename, 'rb') as fw:
        ftp.storbinary('STOR ' + filename, fw)
      ftp.quit()
      success = True
    except Exception as e:
      print('Firmware update aborted due to error while uploading')
      print(e)

  os.remove(filename)

  if not success:
    return False

  print('done')
  print()
  print('Wait for printer to finish updating and reboot before continuing.')
  if not args.yes:
    prompt('Press Enter to continue...')

  return True


def main():
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

        table = []
        for errorIndication, errorStatus, errorIndex, varBinds in walkCmd(
            SnmpEngine(),
            CommunityData(args.community),
            UdpTransportTarget((args.ip, 161), timeout=30),
            ContextData(),
            ObjectType(ObjectIdentity('1.3.6.1.4.1.2435.2.4.3.99.3.1.6.1.2')),
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
                oid = str(varBind[0])
                val = str(varBind[1]) if varBind[1] is not None else ''
                row.append((oid, val))
            table.append(row)

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

        updated = False
        num_firmwares = len(firmInfo)
        if num_firmwares > 1:
            print('WARNING: %d firmware updates pending. '
                  'Printer may reboot between updates.' % num_firmwares)
            print('A 30-second delay will be inserted between each update.')
            if not args.yes:
                prompt('Press Ctrl-C to abort or Enter to continue...')

        for i, entry in enumerate(firmInfo):
            print()
            if update_firmware(entry['cat'], entry['version']):
                updated = True
                if i < num_firmwares - 1:
                    print('Waiting 30 seconds for printer to stabilize...')
                    sys.stdout.flush()
                    time.sleep(30)

        print()
        if updated:
            print('Firmware update completed')
        else:
            print('No firmware update was needed')

    except Exception as e:
        print(e, file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
