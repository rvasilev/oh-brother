import hashlib
import importlib.util
import inspect
import os
import sys
import urllib.error
import xml.etree.ElementTree as ET

import pytest

# Import oh_brother.py by path
module_path = os.path.join(os.path.dirname(__file__), '..', 'oh_brother.py')
spec = importlib.util.spec_from_file_location('oh_brother', module_path)
oh = importlib.util.module_from_spec(spec)
spec.loader.exec_module(oh)


# ---------------------------------------------------------------------------
# Test data
# ---------------------------------------------------------------------------

REAL_SNMP_TABLE = [
    [('1.3.6.1.4.1.2435.2.4.3.99.3.1.6.1.2.1', 'MODEL="HL-L2865DW"')],
    [('1.3.6.1.4.1.2435.2.4.3.99.3.1.6.1.2.2', 'SERIAL="U00000A0A000000"')],
    [('1.3.6.1.4.1.2435.2.4.3.99.3.1.6.1.2.3', 'SPEC="0906"')],
    [('1.3.6.1.4.1.2435.2.4.3.99.3.1.6.1.2.4', 'DEMOID="?"')],
    [('1.3.6.1.4.1.2435.2.4.3.99.3.1.6.1.2.5', 'FONT="?"')],
    [('1.3.6.1.4.1.2435.2.4.3.99.3.1.6.1.2.6', 'FIRMID="MAIN"')],
    [('1.3.6.1.4.1.2435.2.4.3.99.3.1.6.1.2.7', 'FIRMVER="1.24"')],
]

REAL_BROTHER_RESPONSE_UP_TO_DATE = (
    b'<?xml version="1.0" encoding="UTF-8" ?>'
    b'<RESPONSEINFO>'
    b'<FIRMUPDATEINFO>'
    b'<VERSIONCHECK>1</VERSIONCHECK>'
    b'<FIRMID>MAIN</FIRMID>'
    b'</FIRMUPDATEINFO>'
    b'</RESPONSEINFO>'
)


# ---------------------------------------------------------------------------
# parse_snmp_table
# ---------------------------------------------------------------------------


class TestParseSnmpTable:
    def test_real_printer_data(self):
        """Full extraction from real HL-L2865DW SNMP output."""
        result = oh.parse_snmp_table(REAL_SNMP_TABLE)
        assert result['serial'] == 'U00000A0A000000'
        assert result['model'] == 'HL-L2865DW'
        assert result['spec'] == '0906'
        assert len(result['firmwares']) == 1
        assert result['firmwares'][0] == {'cat': 'MAIN', 'version': '1.24'}

    def test_multiple_firmwares(self):
        """Printer with MAIN + SUB1 firmware."""
        table = [
            [('...6', 'FIRMID="MAIN"')],
            [('...7', 'FIRMVER="1.24"')],
            [('...6', 'FIRMID="SUB1"')],
            [('...7', 'FIRMVER="2.10"')],
        ]
        result = oh.parse_snmp_table(table)
        assert result['firmwares'] == [
            {'cat': 'MAIN', 'version': '1.24'},
            {'cat': 'SUB1', 'version': '2.10'},
        ]

    def test_firmver_before_firmid(self):
        """FIRMVER appearing before its FIRMID — should be skipped."""
        table = [
            [('...7', 'FIRMVER="1.24"')],  # No FIRMID yet — skip
            [('...6', 'FIRMID="MAIN"')],
            [('...7', 'FIRMVER="1.25"')],  # Now paired with MAIN
        ]
        result = oh.parse_snmp_table(table)
        assert len(result['firmwares']) == 1
        assert result['firmwares'][0] == {'cat': 'MAIN', 'version': '1.25'}

    def test_no_equals_sign_skipped(self):
        """Rows without '=' should be silently ignored."""
        table = [
            [('...x', '0x0c')],  # Raw hex byte, no '='
            [('...6', 'FIRMID="MAIN"')],
            [('...7', 'FIRMVER="1.24"')],
        ]
        result = oh.parse_snmp_table(table)
        assert result['model'] is None
        assert result['firmwares'][0] == {'cat': 'MAIN', 'version': '1.24'}

    def test_empty_table(self):
        """Empty table returns all None/empty."""
        result = oh.parse_snmp_table([])
        assert result['serial'] is None
        assert result['model'] is None
        assert result['spec'] is None
        assert result['firmwares'] == []

    def test_model_only(self):
        """Table with only MODEL — no serial, no firmware."""
        table = [[('...1', 'MODEL="HL-L2865DW"')]]
        result = oh.parse_snmp_table(table)
        assert result['model'] == 'HL-L2865DW'
        assert result['serial'] is None
        assert result['firmwares'] == []

    def test_verbose_output(self, capsys):
        """Verbose mode prints the table."""
        oh.parse_snmp_table(REAL_SNMP_TABLE, verbose=True)
        captured = capsys.readouterr()
        assert 'HL-L2865DW' in captured.out


# ---------------------------------------------------------------------------
# build_firmware_xml
# ---------------------------------------------------------------------------


class TestBuildFirmwareXml:
    def test_basic_xml_structure(self):
        xml_bytes = oh.build_firmware_xml('HL-L2865DW', '0906', 'MAIN', '1.24')
        root = ET.fromstring(xml_bytes)
        assert root.find('FIRMUPDATETOOLINFO/FIRMCATEGORY').text == 'MAIN'
        assert root.find('FIRMUPDATETOOLINFO/OS').text == 'WIN_NATIVE'
        assert root.find('FIRMUPDATETOOLINFO/INSPECTMODE').text == '0'
        assert root.find('FIRMUPDATEINFO/MODELINFO/NAME').text == 'HL-L2865DW'
        assert root.find('FIRMUPDATEINFO/MODELINFO/SPEC').text == '0906'
        firm = root.find('FIRMUPDATEINFO/MODELINFO/FIRMINFO/FIRM')
        assert firm.find('ID').text == 'MAIN'
        assert firm.find('VERSION').text == '1.24'

    def test_firm_category_mapped_to_main(self):
        """FIRM category should be mapped to MAIN in FIRMCATEGORY element."""
        xml_bytes = oh.build_firmware_xml('HL-L2865DW', '0906', 'FIRM', '1.00')
        root = ET.fromstring(xml_bytes)
        assert root.find('FIRMUPDATETOOLINFO/FIRMCATEGORY').text == 'MAIN'

    def test_ifax_id_mapped_to_main(self):
        """IFAX firm ID should be mapped to MAIN in ID element."""
        xml_bytes = oh.build_firmware_xml('HL-L2865DW', '0906', 'IFAX', '2.00')
        root = ET.fromstring(xml_bytes)
        firm = root.find('FIRMUPDATEINFO/MODELINFO/FIRMINFO/FIRM')
        assert firm.find('ID').text == 'MAIN'

    def test_beta_mode(self):
        xml_bytes = oh.build_firmware_xml('HL-L2865DW', '0906', 'MAIN', '1.24', beta=True)
        root = ET.fromstring(xml_bytes)
        assert root.find('FIRMUPDATETOOLINFO/INSPECTMODE').text == '1'

    def test_output_is_bytes(self):
        result = oh.build_firmware_xml('HL-L2865DW', '0906', 'MAIN', '1.24')
        assert isinstance(result, bytes)

    def test_driver_is_ews(self):
        xml_bytes = oh.build_firmware_xml('HL-L2865DW', '0906', 'MAIN', '1.24')
        root = ET.fromstring(xml_bytes)
        assert root.find('FIRMUPDATEINFO/MODELINFO/DRIVER').text == 'EWS'


# ---------------------------------------------------------------------------
# parse_brother_response
# ---------------------------------------------------------------------------


class TestParseBrotherResponse:
    def test_up_to_date(self):
        result = oh.parse_brother_response(REAL_BROTHER_RESPONSE_UP_TO_DATE)
        assert result['version_check'] == '1'
        assert result['firmware_url'] is None

    def test_update_available(self):
        xml = (
            b'<?xml version="1.0" encoding="UTF-8" ?>'
            b'<RESPONSEINFO>'
            b'<FIRMUPDATEINFO>'
            b'<VERSIONCHECK>0</VERSIONCHECK>'
            b'<PATH>http://update-akamai.brother.co.jp/CS/D00XXX_A.djf</PATH>'
            b'</FIRMUPDATEINFO>'
            b'</RESPONSEINFO>'
        )
        result = oh.parse_brother_response(xml)
        assert result['version_check'] == '0'
        assert result['firmware_url'] == ('http://update-akamai.brother.co.jp/CS/D00XXX_A.djf')

    def test_no_path_element(self):
        """Newer models (HL-L2865DW) may return no PATH — should return None."""
        xml = (
            b'<?xml version="1.0" encoding="UTF-8" ?>'
            b'<RESPONSEINFO>'
            b'<FIRMUPDATEINFO>'
            b'<VERSIONCHECK>0</VERSIONCHECK>'
            b'<FIRMID>MAIN</FIRMID>'
            b'</FIRMUPDATEINFO>'
            b'</RESPONSEINFO>'
        )
        result = oh.parse_brother_response(xml)
        assert result['version_check'] == '0'
        assert result['firmware_url'] is None

    def test_no_versioncheck(self):
        """Response missing VERSIONCHECK entirely."""
        xml = (
            b'<?xml version="1.0" encoding="UTF-8" ?>'
            b'<RESPONSEINFO>'
            b'<FIRMUPDATEINFO>'
            b'<PATH>http://example.com/firmware.djf</PATH>'
            b'</FIRMUPDATEINFO>'
            b'</RESPONSEINFO>'
        )
        result = oh.parse_brother_response(xml)
        assert result['version_check'] is None
        assert result['firmware_url'] is not None

    def test_empty_response(self):
        """Totally unexpected XML — shouldn't crash."""
        xml = b'<?xml version="1.0" encoding="UTF-8" ?><OTHER/>'
        result = oh.parse_brother_response(xml)
        assert result['version_check'] is None
        assert result['firmware_url'] is None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


class TestCLI:
    """CLI argument parsing — parameterized to avoid testing stdlib argparse."""

    @pytest.mark.parametrize(
        'args_str,attr,expected',
        [
            # Boolean flags (default False)
            ('1.2.3.4', 'yes', False),
            ('1.2.3.4', 'test', False),
            ('1.2.3.4', 'verbose', False),
            ('1.2.3.4', 'beta', False),
            # Boolean flags (set)
            ('--yes 1.2.3.4', 'yes', True),
            ('--test 1.2.3.4', 'test', True),
            ('--verbose 1.2.3.4', 'verbose', True),
            ('--beta 1.2.3.4', 'beta', True),
        ],
    )
    def test_boolean_flags(self, args_str, attr, expected):
        args = oh.parser.parse_args(args_str.split())
        assert getattr(args, attr) == expected

    @pytest.mark.parametrize(
        'args_str,attr,expected',
        [
            # String args (defaults)
            ('1.2.3.4', 'community', 'public'),
            ('1.2.3.4', 'fw_version', 'B0000000000'),
            ('1.2.3.4', 'model', None),
            ('1.2.3.4', 'category', None),
            ('1.2.3.4', 'password', None),
            # String args (set)
            ('--community private 1.2.3.4', 'community', 'private'),
            ('--model HL-1110 1.2.3.4', 'model', 'HL-1110'),
            ('--password admin123 1.2.3.4', 'password', 'admin123'),
        ],
    )
    def test_string_args(self, args_str, attr, expected):
        args = oh.parser.parse_args(args_str.split())
        assert getattr(args, attr) == expected

    def test_category_with_version(self):
        args = oh.parser.parse_args(['--category', 'SUB1', '--fw-version', '2.00', '1.2.3.4'])
        assert args.category == 'SUB1'
        assert args.fw_version == '2.00'

    def test_ip_required(self):
        with pytest.raises(SystemExit):
            oh.parser.parse_args([])

    def test_parser_accessible(self):
        """Module-level parser is importable."""
        assert hasattr(oh, 'parser')

    def test_version_falls_back_when_metadata_is_malformed(self, monkeypatch):
        """A broken distribution record must not make the module un-importable."""

        def broken(_name):
            raise ImportError('malformed metadata')

        monkeypatch.setattr(oh, '_dist_version', broken)
        assert oh._version() == '0.0.0+source'


# ---------------------------------------------------------------------------
# Global state reset fixture
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_global_state(monkeypatch):
    """Reset oh module globals between tests to prevent cross-test leakage."""
    for attr in ('args', 'model', 'spec', 'serial', 'firmInfo'):
        monkeypatch.setattr(oh, attr, None, raising=False)


@pytest.fixture(autouse=True)
def no_real_preflight(monkeypatch):
    """Never let a test dial the printer's upload port.

    The R16 preflight is a real TCP connect; left unpatched, every flash-path
    test would reach 1.2.3.4:9100. The preflight tests override this seam.
    ``raising=False`` keeps the fixture inert against a pre-packet source that
    has no such helper, so a RED run fails on behaviour, not on the fixture.
    """
    monkeypatch.setattr(oh, '_printer_port_open', lambda *a, **k: True, raising=False)


# ---------------------------------------------------------------------------
# main()
# ---------------------------------------------------------------------------


class TestMainSmoke:
    """Smoke tests for main() — verifies orchestration without real I/O."""

    def test_main_parses_snmp_and_calls_update(self, monkeypatch):
        """main() extracts SNMP data then calls update_firmware for each category."""

        async def fake_walk_cmd(*args, **kwargs):
            table = []
            for snmp_row in REAL_SNMP_TABLE:
                varBinds = [(oid, val) for oid, val in snmp_row]
                table.append([(str(vb[0]), str(vb[1])) for vb in varBinds])
            return table

        monkeypatch.setattr('builtins.input', lambda _=None: None)

        called_with = []

        def fake_update(cat, ver):
            called_with.append((cat, ver))
            return oh.EXIT_OK

        monkeypatch.setattr(oh, 'update_firmware', fake_update)

        monkeypatch.setattr(oh, '_snmp_walk_table', fake_walk_cmd)
        monkeypatch.setattr('sys.argv', ['oh-brother.py', '1.2.3.4'])
        assert oh.main() == oh.EXIT_OK

        assert called_with == [('MAIN', '1.24')]

    def test_main_model_override(self, monkeypatch):
        """--model flag overrides SNMP-discovered model."""

        async def fake_walk_cmd(*args, **kwargs):
            table = []
            for snmp_row in REAL_SNMP_TABLE:
                varBinds = [(oid, val) for oid, val in snmp_row]
                table.append([(str(vb[0]), str(vb[1])) for vb in varBinds])
            return table

        monkeypatch.setattr('builtins.input', lambda _=None: None)
        called_with = []
        monkeypatch.setattr(
            oh,
            'update_firmware',
            lambda c, v: called_with.append((c, v)) or oh.EXIT_OK,
        )

        monkeypatch.setattr(oh, '_snmp_walk_table', fake_walk_cmd)
        monkeypatch.setattr(
            'sys.argv',
            ['oh-brother.py', '--model', 'HL-9999', '1.2.3.4'],
        )
        oh.main()

        assert called_with == [('MAIN', '1.24')]

    def test_main_category_override(self, monkeypatch):
        """--category + --version replace all firmware entries."""

        async def fake_walk_cmd(*args, **kwargs):
            table = []
            for snmp_row in REAL_SNMP_TABLE:
                varBinds = [(oid, val) for oid, val in snmp_row]
                table.append([(str(vb[0]), str(vb[1])) for vb in varBinds])
            return table

        monkeypatch.setattr('builtins.input', lambda _=None: None)
        called_with = []
        monkeypatch.setattr(
            oh,
            'update_firmware',
            lambda c, v: called_with.append((c, v)) or oh.EXIT_OK,
        )

        monkeypatch.setattr(oh, '_snmp_walk_table', fake_walk_cmd)
        monkeypatch.setattr(
            'sys.argv',
            ['oh-brother.py', '--category', 'SUB1', '--fw-version', '3.00', '1.2.3.4'],
        )
        oh.main()

        assert called_with == [('SUB1', '3.00')]

    def test_main_snmp_error_raises(self, monkeypatch):
        """SNMP error raises Exception."""

        async def fake_walk_cmd(*args, **kwargs):
            print('SNMP timeout', file=sys.stderr)
            sys.exit(1)

        monkeypatch.setattr('builtins.input', lambda _=None: None)

        monkeypatch.setattr(oh, '_snmp_walk_table', fake_walk_cmd)
        monkeypatch.setattr('sys.argv', ['oh-brother.py', '1.2.3.4'])
        with pytest.raises(SystemExit):
            oh.main()

    def test_main_snmp_status_raises(self, monkeypatch):
        """SNMP non-zero status raises Exception."""
        from unittest.mock import MagicMock

        mock_status = MagicMock()

        async def fake_walk_cmd(*args, **kwargs):
            print('ERROR: %s at %s' % (mock_status.prettyPrint(), '?.1.2.3'), file=sys.stderr)
            sys.exit(1)

        monkeypatch.setattr('builtins.input', lambda _=None: None)

        monkeypatch.setattr(oh, '_snmp_walk_table', fake_walk_cmd)
        monkeypatch.setattr('sys.argv', ['oh-brother.py', '1.2.3.4'])
        with pytest.raises(SystemExit):
            oh.main()

    def test_main_multiple_firmwares_waits_for_readiness(self, monkeypatch):
        """R11: between categories the printer is polled, never blind-slept.

        A fixed sleep is wrong in both directions — too short for a printer
        that is still rebooting, wasted time when it is already back — so pin
        both the poll and the absence of the sleep.
        """

        # SNMP data with MAIN + SUB1 firmware
        multi_fw_table = [
            [('...1', 'MODEL="HL-L2865DW"')],
            [('...2', 'SPEC="0906"')],
            [('...6', 'FIRMID="MAIN"')],
            [('...7', 'FIRMVER="1.24"')],
            [('...6', 'FIRMID="SUB1"')],
            [('...7', 'FIRMVER="2.10"')],
        ]

        async def fake_walk_cmd(*args, **kwargs):
            table = []
            for snmp_row in multi_fw_table:
                varBinds = [(oid, val) for oid, val in snmp_row]
                table.append([(str(vb[0]), str(vb[1])) for vb in varBinds])
            return table

        monkeypatch.setattr('builtins.input', lambda _=None: None)
        monkeypatch.setattr(oh, '_snmp_walk_table', fake_walk_cmd)
        monkeypatch.setattr('sys.argv', ['oh-brother.py', '1.2.3.4'])

        called_with = []

        def fake_update(cat, ver):
            called_with.append((cat, ver))
            return oh.EXIT_OK

        monkeypatch.setattr(oh, 'update_firmware', fake_update)

        readiness_waits = []

        def fake_wait(ip, community, timeout=None, poll=None):
            readiness_waits.append((ip, community))
            return 1.0

        monkeypatch.setattr(oh, '_wait_for_printer_ready', fake_wait)

        sleep_calls = []
        monkeypatch.setattr(oh.time, 'sleep', lambda s: sleep_calls.append(s))

        oh.main()

        assert called_with == [('MAIN', '1.24'), ('SUB1', '2.10')]
        # Readiness was polled between the two updates...
        assert readiness_waits == [('1.2.3.4', 'public')]
        # ...and the blind fixed sleep is gone.
        assert sleep_calls == []

    def test_main_stops_when_printer_never_returns(self, monkeypatch, capsys):
        """R11: a printer that stays down ends the run instead of being
        flashed blind."""
        multi_fw_table = [
            [('...1', 'MODEL="HL-L2865DW"')],
            [('...2', 'SPEC="0906"')],
            [('...6', 'FIRMID="MAIN"')],
            [('...7', 'FIRMVER="1.24"')],
            [('...6', 'FIRMID="SUB1"')],
            [('...7', 'FIRMVER="2.10"')],
        ]

        async def fake_walk_cmd(*args, **kwargs):
            table = []
            for snmp_row in multi_fw_table:
                varBinds = [(oid, val) for oid, val in snmp_row]
                table.append([(str(vb[0]), str(vb[1])) for vb in varBinds])
            return table

        monkeypatch.setattr('builtins.input', lambda _=None: None)
        monkeypatch.setattr(oh, '_snmp_walk_table', fake_walk_cmd)
        monkeypatch.setattr('sys.argv', ['oh-brother.py', '1.2.3.4'])

        called_with = []

        def fake_update(cat, ver):
            called_with.append((cat, ver))
            return oh.EXIT_OK

        monkeypatch.setattr(oh, 'update_firmware', fake_update)
        monkeypatch.setattr(
            oh, '_wait_for_printer_ready', lambda ip, community, timeout=None, poll=None: None
        )

        code = oh.main()

        assert code == oh.EXIT_PRINTER
        assert called_with == [('MAIN', '1.24')]  # SUB1 is never attempted
        assert 'did not answer' in capsys.readouterr().out


# ---------------------------------------------------------------------------
# update_firmware()
# ---------------------------------------------------------------------------


class TestUpdateFirmware:
    """Tests for update_firmware() with mocked external I/O."""

    def test_version_up_to_date(self, monkeypatch):
        """VERSIONCHECK=1 without --reflash → EXIT_CURRENT, no fallback."""
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        oh.args = SimpleNamespace(
            beta=False,
            verbose=False,
            test=False,
            yes=False,
            ip='1.2.3.4',
            password=None,
            reflash=False,
        )
        oh.model = 'HL-L2865DW'
        oh.spec = '0906'

        mock_response = MagicMock()
        mock_response.read.return_value = REAL_BROTHER_RESPONSE_UP_TO_DATE

        monkeypatch.setattr(oh.urllib.request, 'urlopen', lambda req, timeout=None: mock_response)

        result = oh.update_firmware('MAIN', '1.24')
        assert result == oh.EXIT_CURRENT
        assert result != oh.EXIT_OK

    def test_no_path_returns_none(self, monkeypatch):
        """No PATH element and fallback fails → EXIT_VENDOR."""
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        oh.args = SimpleNamespace(
            beta=False,
            verbose=False,
            test=False,
            yes=False,
            ip='1.2.3.4',
            password=None,
            reflash=False,
        )
        oh.model = 'HL-L2865DW'
        oh.spec = '0906'

        xml_no_path = (
            b'<?xml version="1.0" encoding="UTF-8" ?>'
            b'<RESPONSEINFO>'
            b'<FIRMUPDATEINFO>'
            b'<VERSIONCHECK>0</VERSIONCHECK>'
            b'<FIRMID>MAIN</FIRMID>'
            b'</FIRMUPDATEINFO>'
            b'</RESPONSEINFO>'
        )

        mock_response = MagicMock()
        mock_response.read.return_value = xml_no_path

        monkeypatch.setattr(oh.urllib.request, 'urlopen', lambda req, timeout=None: mock_response)

        result = oh.update_firmware('MAIN', '1.24')
        assert result == oh.EXIT_VENDOR

    def test_test_flag_stops_before_upload(self, monkeypatch, tmp_path):
        """--test downloads firmware but does not upload; image is retained."""
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        oh.args = SimpleNamespace(
            beta=False,
            verbose=False,
            test=True,
            yes=False,
            ip='1.2.3.4',
            password=None,
            reflash=False,
        )
        oh.model = 'HL-L2865DW'
        oh.spec = '0906'

        xml_update = (
            b'<?xml version="1.0" encoding="UTF-8" ?>'
            b'<RESPONSEINFO>'
            b'<FIRMUPDATEINFO>'
            b'<VERSIONCHECK>0</VERSIONCHECK>'
            b'<PATH>http://update-akamai.brother.co.jp/CS/D00XXX_A.djf</PATH>'
            b'</FIRMUPDATEINFO>'
            b'</RESPONSEINFO>'
        )

        body = b'F' * 200000

        def fake_urlopen(req, timeout=None):
            m = MagicMock()
            m.headers = {'Content-Length': str(len(body))}
            m.read.side_effect = [body, b'']
            return m

        def fail_socket(*args, **kwargs):
            raise AssertionError('no socket may be created in --test mode')

        monkeypatch.setattr(
            oh, '_http_post', lambda url, data, hdrs, timeout=30: (xml_update, None)
        )
        monkeypatch.setattr(oh.urllib.request, 'urlopen', fake_urlopen)
        monkeypatch.setattr(oh.socket, 'getaddrinfo', fail_socket)
        monkeypatch.setattr(oh.socket, 'socket', fail_socket)
        monkeypatch.chdir(tmp_path)

        result = oh.update_firmware('MAIN', '1.24')

        assert result == oh.EXIT_OK
        retained = list((tmp_path / oh.BACKUP_DIRNAME).rglob('*.djf'))
        assert len(retained) == 1
        assert retained[0].stat().st_size == len(body)
        assert not list(tmp_path.rglob('*.part'))

    def test_yes_skips_prompts(self, monkeypatch):
        """--yes flag skips all input() prompts."""
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        oh.args = SimpleNamespace(
            beta=False,
            verbose=False,
            test=False,
            yes=True,
            ip='1.2.3.4',
            password=None,
            reflash=False,
        )
        oh.model = 'HL-L2865DW'
        oh.spec = '0906'

        mock_response = MagicMock()
        mock_response.read.return_value = REAL_BROTHER_RESPONSE_UP_TO_DATE

        input_calls = []
        monkeypatch.setattr('builtins.input', lambda _=None: input_calls.append(1) or '')
        monkeypatch.setattr(oh.urllib.request, 'urlopen', lambda req, timeout=None: mock_response)

        oh.update_firmware('MAIN', '1.24')
        assert len(input_calls) == 0

    def test_vcheck1_fallback_succeeds(self, monkeypatch, tmp_path):
        """VCHECK=1 WITH --reflash → retries with decremented version, gets PATH."""
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        oh.args = SimpleNamespace(
            beta=False,
            verbose=False,
            test=True,
            yes=True,
            ip='1.2.3.4',
            password=None,
            reflash=True,
        )
        oh.model = 'HL-L2865DW'
        oh.spec = '0906'

        xml_update = (
            b'<?xml version="1.0" encoding="UTF-8" ?>'
            b'<RESPONSEINFO>'
            b'<FIRMUPDATEINFO>'
            b'<VERSIONCHECK>0</VERSIONCHECK>'
            b'<PATH>http://update-akamai.brother.co.jp/CS/D02FZM_124Q_crypt.djf</PATH>'
            b'</FIRMUPDATEINFO>'
            b'</RESPONSEINFO>'
        )

        call_count = [0]

        def mock_urlopen(req, timeout=None):
            call_count[0] += 1
            m = MagicMock()
            if call_count[0] == 1:
                # First call: up to date (VCHECK=1, no PATH)
                m.read.return_value = REAL_BROTHER_RESPONSE_UP_TO_DATE
            elif call_count[0] == 2:
                # Second call: fallback with decremented version → PATH
                m.read.return_value = xml_update
            else:
                # Third call: firmware download — 200KB then done
                m.read.side_effect = [b'\x00' * 204800, b'']
                m.headers.get.return_value = None
            return m

        monkeypatch.setattr(oh.urllib.request, 'urlopen', mock_urlopen)
        monkeypatch.setattr('builtins.input', lambda _=None: None)
        monkeypatch.chdir(tmp_path)

        result = oh.update_firmware('MAIN', '1.24')
        assert result == oh.EXIT_OK  # --test mode: downloaded + verified
        assert call_count[0] >= 3  # original + fallback + download

    def test_vcheck1_fallback_fails(self, monkeypatch):
        """VCHECK=1 with --reflash, fallback also VCHECK=1 → EXIT_VENDOR."""
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        oh.args = SimpleNamespace(
            beta=False,
            verbose=False,
            test=False,
            yes=True,
            ip='1.2.3.4',
            password=None,
            reflash=True,
        )
        oh.model = 'HL-L2865DW'
        oh.spec = '0906'

        call_count = [0]

        def mock_urlopen(req, timeout=None):
            call_count[0] += 1
            m = MagicMock()
            m.read.return_value = REAL_BROTHER_RESPONSE_UP_TO_DATE
            return m

        monkeypatch.setattr(oh.urllib.request, 'urlopen', mock_urlopen)
        monkeypatch.setattr('builtins.input', lambda _=None: None)

        result = oh.update_firmware('MAIN', '1.24')
        assert result == oh.EXIT_VENDOR
        assert call_count[0] == 2  # Original + fallback, both VCHECK=1


# ---------------------------------------------------------------------------
# _decrement_version
# ---------------------------------------------------------------------------


class TestDecrementVersion:
    def test_normal_version(self):
        assert oh._decrement_version('1.24') == '1.23'

    def test_zero_padding_is_preserved(self):
        """R14: '2.10' must decrement to '2.09', not '2.9'.

        The API matches the string it is sent, so dropping the zero asks about
        a different version rather than a shorter way of writing the same one.
        """
        assert oh._decrement_version('2.10.5') == '2.09.5'
        assert oh._decrement_version('1.05') == '1.04'
        assert oh._decrement_version('1.24') == '1.23'

    def test_zero_minor_borrows_from_the_major(self):
        """R14: a printer on a .00 version must still fetch its own firmware."""
        assert oh._decrement_version('3.00') == '2.99'
        assert oh._decrement_version('1.00') == '0.99'
        # A single-digit minor field keeps its own width.
        assert oh._decrement_version('3.0') == '2.9'

    def test_lowest_version_has_no_fallback(self):
        assert oh._decrement_version('0.00') is None

    def test_single_part_has_no_minor(self):
        assert oh._decrement_version('1') is None

    def test_non_numeric(self):
        assert oh._decrement_version('abc') is None

    def test_empty(self):
        assert oh._decrement_version('') is None


# ---------------------------------------------------------------------------
# _validate_firmware_url
# ---------------------------------------------------------------------------


class TestValidateFirmwareUrl:
    def test_valid_brother_cdn_http(self):
        """Standard Brother CDN URL over HTTP — should pass."""
        valid, err = oh._validate_firmware_url(
            'http://update-akamai.brother.co.jp/CS/D02FZM_124Q_crypt.djf'
        )
        assert valid is True
        assert err is None

    def test_valid_brother_cdn_https(self):
        """HTTPS variant — should pass."""
        valid, err = oh._validate_firmware_url(
            'https://update-akamai.brother.co.jp/CS/D00XXX_A.djf'
        )
        assert valid is True
        assert err is None

    def test_valid_upd_extension(self):
        """.upd files are valid Brother firmware."""
        valid, _err = oh._validate_firmware_url('http://download.brother.com/pub/HL1110_SUB1.upd')
        assert valid is True

    def test_wrong_domain(self):
        """Non-Brother domain — should fail."""
        valid, err = oh._validate_firmware_url('http://evil.com/firmware.djf')
        assert valid is False
        assert 'domain' in err.lower()

    def test_file_scheme(self):
        """file:// URLs are not allowed."""
        valid, err = oh._validate_firmware_url('file:///tmp/firmware.djf')
        assert valid is False
        assert 'scheme' in err.lower()

    def test_wrong_extension(self):
        """Non-firmware extensions — should fail."""
        valid, err = oh._validate_firmware_url('http://update-akamai.brother.co.jp/CS/readme.txt')
        assert valid is False
        assert 'file type' in err.lower() or 'extension' in err.lower()

    def test_empty_url(self):
        """Empty URL — should fail."""
        valid, _err = oh._validate_firmware_url('')
        assert valid is False

    def test_url_with_query_params(self):
        """URL with query params — should still validate (extension before ?)."""
        valid, _err = oh._validate_firmware_url(
            'http://update-akamai.brother.co.jp/CS/D00XXX_A.djf?token=abc'
        )
        assert valid is True


# ---------------------------------------------------------------------------
# TCP upload (sendfile retry)
# ---------------------------------------------------------------------------


class TestTcpUpload:
    """Tests for the TCP upload retry loop."""

    def test_sendfile_full_success(self, monkeypatch, tmp_path):
        """sendfile returns full file size in one call — success."""
        from unittest.mock import MagicMock

        fw_path = tmp_path / 'test.djf'
        fw_path.write_bytes(b'\x00' * 4096)

        mock_sock = MagicMock()
        mock_sock.sendfile.return_value = 4096

        result = oh._tcp_upload(str(fw_path), mock_sock)
        assert result is True
        assert mock_sock.sendfile.call_count == 1

    def test_sendfile_short_write_retry(self, monkeypatch, tmp_path):
        """sendfile returns partial bytes — retries until complete."""
        from unittest.mock import MagicMock

        fw_path = tmp_path / 'test.djf'
        fw_path.write_bytes(b'\x00' * 8192)

        mock_sock = MagicMock()
        mock_sock.sendfile.side_effect = [4096, 4096, 0]

        result = oh._tcp_upload(str(fw_path), mock_sock)
        assert result is True
        assert mock_sock.sendfile.call_count >= 2

    def test_sendfile_zero_return_fails(self, monkeypatch, tmp_path):
        """sendfile returns 0 — connection closed, should fail."""
        from unittest.mock import MagicMock

        fw_path = tmp_path / 'test.djf'
        fw_path.write_bytes(b'\x00' * 4096)

        mock_sock = MagicMock()
        mock_sock.sendfile.return_value = 0

        result = oh._tcp_upload(str(fw_path), mock_sock)
        assert result is False

    def test_signature_drops_the_dead_ip_parameter(self):
        """The upload decides success/failure; it must not advertise an address."""
        assert 'ip' not in inspect.signature(oh._tcp_upload).parameters


# ---------------------------------------------------------------------------
# _verify_firmware_integrity
# ---------------------------------------------------------------------------


class TestVerifyFirmwareIntegrity:
    """Tests for firmware file integrity checks."""

    def test_content_length_match(self, tmp_path):
        """Content-Length matches file size — passes."""
        fw_path = tmp_path / 'test.djf'
        fw_path.write_bytes(b'\x00' * 204800)  # 200KB

        valid, err = oh._verify_firmware_integrity(str(fw_path), content_length=204800)
        assert valid is True
        assert err is None

    def test_content_length_mismatch(self, tmp_path):
        """Content-Length doesn't match — fails."""
        fw_path = tmp_path / 'test.djf'
        fw_path.write_bytes(b'\x00' * 200000)

        valid, err = oh._verify_firmware_integrity(str(fw_path), content_length=300000)
        assert valid is False
        assert 'size mismatch' in err.lower()

    def test_file_too_small(self, tmp_path):
        """File smaller than 100KB minimum — fails."""
        fw_path = tmp_path / 'test.djf'
        fw_path.write_bytes(b'\x00' * 50000)  # 50KB

        valid, err = oh._verify_firmware_integrity(str(fw_path))
        assert valid is False
        assert 'too small' in err.lower()

    def test_minimum_size_pass(self, tmp_path):
        """File at exactly 100KB — passes with no Content-Length."""
        fw_path = tmp_path / 'test.djf'
        fw_path.write_bytes(b'\x00' * 102400)  # 100KB

        valid, _err = oh._verify_firmware_integrity(str(fw_path))
        assert valid is True

    def test_size_gate_only(self, tmp_path):
        """No Content-Length provided — uses size gate only."""
        fw_path = tmp_path / 'test.djf'
        fw_path.write_bytes(b'\x00' * 500000)  # 500KB

        valid, _err = oh._verify_firmware_integrity(str(fw_path))
        assert valid is True


# ---------------------------------------------------------------------------
# _http_post — HTTP error handling (P1: SSL #42, P2: HTTP errors #51)
# ---------------------------------------------------------------------------


class TestHttpPost:
    """Tests for _http_post error handling."""

    def test_success(self, monkeypatch):
        """Successful POST returns response bytes."""
        from unittest.mock import MagicMock

        mock_resp = MagicMock()
        mock_resp.read.return_value = b'<xml>ok</xml>'

        def fake_urlopen(req, timeout=None, context=None):
            return mock_resp

        monkeypatch.setattr(oh.urllib.request, 'urlopen', fake_urlopen)

        data, err = oh._http_post('https://test.local', b'<req/>', {'Content-Type': 'text/xml'})
        assert data == b'<xml>ok</xml>'
        assert err is None

    def test_http_503(self, monkeypatch):
        """HTTP 503 returns None + error message."""
        import urllib.error

        def fake_urlopen(req, timeout=None, context=None):
            raise urllib.error.HTTPError('https://test.local', 503, 'Service Unavailable', {}, None)

        monkeypatch.setattr(oh.urllib.request, 'urlopen', fake_urlopen)

        data, err = oh._http_post('https://test.local', b'<req/>', {'Content-Type': 'text/xml'})
        assert data is None
        assert '503' in err
        assert 'brother server' in err.lower()

    def test_ssl_cert_error(self, monkeypatch):
        """SSL certificate error returns None + cert guidance."""
        import ssl
        import urllib.error

        def fake_urlopen(req, timeout=None, context=None):
            raise urllib.error.URLError(
                ssl.SSLCertVerificationError(
                    'certificate verify failed: unable to get local issuer certificate'
                )
            )

        monkeypatch.setattr(oh.urllib.request, 'urlopen', fake_urlopen)

        data, err = oh._http_post('https://test.local', b'<req/>', {'Content-Type': 'text/xml'})
        assert data is None
        assert 'ssl' in err.lower() or 'certificate' in err.lower()

    def test_timeout(self, monkeypatch):
        """Socket timeout returns None + network guidance."""
        import urllib.error

        def fake_urlopen(req, timeout=None, context=None):
            raise urllib.error.URLError(TimeoutError('timed out'))

        monkeypatch.setattr(oh.urllib.request, 'urlopen', fake_urlopen)

        data, err = oh._http_post('https://test.local', b'<req/>', {'Content-Type': 'text/xml'})
        assert data is None
        assert 'timeout' in err.lower() or 'network' in err.lower()

    def test_dns_failure(self, monkeypatch):
        """DNS failure returns None + network guidance."""
        import socket
        import urllib.error

        def fake_urlopen(req, timeout=None, context=None):
            raise urllib.error.URLError(socket.gaierror('Name or service not known'))

        monkeypatch.setattr(oh.urllib.request, 'urlopen', fake_urlopen)

        data, err = oh._http_post('https://test.local', b'<req/>', {'Content-Type': 'text/xml'})
        assert data is None
        assert 'network' in err.lower() or 'dns' in err.lower() or 'connect' in err.lower()


# ---------------------------------------------------------------------------
# Safety gates: exit codes, already-current gate, downgrade block, TTY gate
# ---------------------------------------------------------------------------

XML_UPDATE_124 = (
    b'<?xml version="1.0" encoding="UTF-8" ?>'
    b'<RESPONSEINFO>'
    b'<FIRMUPDATEINFO>'
    b'<VERSIONCHECK>0</VERSIONCHECK>'
    b'<PATH>http://update-akamai.brother.co.jp/CS/D02FZM_124Q_crypt.djf</PATH>'
    b'</FIRMUPDATEINFO>'
    b'</RESPONSEINFO>'
)

XML_UPDATE_120 = (
    b'<?xml version="1.0" encoding="UTF-8" ?>'
    b'<RESPONSEINFO>'
    b'<FIRMUPDATEINFO>'
    b'<VERSIONCHECK>0</VERSIONCHECK>'
    b'<PATH>http://update-akamai.brother.co.jp/CS/D02FZM_120Q_crypt.djf</PATH>'
    b'</FIRMUPDATEINFO>'
    b'</RESPONSEINFO>'
)

XML_UPDATE_126 = (
    b'<?xml version="1.0" encoding="UTF-8" ?>'
    b'<RESPONSEINFO>'
    b'<FIRMUPDATEINFO>'
    b'<VERSIONCHECK>0</VERSIONCHECK>'
    b'<PATH>http://update-akamai.brother.co.jp/CS/D02FZM_126R_crypt.djf</PATH>'
    b'</FIRMUPDATEINFO>'
    b'</RESPONSEINFO>'
)


def _args(**overrides):
    from types import SimpleNamespace

    base = {
        'beta': False,
        'verbose': False,
        'test': False,
        'yes': True,
        'ip': '1.2.3.4',
        'password': None,
        'reflash': False,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _download_response(size=204800):
    from unittest.mock import MagicMock

    resp = MagicMock()
    resp.read.side_effect = [b'\x00' * size, b'']
    resp.headers.get.return_value = None
    return resp


class TestSafetyGates:
    """End-to-end guards on the flash path."""

    def test_vcheck1_does_not_upload_without_reflash(self, monkeypatch, capsys):
        """VERSIONCHECK=1 without --reflash: no fallback, no download, no upload."""
        oh.args = _args(reflash=False)
        oh.model = 'HL-L2865DW'
        oh.spec = '0906'

        monkeypatch.setattr(
            oh, '_http_post', lambda *a, **k: (REAL_BROTHER_RESPONSE_UP_TO_DATE, None)
        )

        fallback_calls = []
        monkeypatch.setattr(
            oh, '_try_version_fallback', lambda *a, **k: fallback_calls.append(1) or None
        )

        def no_download(*a, **k):
            raise AssertionError('firmware must not be downloaded')

        def no_socket(*a, **k):
            raise AssertionError('no socket may be opened')

        monkeypatch.setattr(oh.urllib.request, 'urlopen', no_download)
        monkeypatch.setattr(oh.socket, 'socket', no_socket)
        monkeypatch.setattr(oh.socket, 'getaddrinfo', no_socket)

        assert oh.update_firmware('MAIN', '1.24') == oh.EXIT_CURRENT
        assert fallback_calls == []
        out = capsys.readouterr().out
        assert 'already up to date' in out.lower()

    def test_reflash_flag_enables_current_version_upload(self, monkeypatch, tmp_path):
        """With --reflash the fallback path is allowed to run."""
        oh.args = _args(reflash=True, test=True)
        oh.model = 'HL-L2865DW'
        oh.spec = '0906'

        api_calls = []

        def fake_post(url, data, hdrs, *a, **k):
            api_calls.append(1)
            if len(api_calls) == 1:
                return REAL_BROTHER_RESPONSE_UP_TO_DATE, None
            return XML_UPDATE_124, None

        monkeypatch.setattr(oh, '_http_post', fake_post)
        monkeypatch.setattr(oh.urllib.request, 'urlopen', lambda *a, **k: _download_response())
        monkeypatch.chdir(tmp_path)

        assert oh.update_firmware('MAIN', '1.24') == oh.EXIT_OK
        assert len(api_calls) == 2  # original + fallback, proving fallback ran

    def test_downgrade_blocked_when_artifact_older_than_installed(
        self, monkeypatch, tmp_path, capsys
    ):
        """Artifact 1.20 vs installed 1.24 → refused, nothing downloaded."""
        oh.args = _args()
        oh.model = 'HL-L2865DW'
        oh.spec = '0906'

        monkeypatch.setattr(oh, '_http_post', lambda *a, **k: (XML_UPDATE_120, None))

        def no_download(*a, **k):
            raise AssertionError('downgrade artifact must not be downloaded')

        monkeypatch.setattr(oh.urllib.request, 'urlopen', no_download)
        monkeypatch.chdir(tmp_path)

        assert oh.update_firmware('MAIN', '1.24') == oh.EXIT_REFUSED
        out = capsys.readouterr().out
        assert '1.20' in out and '1.24' in out

    def test_upload_failure_exits_nonzero(self, monkeypatch, tmp_path):
        """A failed upload yields EXIT_UPLOAD (non-zero)."""
        from unittest.mock import MagicMock

        oh.args = _args()
        oh.model = 'HL-L2865DW'
        oh.spec = '0906'

        monkeypatch.setattr(oh, '_http_post', lambda *a, **k: (XML_UPDATE_124, None))
        monkeypatch.setattr(oh.urllib.request, 'urlopen', lambda *a, **k: _download_response())
        monkeypatch.setattr(oh, '_tcp_upload', lambda f, sock: False)
        monkeypatch.setattr(
            oh.socket, 'getaddrinfo', lambda *a, **k: [(2, 1, 6, '', ('1.2.3.4', 9100))]
        )
        monkeypatch.setattr(oh.socket, 'socket', lambda *a: MagicMock())
        monkeypatch.chdir(tmp_path)

        result = oh.update_firmware('MAIN', '1.24')
        assert result == oh.EXIT_UPLOAD
        assert result != oh.EXIT_OK

    def test_upload_failure_message_is_not_no_update_needed(self, monkeypatch, capsys):
        """A failed upload must never be reported as 'nothing needed'."""

        async def fake_walk(*a, **k):
            return [[(str(o), str(v)) for o, v in row] for row in REAL_SNMP_TABLE]

        monkeypatch.setattr(oh, '_snmp_walk_table', fake_walk)
        monkeypatch.setattr(oh, 'update_firmware', lambda c, v: oh.EXIT_UPLOAD)
        monkeypatch.setattr('builtins.input', lambda _=None: None)
        monkeypatch.setattr('sys.argv', ['oh-brother.py', '--yes', '1.2.3.4'])

        code = oh.main()
        out = capsys.readouterr().out
        assert code == oh.EXIT_UPLOAD
        assert 'No firmware update was needed' not in out
        assert 'FAILURE' in out

    def test_non_tty_without_yes_refuses_to_flash(self, monkeypatch, tmp_path):
        """stdin not a TTY and no --yes → no socket is ever created."""

        class FakeStdin:
            def isatty(self):
                return False

        oh.args = _args(yes=False)
        oh.model = 'HL-L2865DW'
        oh.spec = '0906'

        monkeypatch.setattr(oh.sys, 'stdin', FakeStdin())
        monkeypatch.setattr(oh, '_http_post', lambda *a, **k: (XML_UPDATE_124, None))
        monkeypatch.setattr(oh.urllib.request, 'urlopen', lambda *a, **k: _download_response())

        def no_socket(*a, **k):
            raise AssertionError('must refuse before opening a socket')

        monkeypatch.setattr(oh.socket, 'socket', no_socket)
        monkeypatch.setattr(oh.socket, 'getaddrinfo', no_socket)
        monkeypatch.chdir(tmp_path)

        assert oh.update_firmware('MAIN', '1.24') == oh.EXIT_REFUSED

    def test_validate_url_rejects_suffix_domain(self):
        """Look-alike domains must not pass the allow-list."""
        for url in ('http://evilbrother.com/CS/x.djf', 'http://notbrother.com/CS/x.djf'):
            valid, err = oh._validate_firmware_url(url)
            assert valid is False, url
            assert 'domain' in err.lower()

    def test_missing_model_or_spec_aborts_before_api(self, monkeypatch, capsys):
        """model=None aborts before any vendor request is made."""
        oh.args = _args()
        oh.model = None
        oh.spec = '0906'

        def no_post(*a, **k):
            raise AssertionError('vendor API must not be called')

        monkeypatch.setattr(oh, '_http_post', no_post)

        assert oh.update_firmware('MAIN', '1.24') == oh.EXIT_REFUSED
        out = capsys.readouterr().out
        assert 'REFUSING' in out

    def test_parse_artifact_version(self):
        """Version extraction from real Brother artifact names."""
        assert oh._parse_artifact_version('D02FZM_124Q_crypt.djf') == '1.24'
        assert oh._parse_artifact_version('D00KJY_F') is None
        assert oh._parse_artifact_version('LZ2751_L') is None

    def test_parse_artifact_version_accepts_two_to_four_digits(self):
        """The downgrade gate must see 2- and 4-digit artifacts, not warn past them."""
        assert oh._parse_artifact_version('X_12Q') == '1.2'
        assert oh._parse_artifact_version('Y_1245Q') == '1.245'
        # A single digit is not a Brother version encoding; keep it unparsed.
        assert oh._parse_artifact_version('V_1Q') is None


# ---------------------------------------------------------------------------
# Firmware write-path hardening: retention, incomplete transfer, verification
# ---------------------------------------------------------------------------


def _fake_tcp_socket(monkeypatch, sendfile_side_effect=None):
    """Install a fake raw-TCP socket and address lookup for update_firmware."""
    from unittest.mock import MagicMock

    sock = MagicMock()
    sock.__enter__.return_value = sock
    if sendfile_side_effect is not None:
        sock.sendfile.side_effect = sendfile_side_effect
    monkeypatch.setattr(
        oh.socket, 'getaddrinfo', lambda *a, **k: [(2, 1, 6, '', ('1.2.3.4', 9100))]
    )
    monkeypatch.setattr(oh.socket, 'socket', lambda *a: sock)
    return sock


class TestFlashHardening:
    """Recovery-image retention, incomplete transfers, post-flash checks."""

    def test_image_retained_on_upload_failure(self, monkeypatch, tmp_path):
        """A failed upload keeps the image for a retry."""
        oh.args = _args()
        oh.model = 'HL-L2865DW'
        oh.spec = '0906'

        monkeypatch.setattr(oh, '_http_post', lambda *a, **k: (XML_UPDATE_124, None))
        monkeypatch.setattr(oh.urllib.request, 'urlopen', lambda *a, **k: _download_response())
        monkeypatch.setattr(oh, '_tcp_upload', lambda f, sock: False)
        _fake_tcp_socket(monkeypatch)
        monkeypatch.chdir(tmp_path)

        result = oh.update_firmware('MAIN', '1.24')
        assert result == oh.EXIT_UPLOAD

        found = list(tmp_path.rglob('*.djf'))
        assert len(found) == 1
        assert found[0].read_bytes()

    def test_test_mode_retains_artifact(self, monkeypatch, tmp_path):
        """--test leaves the downloaded image on disk (it is the backup)."""
        oh.args = _args(test=True)
        oh.model = 'HL-L2865DW'
        oh.spec = '0906'

        monkeypatch.setattr(oh, '_http_post', lambda *a, **k: (XML_UPDATE_124, None))
        monkeypatch.setattr(oh.urllib.request, 'urlopen', lambda *a, **k: _download_response())
        monkeypatch.chdir(tmp_path)

        assert oh.update_firmware('MAIN', '1.24') == oh.EXIT_OK
        assert list(tmp_path.rglob('*.djf'))
        assert not list(tmp_path.rglob('*.part'))

    def test_no_partial_file_under_real_firmware_name(self, monkeypatch, tmp_path):
        """An interrupted download leaves no file at the real firmware name."""
        from unittest.mock import MagicMock

        oh.args = _args()
        oh.model = 'HL-L2865DW'
        oh.spec = '0906'

        monkeypatch.setattr(oh, '_http_post', lambda *a, **k: (XML_UPDATE_124, None))

        resp = MagicMock()
        resp.headers.get.return_value = None
        resp.read.side_effect = [b'\x00' * 102400, OSError('connection reset')]
        monkeypatch.setattr(oh.urllib.request, 'urlopen', lambda *a, **k: resp)
        monkeypatch.chdir(tmp_path)

        assert oh.update_firmware('MAIN', '1.24') == oh.EXIT_DOWNLOAD
        assert not (tmp_path / 'D02FZM_124Q_crypt.djf').exists()
        assert not list(tmp_path.rglob('*.part'))

    def test_sendfile_timeout_after_progress_marked_incomplete(self, monkeypatch, tmp_path, capsys):
        """A timeout after bytes were accepted is INCOMPLETE, not a failure."""
        oh.args = _args()
        oh.model = 'HL-L2865DW'
        oh.spec = '0906'

        monkeypatch.setattr(oh, '_http_post', lambda *a, **k: (XML_UPDATE_124, None))
        monkeypatch.setattr(oh.urllib.request, 'urlopen', lambda *a, **k: _download_response())
        _fake_tcp_socket(monkeypatch, sendfile_side_effect=[4096, TimeoutError('timed out')])
        monkeypatch.chdir(tmp_path)

        result = oh.update_firmware('MAIN', '1.24')
        out = capsys.readouterr().out

        assert result == oh.EXIT_UPLOAD
        assert result != oh.EXIT_OK
        assert 'INCOMPLETE' in out
        assert 'DO NOT POWER OFF' in out
        assert list(tmp_path.rglob('*.djf'))  # retained for reflash

    def test_post_upload_version_verified_via_snmp(self, monkeypatch, tmp_path, capsys):
        """A matching post-flash version is a verified success."""
        oh.args = _args(community='public')
        oh.model = 'HL-L2865DW'
        oh.spec = '0906'

        monkeypatch.setattr(oh, '_http_post', lambda *a, **k: (XML_UPDATE_124, None))
        monkeypatch.setattr(oh.urllib.request, 'urlopen', lambda *a, **k: _download_response())
        monkeypatch.setattr(oh, '_tcp_upload', lambda f, sock: True)
        monkeypatch.setattr(oh, '_query_printer_version', lambda ip, community, cat: '1.24')
        _fake_tcp_socket(monkeypatch)
        monkeypatch.chdir(tmp_path)

        result = oh.update_firmware('MAIN', '1.24')
        out = capsys.readouterr().out

        assert result == oh.EXIT_OK
        assert 'expected=1.24 actual=1.24' in out
        assert not list(tmp_path.rglob('*.djf'))  # deleted after verified flash

    def test_post_upload_version_mismatch_fails(self, monkeypatch, tmp_path, capsys):
        """A reboot back onto the wrong version is a real failure.

        Mismatch means the printer went down for the flash and came back on
        something else. The reading has to follow a reboot: the old firmware
        answering SNMP *before* the flash starts is not evidence of anything.
        """
        oh.args = _args()
        oh.model = 'HL-L2865DW'
        oh.spec = '0906'

        monkeypatch.setattr(oh, 'FLASH_VERIFY_POLL', 0)
        monkeypatch.setattr(oh.time, 'sleep', lambda s: None)
        monkeypatch.setattr(oh, '_http_post', lambda *a, **k: (XML_UPDATE_124, None))
        monkeypatch.setattr(oh.urllib.request, 'urlopen', lambda *a, **k: _download_response())
        monkeypatch.setattr(oh, '_tcp_upload', lambda f, sock: True)
        reads = iter(['1.20', None, '1.20'])
        monkeypatch.setattr(oh, '_query_printer_version', lambda ip, community, cat: next(reads))
        _fake_tcp_socket(monkeypatch)
        monkeypatch.chdir(tmp_path)

        result = oh.update_firmware('MAIN', '1.24')
        out = capsys.readouterr().out

        assert result == oh.EXIT_UPLOAD
        assert 'expected=1.24 actual=1.20' in out
        assert list(tmp_path.rglob('*.djf'))  # retained on real failure

    def test_post_upload_stalled_when_printer_never_restarts(self, monkeypatch, tmp_path, capsys):
        """A printer that never goes down never started the update.

        This is the observable signature of a model that accepts a raw-port
        firmware job and silently discards it: the transfer completes, the
        printer stays up, and the version never changes.
        """
        oh.args = _args()
        oh.model = 'HL-L2865DW'
        oh.spec = '0906'

        monkeypatch.setattr(oh, 'FLASH_REBOOT_GRACE', 0)
        monkeypatch.setattr(oh, 'FLASH_VERIFY_POLL', 0)
        monkeypatch.setattr(oh.time, 'sleep', lambda s: None)
        monkeypatch.setattr(oh, '_http_post', lambda *a, **k: (XML_UPDATE_126, None))
        monkeypatch.setattr(oh.urllib.request, 'urlopen', lambda *a, **k: _download_response())
        monkeypatch.setattr(oh, '_tcp_upload', lambda f, sock: True)
        monkeypatch.setattr(oh, '_query_printer_version', lambda ip, community, cat: '1.24')
        _fake_tcp_socket(monkeypatch)
        monkeypatch.chdir(tmp_path)

        result = oh.update_firmware('MAIN', '1.24')
        out = capsys.readouterr().out

        assert result == oh.EXIT_UPLOAD
        assert 'did NOT start' in out
        assert 'silently discarded' in out
        assert list(tmp_path.rglob('*.djf'))  # retained, nothing was written

    def test_post_upload_unverifiable_is_not_failure(self, monkeypatch, tmp_path, capsys):
        """Not coming back within the deadline is UNVERIFIED, not a failure."""
        oh.args = _args()
        oh.model = 'HL-L2865DW'
        oh.spec = '0906'

        monkeypatch.setattr(oh, 'FLASH_VERIFY_TIMEOUT', 0)
        monkeypatch.setattr(oh.time, 'sleep', lambda s: None)
        monkeypatch.setattr(oh, '_http_post', lambda *a, **k: (XML_UPDATE_124, None))
        monkeypatch.setattr(oh.urllib.request, 'urlopen', lambda *a, **k: _download_response())
        monkeypatch.setattr(oh, '_tcp_upload', lambda f, sock: True)
        monkeypatch.setattr(oh, '_query_printer_version', lambda ip, community, cat: None)
        _fake_tcp_socket(monkeypatch)
        monkeypatch.chdir(tmp_path)

        result = oh.update_firmware('MAIN', '1.24')
        out = capsys.readouterr().out

        assert result == oh.EXIT_UNVERIFIED
        assert result != oh.EXIT_UPLOAD
        assert 'could not be verified' in out
        assert 'FAILURE' not in out
        assert list(tmp_path.rglob('*.djf'))  # retained


# ---------------------------------------------------------------------------
# Interrupt handling (R19) and printer-unreachable classification (R15)
# ---------------------------------------------------------------------------


def _prepare_flash(monkeypatch, tmp_path):
    """Point update_firmware at a valid API response and a fake download."""
    oh.args = _args()
    oh.model = 'HL-L2865DW'
    oh.spec = '0906'
    monkeypatch.setattr(oh, '_http_post', lambda *a, **k: (XML_UPDATE_124, None))
    monkeypatch.setattr(oh.urllib.request, 'urlopen', lambda *a, **k: _download_response())
    monkeypatch.chdir(tmp_path)


class TestInterruptHandling:
    """A Ctrl-C must never brick a printer nor escape as a traceback."""

    def test_ctrl_c_during_upload_warns_and_retains_image(self, monkeypatch, tmp_path, capsys):
        """Interrupted mid-transfer: loud warning, image kept, exit 7."""
        _prepare_flash(monkeypatch, tmp_path)

        def interrupt(f, sock):
            raise KeyboardInterrupt()

        monkeypatch.setattr(oh, '_tcp_upload', interrupt)
        _fake_tcp_socket(monkeypatch)

        result = oh.update_firmware('MAIN', '1.24')
        out = capsys.readouterr().out

        assert result == oh.EXIT_UPLOAD
        assert 'INTERRUPTED DURING UPLOAD' in out
        assert 'DO NOT TURN THE PRINTER OFF' in out
        assert 'Traceback' not in out
        # The recovery image is the whole point of retaining it.
        assert len(list(tmp_path.rglob('*.djf'))) == 1

    def test_ctrl_c_during_verification_is_not_a_success(self, monkeypatch, tmp_path, capsys):
        """Interrupted during the confirm poll: UNVERIFIED, never exit 0."""
        _prepare_flash(monkeypatch, tmp_path)
        monkeypatch.setattr(oh, '_tcp_upload', lambda f, sock: True)
        _fake_tcp_socket(monkeypatch)

        def interrupt(*a, **k):
            raise KeyboardInterrupt()

        monkeypatch.setattr(oh, '_verify_flash', interrupt)

        result = oh.update_firmware('MAIN', '1.24')
        out = capsys.readouterr().out

        assert result == oh.EXIT_UNVERIFIED
        assert result != oh.EXIT_OK
        assert 'NOT confirmed' in out
        assert 'Traceback' not in out
        assert list(tmp_path.rglob('*.djf'))  # retained, not deleted

    def test_ctrl_c_during_snmp_exits_cleanly(self, monkeypatch, capsys):
        """A Ctrl-C before the upload window is a clean 130."""

        async def interrupted_walk(*a, **k):
            raise KeyboardInterrupt()

        monkeypatch.setattr(oh, '_snmp_walk_table', interrupted_walk)
        monkeypatch.setattr('builtins.input', lambda _=None: None)
        monkeypatch.setattr('sys.argv', ['oh-brother.py', '--yes', '1.2.3.4'])

        code = oh.main()
        out = capsys.readouterr().out

        assert code == oh.EXIT_INTERRUPTED == 130
        assert 'Interrupted.' in out
        assert 'Traceback' not in out


class TestPrinterUnreachable:
    """R15: an unresolvable or refusing printer is exit 4, not exit 1."""

    def test_unresolvable_host_returns_exit_printer(self, monkeypatch, tmp_path, capsys):
        _prepare_flash(monkeypatch, tmp_path)

        def no_resolve(*a, **k):
            raise oh.socket.gaierror('Name or service not known')

        monkeypatch.setattr(oh.socket, 'getaddrinfo', no_resolve)

        result = oh.update_firmware('MAIN', '1.24')
        out = capsys.readouterr().out

        assert result == oh.EXIT_PRINTER == 4
        assert 'Cannot reach the printer' in out
        # Nothing reached the printer, so the image must be kept.
        assert list(tmp_path.rglob('*.djf'))

    def test_refused_connection_returns_exit_printer(self, monkeypatch, tmp_path, capsys):
        from unittest.mock import MagicMock

        _prepare_flash(monkeypatch, tmp_path)

        sock = MagicMock()
        sock.connect.side_effect = ConnectionRefusedError('Connection refused')
        monkeypatch.setattr(
            oh.socket, 'getaddrinfo', lambda *a, **k: [(2, 1, 6, '', ('1.2.3.4', 9100))]
        )
        monkeypatch.setattr(oh.socket, 'socket', lambda *a: sock)

        result = oh.update_firmware('MAIN', '1.24')
        out = capsys.readouterr().out

        assert result == oh.EXIT_PRINTER
        assert 'Cannot reach the printer' in out
        # A socket that never connected must not be leaked.
        assert sock.close.called

    def test_upload_failure_after_connect_is_still_exit_upload(self, monkeypatch, tmp_path, capsys):
        """Connectivity (4) and rejection (7) must stay distinguishable."""
        _prepare_flash(monkeypatch, tmp_path)
        monkeypatch.setattr(oh, '_tcp_upload', lambda f, sock: False)
        _fake_tcp_socket(monkeypatch)

        result = oh.update_firmware('MAIN', '1.24')

        assert result == oh.EXIT_UPLOAD
        assert result != oh.EXIT_PRINTER


class TestSnmpFailureClassification:
    """An off or SNMP-disabled printer must report PRINTER (4), not ERROR (1).

    This was a sibling of the R15 flaw: _snmp_walk_table called sys.exit(1),
    so the most common real-world failure ("printer is off") reported an
    internal error and the documented exit code 4 was unreachable.
    """

    def test_walk_raises_snmp_error_on_no_response(self, monkeypatch):
        """The walk must raise, not kill the process with sys.exit()."""
        import asyncio
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        async def fake_walk(*a, **k):
            yield ('RequestTimedOut', None, None, [])

        monkeypatch.setattr(oh, 'walk_cmd', fake_walk)
        monkeypatch.setattr(
            oh, 'UdpTransportTarget', SimpleNamespace(create=AsyncMock(return_value=None))
        )
        monkeypatch.setattr(oh, 'SnmpDispatcher', lambda: None)
        monkeypatch.setattr(oh, 'CommunityData', lambda *a, **k: None)
        monkeypatch.setattr(oh, 'ObjectType', lambda *a: None)
        monkeypatch.setattr(oh, 'ObjectIdentity', lambda *a: None)

        with pytest.raises(oh.SnmpError) as excinfo:
            asyncio.run(oh._snmp_walk_table('1.2.3.4', 'public', '1.2.3.4'))

        assert excinfo.value.exit_code == oh.EXIT_PRINTER == 4
        assert 'No SNMP response' in str(excinfo.value)

    def test_no_snmp_response_maps_to_exit_printer(self, monkeypatch, capsys):
        """main() surfaces the walk's code, so 'printer off' exits 4."""

        async def no_response(*a, **k):
            raise oh.SnmpError('No SNMP response from 1.2.3.4 (RequestTimedOut).', oh.EXIT_PRINTER)

        monkeypatch.setattr(oh, '_snmp_walk_table', no_response)
        monkeypatch.setattr('sys.argv', ['oh-brother.py', '--yes', '1.2.3.4'])

        code = oh.main()
        err = capsys.readouterr().err

        assert code == oh.EXIT_PRINTER == 4
        assert 'No SNMP response' in err

    def test_snmp_protocol_error_stays_exit_error(self, monkeypatch, capsys):
        """A printer that answers but errors is not 'unreachable'."""

        async def bad_response(*a, **k):
            raise oh.SnmpError(
                'SNMP error reading the printer: noSuchName at 1.2.3.4', oh.EXIT_ERROR
            )

        monkeypatch.setattr(oh, '_snmp_walk_table', bad_response)
        monkeypatch.setattr('sys.argv', ['oh-brother.py', '--yes', '1.2.3.4'])

        code = oh.main()
        _err = capsys.readouterr().err

        assert code == oh.EXIT_ERROR == 1
        assert code != oh.EXIT_PRINTER

    def test_snmp_transport_uses_bounded_timeout_and_retries(self, monkeypatch):
        """The transport must state its budget, not inherit pysnmp's.

        pysnmp defaults to timeout=1, retries=5. The original code passed
        only timeout=30, so an unreachable printer cost 30*(5+1) = 180s.
        """
        import asyncio
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        captured = {}

        async def fake_create(address, *a, **k):
            captured['address'] = address
            captured.update(k)
            return MagicMock()

        async def empty_walk(*a, **k):
            for row in ():
                yield row

        monkeypatch.setattr(oh, 'UdpTransportTarget', SimpleNamespace(create=fake_create))
        monkeypatch.setattr(oh, 'walk_cmd', empty_walk)
        monkeypatch.setattr(oh, 'SnmpDispatcher', lambda: None)
        monkeypatch.setattr(oh, 'CommunityData', lambda *a, **k: None)
        monkeypatch.setattr(oh, 'ObjectType', lambda *a: None)
        monkeypatch.setattr(oh, 'ObjectIdentity', lambda *a: None)

        asyncio.run(oh._snmp_walk_table('1.2.3.4', 'public', '1.2.3.4'))

        assert captured['address'] == ('1.2.3.4', 161)
        assert captured['timeout'] == oh.SNMP_TIMEOUT == 5
        assert captured['retries'] == oh.SNMP_RETRIES == 1
        # The regression was retries silently defaulting to 5.
        assert captured['retries'] < 5
        # Worst case per request must be well under the old three minutes.
        assert captured['timeout'] * (captured['retries'] + 1) <= 15

    def test_snmp_stage_is_bounded_by_a_deadline(self, monkeypatch, capsys):
        """A walk that never returns is cut off, reported as exit 4."""
        import asyncio
        import time

        monkeypatch.setattr(oh, 'SNMP_DEADLINE', 0.05)

        async def hangs(*a, **k):
            await asyncio.sleep(30)

        monkeypatch.setattr(oh, '_snmp_walk_table', hangs)
        monkeypatch.setattr('sys.argv', ['oh-brother.py', '--yes', '1.2.3.4'])

        started = time.monotonic()
        code = oh.main()
        elapsed = time.monotonic() - started
        err = capsys.readouterr().err

        assert code == oh.EXIT_PRINTER
        assert 'within' in err
        assert elapsed < 5, 'SNMP_DEADLINE did not cut the walk off'

    def test_query_printer_version_tolerates_snmp_error(self, monkeypatch):
        """A rebooting printer must yield None, not raise.

        _verify_flash polls the printer while it is restarting, so SNMP
        silence during that window is expected. If changing the walk from
        sys.exit() to SnmpError broke this, a successful flash would be
        reported as a failure - the exact false negative the verification
        design exists to avoid.
        """

        async def silent(*a, **k):
            raise oh.SnmpError('No SNMP response', oh.EXIT_PRINTER)

        monkeypatch.setattr(oh, '_snmp_walk_table', silent)

        assert oh._query_printer_version('1.2.3.4', 'public', 'MAIN') is None

    def test_verify_flash_unverified_when_printer_stays_silent(self, monkeypatch):
        """Still-silent at the deadline is 'unverified', never a failure."""
        monkeypatch.setattr(oh, 'FLASH_VERIFY_TIMEOUT', 0)
        monkeypatch.setattr(oh, 'FLASH_VERIFY_POLL', 0)
        monkeypatch.setattr(oh, '_query_printer_version', lambda ip, community, cat: None)

        status, actual = oh._verify_flash('1.2.3.4', 'public', 'MAIN', '1.24')

        assert status == 'unverified'
        assert actual is None

    def test_verify_flash_does_not_trust_the_pre_reboot_read(self, monkeypatch):
        """The old firmware answering before the flash is not a verdict.

        A printer that is about to update correctly keeps answering SNMP with
        the running version for the first seconds after the upload. Reading
        that as final reported a false failure on every successful flash.
        """
        monkeypatch.setattr(oh, 'FLASH_VERIFY_POLL', 0)
        monkeypatch.setattr(oh.time, 'sleep', lambda s: None)
        reads = iter(['1.24', '1.24', None, None, '1.26'])
        monkeypatch.setattr(oh, '_query_printer_version', lambda ip, community, cat: next(reads))

        status, actual = oh._verify_flash('1.2.3.4', 'public', 'MAIN', '1.26')

        assert (status, actual) == ('ok', '1.26')

    def test_verify_flash_stalled_when_the_printer_never_goes_down(self, monkeypatch):
        """Still answering on the old version past the grace window.

        Nothing was written, so this must not be reported as a flash that
        failed to verify — the printer never started.
        """
        monkeypatch.setattr(oh, 'FLASH_REBOOT_GRACE', 0)
        monkeypatch.setattr(oh, 'FLASH_VERIFY_POLL', 0)
        monkeypatch.setattr(oh.time, 'sleep', lambda s: None)
        monkeypatch.setattr(oh, '_query_printer_version', lambda ip, community, cat: '1.24')

        status, actual = oh._verify_flash('1.2.3.4', 'public', 'MAIN', '1.26')

        assert (status, actual) == ('stalled', '1.24')


# ---------------------------------------------------------------------------
# Phase 5 — R11 readiness polling between categories
# ---------------------------------------------------------------------------


class TestPrinterReadiness:
    """R11: wait for the printer to answer, don't guess with a fixed sleep."""

    def test_ready_immediately_does_not_sleep_at_all(self, monkeypatch):
        monkeypatch.setattr(oh, '_printer_ready', lambda ip, community: True)
        slept = []
        monkeypatch.setattr(oh.time, 'sleep', lambda s: slept.append(s))

        waited = oh._wait_for_printer_ready('1.2.3.4', 'public')

        assert waited is not None
        assert waited < 1
        assert slept == []

    def test_polls_until_the_printer_answers(self, monkeypatch):
        probes = []

        def ready(ip, community):
            probes.append(1)
            return len(probes) >= 3

        monkeypatch.setattr(oh, '_printer_ready', ready)
        monkeypatch.setattr(oh.time, 'sleep', lambda s: None)

        waited = oh._wait_for_printer_ready('1.2.3.4', 'public', timeout=30, poll=0)

        assert waited is not None
        assert len(probes) == 3

    def test_gives_up_at_the_deadline(self, monkeypatch):
        """Bounded: it stops probing rather than looping forever."""
        probes = []

        def never(ip, community):
            probes.append(1)
            return False

        monkeypatch.setattr(oh, '_printer_ready', never)
        monkeypatch.setattr(oh.time, 'sleep', lambda s: None)

        assert oh._wait_for_printer_ready('1.2.3.4', 'public', timeout=0, poll=0) is None
        assert len(probes) == 1
        # The shipped default is a bounded window, not "wait forever".
        assert oh.READY_TIMEOUT == 300

    def test_snmp_failure_reads_as_not_ready(self, monkeypatch):
        """An off printer is 'not ready', never an exception."""

        async def broken(*a, **k):
            raise oh.SnmpError('no response', oh.EXIT_PRINTER)

        monkeypatch.setattr(oh, '_snmp_walk_table', broken)

        assert oh._printer_ready('1.2.3.4', 'public') is False

    def test_answering_printer_reads_as_ready(self, monkeypatch):
        async def table(*a, **k):
            return [[('1.2.3', '4')]]

        monkeypatch.setattr(oh, '_snmp_walk_table', table)

        assert oh._printer_ready('1.2.3.4', 'public') is True

    def test_empty_walk_reads_as_not_ready(self, monkeypatch):
        """A reply with no rows is not evidence the printer is up."""

        async def empty(*a, **k):
            return []

        monkeypatch.setattr(oh, '_snmp_walk_table', empty)

        assert oh._printer_ready('1.2.3.4', 'public') is False


# ---------------------------------------------------------------------------
# Phase 5 — R10 bounded download
# ---------------------------------------------------------------------------

PATH_XML_R10 = (
    b'<?xml version="1.0" encoding="UTF-8" ?>'
    b'<RESPONSEINFO>'
    b'<FIRMUPDATEINFO>'
    b'<VERSIONCHECK>0</VERSIONCHECK>'
    b'<PATH>http://update-akamai.brother.co.jp/CS/D02FZM_124Q_crypt.djf</PATH>'
    b'</FIRMUPDATEINFO>'
    b'</RESPONSEINFO>'
)


class TestBoundedDownload:
    """R10: a body larger than declared, or past the cap, is refused."""

    def _arm(self, monkeypatch, tmp_path, content_length, chunks):
        """Arms a mocked download. Returns a list recording each read()."""
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        remaining = list(chunks) + [b'']
        reads = []

        oh.args = SimpleNamespace(
            beta=False,
            verbose=False,
            test=True,
            yes=True,
            reflash=True,
            category=None,
            fw_version='B0000000000',
            ip='1.2.3.4',
            community='public',
            model=None,
            password=None,
        )
        oh.model = 'HL-L2865DW'
        oh.spec = '0906'

        monkeypatch.setattr(
            oh, '_http_post', lambda url, data, hdrs, timeout=30: (PATH_XML_R10, None)
        )

        def fake_urlopen(req, timeout=None):
            m = MagicMock()

            def read(size=-1):
                reads.append(size)
                return remaining.pop(0)

            m.headers = {'Content-Length': content_length}
            m.read.side_effect = read
            return m

        monkeypatch.setattr(oh.urllib.request, 'urlopen', fake_urlopen)
        monkeypatch.chdir(tmp_path)
        return reads

    def test_more_data_than_content_length_aborts(self, monkeypatch, tmp_path, capsys):
        """A source that keeps sending past its declared size is refused.

        Asserted on the reason, not merely the exit code: a pre-fix run also
        ended at EXIT_DOWNLOAD, but only after swallowing the whole stream and
        failing the integrity check. So the read count is the real evidence —
        the loop must stop at the first over-long chunk.
        """
        reads = self._arm(monkeypatch, tmp_path, '1024', [b'X' * 200000])

        code = oh.update_firmware('MAIN', '1.24')
        out = capsys.readouterr().out

        assert code == oh.EXIT_DOWNLOAD
        assert 'more data than its declared Content-Length' in out
        assert len(reads) == 1  # stopped at the first chunk
        assert not list(tmp_path.rglob('*.djf'))
        assert not list(tmp_path.rglob('*.part'))

    def test_exceeding_the_hard_cap_aborts(self, monkeypatch, tmp_path, capsys):
        """With no Content-Length at all the loop is still bounded.

        The body is a plausible size (over the 100 KB integrity floor), so a
        pre-fix run accepted it and reported success — the cap is the only
        thing that can fail this test.
        """
        monkeypatch.setattr(oh, 'DOWNLOAD_HARD_CAP', 4096, raising=False)
        reads = self._arm(monkeypatch, tmp_path, None, [b'X' * 200000, b'X'])

        code = oh.update_firmware('MAIN', '1.24')
        out = capsys.readouterr().out

        assert code == oh.EXIT_DOWNLOAD
        assert 'safety cap' in out
        assert len(reads) == 1
        assert not list(tmp_path.rglob('*.djf'))
        assert not list(tmp_path.rglob('*.part'))

    def test_an_honest_download_is_unaffected(self, monkeypatch, tmp_path):
        """The bound must not break a normal download."""
        body = b'F' * 200000
        reads = self._arm(monkeypatch, tmp_path, str(len(body)), [body])

        assert oh.update_firmware('MAIN', '1.24') == oh.EXIT_OK
        # One data read, plus the EOF read that ends the loop.
        assert len(reads) == 2
        retained = list((tmp_path / oh.BACKUP_DIRNAME).rglob('*.djf'))
        assert len(retained) == 1
        assert retained[0].stat().st_size == len(body)
        assert not list(tmp_path.rglob('*.part'))


# ---------------------------------------------------------------------------
# Phase 5 — R12 forced category / version flags
# ---------------------------------------------------------------------------


class TestForcedCategoryFlags:
    """R12: -f without -c was ignored, and -c alone discarded the installed
    version that the downgrade check is built on."""

    def _arm_snmp(self, monkeypatch):
        async def fake_walk_cmd(*args, **kwargs):
            table = []
            for snmp_row in REAL_SNMP_TABLE:
                varBinds = [(oid, val) for oid, val in snmp_row]
                table.append([(str(vb[0]), str(vb[1])) for vb in varBinds])
            return table

        monkeypatch.setattr(oh, '_snmp_walk_table', fake_walk_cmd)
        monkeypatch.setattr('builtins.input', lambda _=None: None)

    def test_fw_version_without_category_is_a_usage_error(self, monkeypatch):
        """A silently ignored flag is worse than a refusal."""
        self._arm_snmp(monkeypatch)
        monkeypatch.setattr('sys.argv', ['oh-brother.py', '-f', '1.23', '1.2.3.4'])

        with pytest.raises(SystemExit) as exc:
            oh.main()

        assert exc.value.code == oh.EXIT_USAGE

    def test_forced_category_keeps_the_installed_version(self, monkeypatch):
        """-c alone must carry the real installed version into the check."""
        self._arm_snmp(monkeypatch)
        called_with = []
        monkeypatch.setattr(
            oh, 'update_firmware', lambda c, v: called_with.append((c, v)) or oh.EXIT_OK
        )
        monkeypatch.setattr('sys.argv', ['oh-brother.py', '-c', 'MAIN', '1.2.3.4'])

        oh.main()

        # The SNMP version, not the B0000000000 sentinel.
        assert called_with == [('MAIN', '1.24')]

    def test_forced_category_unknown_to_snmp_is_refused(self, monkeypatch):
        """No installed version on record means a downgrade cannot be ruled
        out, so refuse rather than wave it through."""
        self._arm_snmp(monkeypatch)
        called_with = []
        monkeypatch.setattr(
            oh, 'update_firmware', lambda c, v: called_with.append((c, v)) or oh.EXIT_OK
        )
        monkeypatch.setattr('sys.argv', ['oh-brother.py', '-c', 'NOPE', '1.2.3.4'])

        assert oh.main() == oh.EXIT_REFUSED
        assert called_with == []

    def test_explicit_version_still_overrides(self, monkeypatch):
        self._arm_snmp(monkeypatch)
        called_with = []
        monkeypatch.setattr(
            oh, 'update_firmware', lambda c, v: called_with.append((c, v)) or oh.EXIT_OK
        )
        monkeypatch.setattr('sys.argv', ['oh-brother.py', '-c', 'SUB1', '-f', '3.00', '1.2.3.4'])

        oh.main()

        assert called_with == [('SUB1', '3.00')]

    def test_sentinel_is_not_a_version(self):
        """The default must be recognisable as 'not supplied'."""
        assert oh.FW_VERSION_SENTINEL == 'B0000000000'
        assert oh._version_tuple(oh.FW_VERSION_SENTINEL) is None


# ---------------------------------------------------------------------------
# Phase 5 — R13 beta gate
# ---------------------------------------------------------------------------


class TestBetaGate:
    """R13: --beta was its own consent token. It is not enough."""

    def test_beta_without_yes_is_refused(self, monkeypatch, capsys):
        reached = []

        async def walk(*a, **k):
            reached.append(1)
            return []

        monkeypatch.setattr(oh, '_snmp_walk_table', walk)
        monkeypatch.setattr('sys.argv', ['oh-brother.py', '--beta', '1.2.3.4'])

        assert oh.main() == oh.EXIT_REFUSED
        assert reached == []  # refused before any network work
        assert '--yes' in capsys.readouterr().out

    def test_beta_with_yes_passes_the_gate(self, monkeypatch):
        async def unreachable(*a, **k):
            raise oh.SnmpError('no response', oh.EXIT_PRINTER)

        monkeypatch.setattr(oh, '_snmp_walk_table', unreachable)
        monkeypatch.setattr('sys.argv', ['oh-brother.py', '--beta', '--yes', '1.2.3.4'])

        # Past the gate: an absent printer is a different failure.
        assert oh.main() == oh.EXIT_PRINTER

    def test_beta_with_test_passes_the_gate(self, monkeypatch):
        """--test cannot write, so inspecting a beta image needs no consent."""

        async def unreachable(*a, **k):
            raise oh.SnmpError('no response', oh.EXIT_PRINTER)

        monkeypatch.setattr(oh, '_snmp_walk_table', unreachable)
        monkeypatch.setattr('sys.argv', ['oh-brother.py', '--beta', '--test', '1.2.3.4'])

        code = oh.main()

        assert code != oh.EXIT_REFUSED
        assert code == oh.EXIT_PRINTER


# ---------------------------------------------------------------------------
# Phase 5 — R9 the FTP outcome must survive a failed QUIT
# ---------------------------------------------------------------------------


class TestFtpUploadOutcome:
    """R9: a completed STOR is a completed transfer, even if QUIT then fails."""

    def test_quit_failure_does_not_lose_a_completed_stor(self, monkeypatch, tmp_path):
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        oh.args = SimpleNamespace(
            beta=False,
            verbose=False,
            test=False,
            yes=True,
            reflash=True,
            category=None,
            fw_version='B0000000000',
            ip='1.2.3.4',
            community='public',
            password='admin',
            model=None,
        )
        oh.model = 'HL-L2865DW'
        oh.spec = '0906'

        body = b'F' * 200000

        monkeypatch.setattr(
            oh, '_http_post', lambda url, data, hdrs, timeout=30: (PATH_XML_R10, None)
        )

        def fake_urlopen(req, timeout=None):
            m = MagicMock()
            m.headers = {'Content-Length': str(len(body))}
            m.read.side_effect = [body, b'']
            return m

        monkeypatch.setattr(oh.urllib.request, 'urlopen', fake_urlopen)

        ftp = MagicMock()
        ftp.quit.side_effect = OSError('connection reset by peer')
        monkeypatch.setattr(oh, 'FTP', lambda *a, **k: ftp)

        verified = []
        monkeypatch.setattr(
            oh,
            '_verify_flash',
            lambda ip, community, cat, expected: verified.append(expected) or ('ok', '1.24'),
        )

        monkeypatch.chdir(tmp_path)

        code = oh.update_firmware('MAIN', '1.24')

        assert ftp.storbinary.called  # the transfer did happen
        assert verified == ['1.24']  # so verification decided the outcome
        assert code == oh.EXIT_OK


# ---------------------------------------------------------------------------
# Phase 5 — R17 diagnostic traceback
# ---------------------------------------------------------------------------


class TestFailureTraceback:
    """R17: an exit code alone is thin evidence for an unwatched failure."""

    @staticmethod
    def _explode(monkeypatch):
        async def boom(*a, **k):
            raise ValueError('boom')

        monkeypatch.setattr(oh, '_snmp_walk_table', boom)

    def test_traceback_is_printed(self, monkeypatch, capsys):
        self._explode(monkeypatch)
        monkeypatch.setattr('sys.argv', ['oh-brother.py', '1.2.3.4'])

        assert oh.main() == oh.EXIT_ERROR
        err = capsys.readouterr().err

        assert 'Traceback' in err
        # The raising frame's source line, not merely the message.
        assert 'raise ValueError(' in err  # the source line, quote-agnostic

    def test_short_form_shows_the_raising_frame_only(self, monkeypatch, capsys):
        self._explode(monkeypatch)
        monkeypatch.setattr('sys.argv', ['oh-brother.py', '1.2.3.4'])

        assert oh.main() == oh.EXIT_ERROR
        err = capsys.readouterr().err

        assert err.count('File "') == 1
        assert 'raise ValueError(' in err  # the source line, quote-agnostic

    def test_verbose_shows_the_full_chain(self, monkeypatch, capsys):
        self._explode(monkeypatch)
        monkeypatch.setattr('sys.argv', ['oh-brother.py', '-v', '1.2.3.4'])

        assert oh.main() == oh.EXIT_ERROR
        err = capsys.readouterr().err

        assert err.count('File "') >= 2  # full chain, not one frame
        assert 'raise ValueError(' in err  # the source line, quote-agnostic


# ---------------------------------------------------------------------------
# P1 — non-ASCII stdout must not launder the exit-code contract
# ---------------------------------------------------------------------------


class TestAsciiOutputPortability:
    """Operator-facing output must survive a non-UTF-8 stdout.

    U+2014 is unencodable in ascii/cp437/cp850/cp866/cp932/latin-1. When a
    print() of a safety message raises UnicodeEncodeError it escapes the
    per-window handlers and is caught by main()'s top-level `except
    Exception`, turning EXIT_UPLOAD (7) into EXIT_ERROR (1) and replacing
    the DO-NOT-POWER-OFF warning with a traceback.
    """

    def test_operator_messages_encode_as_ascii(self):
        """Every operator-facing message builder is ASCII-printable."""
        messages = [
            oh._retained_message('D02FZM_124Q_crypt.djf'),
            oh._incomplete_message('D02FZM_124Q_crypt.djf'),
            oh._interrupt_during_upload('D02FZM_124Q_crypt.djf'),
            oh._interrupt_during_verification('D02FZM_124Q_crypt.djf', '1.24'),
        ]
        for message in messages:
            assert isinstance(message, str)
            # Raises UnicodeEncodeError before the P1 fix.
            message.encode('ascii')

    def test_forced_upload_incomplete_survives_ascii_stdout(self, monkeypatch, tmp_path):
        """A partial upload under an ASCII stdout still returns 7, not 1."""
        import io
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        async def fake_walk_cmd(*args, **kwargs):
            return [[(str(oid), str(val)) for oid, val in row] for row in REAL_SNMP_TABLE]

        # Replace only the module's socket namespace.  Patching the stdlib
        # socket.socket *class* (as _fake_tcp_socket does) also breaks the
        # socket.socketpair() that asyncio uses for its event-loop self-pipe,
        # so main() never reaches the upload window.
        sock = MagicMock()
        sock.__enter__.return_value = sock
        monkeypatch.setattr(
            oh,
            'socket',
            SimpleNamespace(
                getaddrinfo=lambda *a, **k: [(2, 1, 6, '', ('1.2.3.4', 9100))],
                socket=lambda *a: sock,
                SOL_TCP=6,
                timeout=TimeoutError,
            ),
        )

        uploads = []

        def fake_upload(filename, sock_obj):
            uploads.append(filename)
            return oh.UPLOAD_INCOMPLETE

        monkeypatch.setattr('builtins.input', lambda _=None: None)
        monkeypatch.setattr(oh, '_snmp_walk_table', fake_walk_cmd)
        monkeypatch.setattr(oh, '_http_post', lambda *a, **k: (XML_UPDATE_124, None))
        monkeypatch.setattr(oh.urllib.request, 'urlopen', lambda *a, **k: _download_response())
        monkeypatch.setattr(oh, '_tcp_upload', fake_upload)
        monkeypatch.chdir(tmp_path)

        buffer = io.BytesIO()
        ascii_stdout = io.TextIOWrapper(buffer, encoding='ascii', errors='strict')
        monkeypatch.setattr(sys, 'stdout', ascii_stdout)
        monkeypatch.setattr('sys.argv', ['oh-brother.py', '--yes', '1.2.3.4'])

        code = oh.main()  # must not raise
        ascii_stdout.flush()
        out = buffer.getvalue().decode('ascii')

        # Prove the fixture actually drove the upload path; a pass for any
        # other reason would not exercise the defect.
        assert uploads, 'the upload window was never reached'
        assert code == oh.EXIT_UPLOAD == 7
        assert code != oh.EXIT_ERROR
        assert 'POWER OFF' in out
        assert 'Traceback' not in out


# ---------------------------------------------------------------------------
# Phase 7 follow-up (P5): backup root, unique partial name, early check
# ---------------------------------------------------------------------------


class TestBackupRootAndPartial:
    """P5: the retained-image root is configurable, the partial name is unique,
    and an unusable location is discovered before a single byte is downloaded.

    ``OH_BROTHER_BACKUP_DIR`` is referenced as a literal here rather than via
    ``oh.BACKUP_DIR_ENV`` so a pre-fix run exercises the real download path
    instead of dying with an AttributeError on a symbol the fix introduces.
    """

    def _arm(self, monkeypatch):
        oh.args = _args(test=True)
        oh.model = 'HL-L2865DW'
        oh.spec = '0906'
        monkeypatch.setattr(oh, '_http_post', lambda *a, **k: (XML_UPDATE_124, None))

    def test_unusable_backup_dir_fails_before_download(self, monkeypatch, tmp_path, capsys):
        """A backup root that cannot be created must cost no bandwidth."""
        self._arm(monkeypatch)
        blocker = tmp_path / 'blocked'
        blocker.write_text('not a directory')
        monkeypatch.setenv('OH_BROTHER_BACKUP_DIR', str(blocker))
        monkeypatch.chdir(tmp_path)

        downloads = []

        def no_download(*a, **k):
            downloads.append(1)
            raise AssertionError('urlopen must not be called')

        monkeypatch.setattr(oh.urllib.request, 'urlopen', no_download)

        sockets = []
        monkeypatch.setattr(oh.socket, 'socket', lambda *a, **k: sockets.append(1))
        monkeypatch.setattr(oh.socket, 'getaddrinfo', lambda *a, **k: sockets.append(1))

        result = oh.update_firmware('MAIN', '1.24')
        out = capsys.readouterr().out

        assert result == oh.EXIT_DOWNLOAD
        assert downloads == []
        assert sockets == []
        assert 'blocked' in out

    def test_backup_root_is_honoured(self, monkeypatch, tmp_path):
        """With the env var set the image lands under it, not under CWD."""
        self._arm(monkeypatch)
        root = tmp_path / 'data'
        root.mkdir()
        cwd = tmp_path / 'run'
        cwd.mkdir()
        monkeypatch.chdir(cwd)
        monkeypatch.setenv('OH_BROTHER_BACKUP_DIR', str(root))
        monkeypatch.setattr(oh.urllib.request, 'urlopen', lambda *a, **k: _download_response())

        assert oh.update_firmware('MAIN', '1.24') == oh.EXIT_OK

        retained = list((root / oh.BACKUP_DIRNAME).rglob('*.djf'))
        assert len(retained) == 1
        assert retained[0].stat().st_size == 204800
        assert not list(cwd.rglob('*.djf'))
        assert not list(cwd.rglob('*.part'))

    def test_backup_root_defaults_to_cwd(self, monkeypatch, tmp_path):
        """Unset, the root is the CWD, exactly as before the change."""
        self._arm(monkeypatch)
        monkeypatch.delenv('OH_BROTHER_BACKUP_DIR', raising=False)
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(oh.urllib.request, 'urlopen', lambda *a, **k: _download_response())

        assert oh.update_firmware('MAIN', '1.24') == oh.EXIT_OK
        retained = list((tmp_path / oh.BACKUP_DIRNAME).rglob('*.djf'))
        assert len(retained) == 1
        assert retained[0].stat().st_size == 204800

    def test_unique_partial_name_leaves_a_stale_partial_alone(self, monkeypatch, tmp_path):
        """A leftover <name>.part from an earlier run is never touched."""
        self._arm(monkeypatch)
        monkeypatch.delenv('OH_BROTHER_BACKUP_DIR', raising=False)
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(oh.urllib.request, 'urlopen', lambda *a, **k: _download_response())

        stale = tmp_path / 'D02FZM_124Q_crypt.djf.part'
        stale.write_bytes(b'stale in-flight bytes')

        assert oh.update_firmware('MAIN', '1.24') == oh.EXIT_OK

        assert stale.exists()
        assert stale.read_bytes() == b'stale in-flight bytes'
        assert list(tmp_path.rglob('*.part')) == [stale]

    def test_promotion_failure_keeps_the_verified_image(self, monkeypatch, tmp_path, capsys):
        """A failed promote must not delete the only verified copy."""
        self._arm(monkeypatch)
        monkeypatch.delenv('OH_BROTHER_BACKUP_DIR', raising=False)
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(oh.urllib.request, 'urlopen', lambda *a, **k: _download_response())

        def refuse_replace(src, dst):
            raise OSError(18, 'Invalid cross-device link')

        monkeypatch.setattr(oh.os, 'replace', refuse_replace)

        result = oh.update_firmware('MAIN', '1.24')
        out = capsys.readouterr().out

        assert result == oh.EXIT_DOWNLOAD
        assert 'Invalid cross-device link' in out
        parts = list(tmp_path.rglob('*.part'))
        assert len(parts) == 1
        assert parts[0].stat().st_size == 204800

    @pytest.mark.parametrize(
        'exc',
        [
            urllib.error.URLError('network down'),
            urllib.error.HTTPError('http://x/y.djf', 404, 'Not Found', None, None),
        ],
    )
    def test_download_that_never_starts_leaves_nothing_behind(self, monkeypatch, tmp_path, exc):
        """A urlopen failure must not leave a partial in the backup directory.

        Regression: the partial name was reserved with mkstemp *before* the
        request, so the printer-offline (URLError) and CDN-404 (HTTPError) paths
        each left a zero-byte hidden ``.<name>.<rand>.part`` behind. The name is
        reserved and released; the file is created by open() only once the
        response is in hand.
        """
        self._arm(monkeypatch)
        monkeypatch.delenv('OH_BROTHER_BACKUP_DIR', raising=False)
        monkeypatch.chdir(tmp_path)

        def boom(*a, **k):
            raise exc

        monkeypatch.setattr(oh.urllib.request, 'urlopen', boom)

        assert oh.update_firmware('MAIN', '1.24') == oh.EXIT_DOWNLOAD
        assert [p for p in tmp_path.rglob('*') if p.is_file()] == []


# ---------------------------------------------------------------------------
# Packet 3A (P4): the retained-copy digest record and change detection
# ---------------------------------------------------------------------------

ARTIFACT_124 = 'D02FZM_124Q_crypt.djf'


class TestRetainedCopyState:
    """The retained copy is classified from its sidecar digest record.

    ``_retained_copy_state`` distinguishes "the bytes on disk are the ones that
    were verified" from "we do not know" and "they changed". A malformed or
    unreadable sidecar is ``unrecorded``, never ``corrupt``: an absence of
    knowledge must not be promoted into a claim. The sidecar suffix is written
    as a literal here so a pre-fix run exercises the real path rather than dying
    with an AttributeError on a symbol the fix introduces.
    """

    def _seed(self, tmp_path, body, sidecar=None):
        backup = tmp_path / 'firmware_backups' / 'HL-L2865DW' / '1.24' / ARTIFACT_124
        backup.parent.mkdir(parents=True, exist_ok=True)
        backup.write_bytes(body)
        if sidecar is not None:
            (backup.parent / (ARTIFACT_124 + '.sha256')).write_text(sidecar)
        return str(backup)

    def test_absent_when_no_file(self, tmp_path):
        path = str(tmp_path / 'firmware_backups' / 'HL-L2865DW' / '1.24' / ARTIFACT_124)
        assert oh._retained_copy_state(path) == ('absent', None)

    def test_verified_when_recorded_digest_matches(self, tmp_path):
        body = b'V' * 204800
        digest = hashlib.sha256(body).hexdigest()
        path = self._seed(tmp_path, body, '%s  %s\n' % (digest, ARTIFACT_124))
        state, reported = oh._retained_copy_state(path)
        assert state == 'verified'
        assert reported == digest

    def test_unrecorded_when_sidecar_missing(self, tmp_path):
        path = self._seed(tmp_path, b'V' * 204800)
        assert oh._retained_copy_state(path) == ('unrecorded', None)

    def test_unrecorded_when_sidecar_is_garbage(self, tmp_path):
        path = self._seed(tmp_path, b'V' * 204800, 'not a sha256sum line at all\n')
        assert oh._retained_copy_state(path) == ('unrecorded', None)

    def test_unrecorded_when_sidecar_digest_is_not_hex(self, tmp_path):
        path = self._seed(tmp_path, b'V' * 204800, '%s  %s\n' % ('z' * 64, ARTIFACT_124))
        assert oh._retained_copy_state(path) == ('unrecorded', None)

    def test_corrupt_when_contents_replaced_under_the_sidecar(self, tmp_path):
        body = b'V' * 204800
        digest = hashlib.sha256(body).hexdigest()
        path = self._seed(tmp_path, body, '%s  %s\n' % (digest, ARTIFACT_124))
        with open(path, 'wb') as f:
            f.write(b'X' * 4096)

        state, reported = oh._retained_copy_state(path)
        assert state == 'corrupt'
        assert reported == hashlib.sha256(b'X' * 4096).hexdigest()
        assert reported != digest


class TestRetainedCopyChangeDetection:
    """``update_firmware`` uses the recorded digest for reporting and change
    detection only. It must never skip the download: a recorded hash is not
    vendor corroboration (gate G2), so even a matching record still fetches.
    """

    def _arm(self, monkeypatch, tmp_path, body):
        oh.args = _args(test=True)
        oh.model = 'HL-L2865DW'
        oh.spec = '0906'
        monkeypatch.delenv('OH_BROTHER_BACKUP_DIR', raising=False)
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(oh, '_http_post', lambda *a, **k: (XML_UPDATE_124, None))

        downloads = []

        def fake_urlopen(req, timeout=None):
            from unittest.mock import MagicMock

            downloads.append(req)
            m = MagicMock()
            m.headers = {'Content-Length': str(len(body))}
            m.read.side_effect = [body, b'']
            return m

        monkeypatch.setattr(oh.urllib.request, 'urlopen', fake_urlopen)
        return downloads

    def _backup(self, tmp_path):
        return tmp_path / 'firmware_backups' / 'HL-L2865DW' / '1.24' / ARTIFACT_124

    def _sidecar(self, backup):
        return backup.parent / (backup.name + '.sha256')

    def test_corrupt_record_is_not_treated_as_known_good(self, monkeypatch, tmp_path):
        """G2: a wrong recorded hash must still cause a real re-download."""
        fresh = b'B' * 204800
        backup = self._backup(tmp_path)
        backup.parent.mkdir(parents=True)
        backup.write_bytes(b'old retained bytes')
        self._sidecar(backup).write_text('%s  %s\n' % ('0' * 64, ARTIFACT_124))
        downloads = self._arm(monkeypatch, tmp_path, fresh)

        state, _ = oh._retained_copy_state(str(backup))
        assert state == 'corrupt'

        result = oh.update_firmware('MAIN', '1.24')

        assert len(downloads) == 1
        assert result == oh.EXIT_OK
        assert backup.read_bytes() == fresh

    def test_sidecar_written_in_sha256sum_format(self, monkeypatch, tmp_path):
        """Retention records one sha256sum-compatible line naming the basename."""
        body = b'C' * 204800
        self._arm(monkeypatch, tmp_path, body)

        result = oh.update_firmware('MAIN', '1.24')

        assert result == oh.EXIT_OK
        backup = self._backup(tmp_path)
        sidecar = self._sidecar(backup)
        assert sidecar.exists()
        expected = hashlib.sha256(body).hexdigest()
        text = sidecar.read_text()
        # sha256sum -c format: <digest><two spaces><basename><newline>.
        assert text == '%s  %s\n' % (expected, ARTIFACT_124)
        digest_field, name_field = text.split()
        assert digest_field == expected
        assert name_field == ARTIFACT_124
        assert os.path.sep not in name_field

    def test_unchanged_bytes_do_not_rewrite_the_retained_image(self, monkeypatch, tmp_path, capsys):
        """An identical vendor artifact leaves the file and its record alone."""
        body = b'D' * 204800
        backup = self._backup(tmp_path)
        backup.parent.mkdir(parents=True)
        backup.write_bytes(body)
        expected = '%s  %s\n' % (hashlib.sha256(body).hexdigest(), ARTIFACT_124)
        self._sidecar(backup).write_text(expected)
        before = backup.stat()
        downloads = self._arm(monkeypatch, tmp_path, body)

        result = oh.update_firmware('MAIN', '1.24')
        out = capsys.readouterr().out

        assert result == oh.EXIT_OK
        assert len(downloads) == 1
        after = backup.stat()
        assert (after.st_ino, after.st_mtime_ns) == (before.st_ino, before.st_mtime_ns)
        assert backup.read_bytes() == body
        assert 'DIFFERS' not in out
        assert self._sidecar(backup).read_text() == expected

    def test_changed_bytes_are_reported_and_promoted(self, monkeypatch, tmp_path, capsys):
        """A republished artifact under the same name is called out loudly."""
        old = b'O' * 204800
        new = b'N' * 204800
        old_digest = hashlib.sha256(old).hexdigest()
        new_digest = hashlib.sha256(new).hexdigest()
        backup = self._backup(tmp_path)
        backup.parent.mkdir(parents=True)
        backup.write_bytes(old)
        self._sidecar(backup).write_text('%s  %s\n' % (old_digest, ARTIFACT_124))
        downloads = self._arm(monkeypatch, tmp_path, new)

        result = oh.update_firmware('MAIN', '1.24')
        out = capsys.readouterr().out

        assert result == oh.EXIT_OK
        assert len(downloads) == 1
        assert old_digest in out
        assert new_digest in out
        assert 'DIFFERS' in out
        assert backup.read_bytes() == new
        assert self._sidecar(backup).read_text() == ('%s  %s\n' % (new_digest, ARTIFACT_124))

    def test_sidecar_write_failure_keeps_the_image_and_exit_code(
        self, monkeypatch, tmp_path, capsys
    ):
        """A record that cannot be written must never cost the image."""
        body = b'F' * 204800
        backup = self._backup(tmp_path)
        backup.parent.mkdir(parents=True)
        # A directory where the sidecar belongs makes the recording fail.
        self._sidecar(backup).mkdir()
        self._arm(monkeypatch, tmp_path, body)

        result = oh.update_firmware('MAIN', '1.24')
        out = capsys.readouterr().out

        assert result == oh.EXIT_OK
        assert backup.exists()
        assert backup.read_bytes() == body
        assert 'could not record' in out
        assert 'sha256' in out
        assert self._sidecar(backup).is_dir()
        assert not list(backup.parent.glob('*.tmp'))

    def test_unreadable_image_at_promote_is_exit_download_not_a_traceback(
        self, monkeypatch, tmp_path, capsys
    ):
        """A read failure after verification reports the documented code.

        ``_sha256_file`` runs twice on this path: once to classify the retained
        copy, once to digest the freshly downloaded partial. Only the second
        call may fail here, so the stand-in raises for the partial's path alone
        and the retained copy still classifies normally.
        """
        body = b'G' * 204800
        self._arm(monkeypatch, tmp_path, body)
        real = oh._sha256_file

        def flaky(path):
            if str(path).endswith('.part'):
                raise OSError('simulated read failure')
            return real(path)

        monkeypatch.setattr(oh, '_sha256_file', flaky)

        result = oh.update_firmware('MAIN', '1.24')
        out = capsys.readouterr().out

        assert result == oh.EXIT_DOWNLOAD
        assert 'could not re-read' in out
        # The image is named so it can be recovered -- never silently dropped.
        assert '.part' in out


class TestConditionalReuse:
    """Packet 3B: reuse a verified retained image when the vendor confirms it.

    A 304 is only sound when 3A says the retained copy is 'verified' AND the
    vendor supplied a validator to condition on. The digest alone is never
    vendor corroboration (gate G2), so a wrong or missing record still fetches.
    ``urllib`` raises ``HTTPError`` for a 304 rather than returning a response,
    so the reuse decision has to be taken inside the HTTPError handler; a naive
    form reports a successful reuse as a download failure.
    """

    ARTIFACT = ARTIFACT_124

    def _arm(self, monkeypatch, tmp_path):
        oh.args = _args(test=True)
        oh.model = 'HL-L2865DW'
        oh.spec = '0906'
        monkeypatch.delenv('OH_BROTHER_BACKUP_DIR', raising=False)
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(oh, '_http_post', lambda *a, **k: (XML_UPDATE_124, None))

    def _backup(self, tmp_path):
        return tmp_path / 'firmware_backups' / 'HL-L2865DW' / '1.24' / self.ARTIFACT

    def _sidecar(self, backup):
        return backup.parent / (backup.name + '.sha256')

    def _validator(self, backup):
        return backup.parent / (backup.name + '.validator')

    def _seed_verified(self, tmp_path, body, validator='ETag: "v1"\n'):
        backup = self._backup(tmp_path)
        backup.parent.mkdir(parents=True, exist_ok=True)
        backup.write_bytes(body)
        self._sidecar(backup).write_text(
            '%s  %s\n' % (hashlib.sha256(body).hexdigest(), self.ARTIFACT)
        )
        if validator is not None:
            self._validator(backup).write_text(validator)
        return backup

    def _http_304(self):
        return urllib.error.HTTPError(
            'http://update-akamai.brother.co.jp/CS/x.djf', 304, 'Not Modified', {}, None
        )

    def _fake_200(self, attempts, body, headers=None):
        from unittest.mock import MagicMock

        def fake_urlopen(req, timeout=None):
            attempts.append(req)
            m = MagicMock()
            m.headers = dict(headers or {})
            m.headers.setdefault('Content-Length', str(len(body)))
            m.read.side_effect = [body, b'']
            return m

        return fake_urlopen

    @staticmethod
    def _header(req, name):
        for key, value in req.headers.items():
            if key.lower() == name.lower():
                return value
        return None

    def test_304_reuses_the_retained_image(self, monkeypatch, tmp_path, capsys):
        """A vendor 304 means the retained image is current; no body is read."""
        body = b'B' * 204800
        backup = self._seed_verified(tmp_path, body)
        self._arm(monkeypatch, tmp_path)
        before = backup.stat()
        attempts = []

        def fake_urlopen(req, timeout=None):
            attempts.append(req)
            raise self._http_304()

        monkeypatch.setattr(oh.urllib.request, 'urlopen', fake_urlopen)

        result = oh.update_firmware('MAIN', '1.24')
        out = capsys.readouterr().out

        assert result == oh.EXIT_OK
        assert len(attempts) == 1
        after = backup.stat()
        assert (after.st_ino, after.st_mtime_ns) == (before.st_ino, before.st_mtime_ns)
        assert backup.read_bytes() == body
        assert '304' in out
        assert os.path.abspath(str(backup)) in out
        assert 'no download' in out.lower()
        assert not list(backup.parent.glob('*.part'))

    def test_304_is_not_reported_as_a_download_failure(self, monkeypatch, tmp_path, capsys):
        """The trap: urllib raises HTTPError(304); a reuse must not read as 6."""
        backup = self._seed_verified(tmp_path, b'B' * 204800)
        self._arm(monkeypatch, tmp_path)

        def fake_urlopen(req, timeout=None):
            raise self._http_304()

        monkeypatch.setattr(oh.urllib.request, 'urlopen', fake_urlopen)

        result = oh.update_firmware('MAIN', '1.24')
        out = capsys.readouterr().out

        assert result != oh.EXIT_DOWNLOAD
        assert result == oh.EXIT_OK
        assert 'try again later' not in out
        assert backup.exists()

    def test_no_validator_sends_no_conditional_header(self, monkeypatch, tmp_path):
        """No stored validator means nothing to condition on: fetch as before."""
        body = b'B' * 204800
        self._seed_verified(tmp_path, body, validator=None)
        self._arm(monkeypatch, tmp_path)
        attempts = []
        monkeypatch.setattr(oh.urllib.request, 'urlopen', self._fake_200(attempts, b'C' * 204800))

        result = oh.update_firmware('MAIN', '1.24')

        assert result == oh.EXIT_OK
        assert len(attempts) == 1
        assert self._header(attempts[0], 'If-None-Match') is None
        assert self._header(attempts[0], 'If-Modified-Since') is None
        assert self._backup(tmp_path).read_bytes() == b'C' * 204800

    def test_corrupt_retained_copy_never_produces_reuse(self, monkeypatch, tmp_path):
        """G2 carried through: a wrong recorded hash must not yield a 304 path."""
        backup = self._backup(tmp_path)
        backup.parent.mkdir(parents=True)
        backup.write_bytes(b'old retained bytes')
        self._sidecar(backup).write_text('%s  %s\n' % ('0' * 64, self.ARTIFACT))
        self._validator(backup).write_text('ETag: "v1"\n')
        self._arm(monkeypatch, tmp_path)
        fresh = b'D' * 204800
        attempts = []
        monkeypatch.setattr(oh.urllib.request, 'urlopen', self._fake_200(attempts, fresh))

        result = oh.update_firmware('MAIN', '1.24')

        assert result == oh.EXIT_OK
        assert len(attempts) == 1
        assert self._header(attempts[0], 'If-None-Match') is None
        assert backup.read_bytes() == fresh

    def test_200_with_validator_downloads_normally(self, monkeypatch, tmp_path):
        """A validator present still converges on the normal download path."""
        body = b'B' * 204800
        backup = self._seed_verified(tmp_path, body, validator='ETag: "v1"\n')
        self._arm(monkeypatch, tmp_path)
        attempts = []
        monkeypatch.setattr(
            oh.urllib.request, 'urlopen', self._fake_200(attempts, body, {'ETag': '"v1"'})
        )

        result = oh.update_firmware('MAIN', '1.24')

        assert result == oh.EXIT_OK
        assert len(attempts) == 1
        assert self._header(attempts[0], 'If-None-Match') == '"v1"'
        assert backup.exists()

    def test_last_modified_validator_sends_if_modified_since(self, monkeypatch, tmp_path):
        """A Last-Modified validator maps to If-Modified-Since, not If-None-Match."""
        body = b'B' * 204800
        when = 'Wed, 01 Jan 2025 00:00:00 GMT'
        backup = self._seed_verified(tmp_path, body, validator='Last-Modified: %s\n' % when)
        self._arm(monkeypatch, tmp_path)
        attempts = []
        monkeypatch.setattr(
            oh.urllib.request, 'urlopen', self._fake_200(attempts, body, {'Last-Modified': when})
        )

        result = oh.update_firmware('MAIN', '1.24')

        assert result == oh.EXIT_OK
        assert len(attempts) == 1
        assert self._header(attempts[0], 'If-Modified-Since') == when
        assert self._header(attempts[0], 'If-None-Match') is None
        assert backup.exists()

    def test_304_after_copy_stops_verifying_falls_back(self, monkeypatch, tmp_path, capsys):
        """A 304 for a copy that no longer re-verifies must fail open, not abort.

        The vendor then serves bytes identical to the recorded digest. A stale
        'verified' judgement would skip the promote and leave the tampered file
        in place, so the fallback must carry the re-checked classification
        forward and promote the freshly downloaded bytes.
        """
        body = b'B' * 204800
        backup = self._seed_verified(tmp_path, body)
        self._arm(monkeypatch, tmp_path)
        attempts = []
        state = {'n': 0}

        def fake_urlopen(req, timeout=None):
            attempts.append(req)
            state['n'] += 1
            if state['n'] == 1:
                # The file changes under us before the 304 is handled.
                backup.write_bytes(b'tampered')
                raise self._http_304()
            from unittest.mock import MagicMock

            m = MagicMock()
            m.headers = {'Content-Length': str(len(body))}
            m.read.side_effect = [body, b'']
            return m

        monkeypatch.setattr(oh.urllib.request, 'urlopen', fake_urlopen)

        result = oh.update_firmware('MAIN', '1.24')
        _out = capsys.readouterr().out

        assert result == oh.EXIT_OK
        assert len(attempts) == 2
        assert self._header(attempts[0], 'If-None-Match') == '"v1"'
        assert self._header(attempts[1], 'If-None-Match') is None
        assert backup.read_bytes() == body

    def test_validator_written_from_response_headers(self, monkeypatch, tmp_path):
        """Retention records the vendor validator as one '<Header>: <value>' line.

        Both headers are offered here because that is what the real CDN sends,
        and Last-Modified has to win: it is the one that CDN acts on, while
        If-None-Match carrying the same response's ETag returns 200 every time
        (probed: exact ETag, lowercase name, and md5 prefix alone). The
        ETag-only fallback is covered by the rewrite test below, so reverting
        this preference to the stronger-looking validator fails here.
        """
        self._arm(monkeypatch, tmp_path)
        attempts = []
        monkeypatch.setattr(
            oh.urllib.request,
            'urlopen',
            self._fake_200(
                attempts,
                b'B' * 204800,
                {'ETag': '"5f8a-1c2d"', 'Last-Modified': 'Thu, 30 Apr 2026 10:28:09 GMT'},
            ),
        )

        result = oh.update_firmware('MAIN', '1.24')

        assert result == oh.EXIT_OK
        validator = self._validator(self._backup(tmp_path))
        assert validator.read_text() == ('Last-Modified: Thu, 30 Apr 2026 10:28:09 GMT\n')

    def test_validator_rewritten_when_bytes_are_identical(self, monkeypatch, tmp_path):
        """Stale validators silently disable reuse, so they are refreshed."""
        body = b'B' * 204800
        backup = self._seed_verified(tmp_path, body, validator='Last-Modified: old\n')
        self._arm(monkeypatch, tmp_path)
        before = backup.stat()
        attempts = []
        monkeypatch.setattr(
            oh.urllib.request, 'urlopen', self._fake_200(attempts, body, {'ETag': '"new"'})
        )

        result = oh.update_firmware('MAIN', '1.24')

        assert result == oh.EXIT_OK
        after = backup.stat()
        assert (after.st_ino, after.st_mtime_ns) == (before.st_ino, before.st_mtime_ns)
        assert self._validator(backup).read_text() == 'ETag: "new"\n'

    def test_no_validator_headers_write_no_validator_file(self, monkeypatch, tmp_path):
        """Neither ETag nor Last-Modified means nothing to store."""
        self._arm(monkeypatch, tmp_path)
        attempts = []
        monkeypatch.setattr(oh.urllib.request, 'urlopen', self._fake_200(attempts, b'B' * 204800))

        result = oh.update_firmware('MAIN', '1.24')

        assert result == oh.EXIT_OK
        assert not self._validator(self._backup(tmp_path)).exists()


# ---------------------------------------------------------------------------
# Packet 4 (P6): NULL standard streams must not launder a refusal into exit 1
# ---------------------------------------------------------------------------


class TestStreamGuards:
    """P6: CPython sets sys.stdin/sys.stdout to None when the fd is closed.

    sys.stdin.isatty() and sys.stdout.flush() then raise AttributeError. The
    consent gate must treat a missing stream as "not a terminal" and return
    EXIT_REFUSED (9), not let the crash become EXIT_ERROR (1).
    """

    def test_isatty_none_is_false(self):
        assert oh._isatty(None) is False

    def test_isatty_non_tty_stream_is_false(self):
        import io

        assert oh._isatty(io.TextIOWrapper(io.BytesIO())) is False

    def test_isatty_tty_stream_is_true(self):
        class FakeTTY:
            def isatty(self):
                return True

        assert oh._isatty(FakeTTY()) is True

    def test_null_stdin_without_yes_refuses_not_errors(self, monkeypatch, capsys):
        """The headline assertion: a stream-less launch returns 9, not 1."""

        async def fake_walk_cmd(*args, **kwargs):
            return [[(str(oid), str(val)) for oid, val in row] for row in REAL_SNMP_TABLE]

        monkeypatch.setattr(oh, '_snmp_walk_table', fake_walk_cmd)
        monkeypatch.setattr(oh, '_http_post', lambda *a, **k: (XML_UPDATE_124, None))
        monkeypatch.setattr(oh.sys, 'stdin', None)
        monkeypatch.setattr('sys.argv', ['oh-brother.py', '1.2.3.4'])

        code = oh.main()
        out = capsys.readouterr().out

        assert code == oh.EXIT_REFUSED
        assert code == 9
        assert code != oh.EXIT_ERROR
        assert code != 1
        assert 'REFUSING to flash firmware unattended' in out

    def test_prompt_with_null_stdin_does_not_raise(self, monkeypatch):
        monkeypatch.setattr(oh.sys, 'stdin', None)
        oh.prompt('continue?')  # must be a silent no-op

    def test_null_stdout_survives_the_download_path(self, monkeypatch, tmp_path):
        """flush() and the progress dot must not raise on a None stdout."""
        oh.args = _args(test=True)
        oh.model = 'HL-L2865DW'
        oh.spec = '0906'
        monkeypatch.setattr(oh, '_http_post', lambda *a, **k: (XML_UPDATE_124, None))
        monkeypatch.setattr(oh.urllib.request, 'urlopen', lambda *a, **k: _download_response())
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(oh.sys, 'stdout', None)

        assert oh.update_firmware('MAIN', '1.24') == oh.EXIT_OK
        assert list(tmp_path.rglob('*.djf'))


# ---------------------------------------------------------------------------
# Packet 4 (P7): the flash-verification walk must be bounded
# ---------------------------------------------------------------------------


class TestBoundedWalkGap:
    """P7: _query_printer_version was the only SNMP call without a deadline."""

    def _slow_walk(self):
        async def walk(*args, **kwargs):
            await oh.asyncio.sleep(1.0)
            return [[(str(oid), str(val)) for oid, val in row] for row in REAL_SNMP_TABLE]

        return walk

    def test_query_printer_version_times_out_not_hangs(self, monkeypatch):
        import time

        monkeypatch.setattr(oh, '_snmp_walk_table', self._slow_walk())
        monkeypatch.setattr(oh, 'SNMP_DEADLINE', 0.05)

        start = time.monotonic()
        result = oh._query_printer_version('1.2.3.4', 'public', 'MAIN')
        elapsed = time.monotonic() - start

        assert result is None  # a slow walk is not a version answer
        assert elapsed < 0.5  # it returned via the deadline

    def test_verify_flash_classifies_timeout_as_unverified(self, monkeypatch):
        monkeypatch.setattr(oh, '_snmp_walk_table', self._slow_walk())
        monkeypatch.setattr(oh, 'SNMP_DEADLINE', 0.05)

        status, _actual = oh._verify_flash(
            '1.2.3.4', 'public', 'MAIN', '1.24', timeout=0.2, poll=0.01
        )

        assert status == 'unverified'
        assert status != 'mismatch'


# ---------------------------------------------------------------------------
# Packet 4 (P7/R16): the advisory reachability preflight warns, never refuses
# ---------------------------------------------------------------------------


class TestR16Preflight:
    """R16: a read-only TCP probe before the download, advisory only."""

    def _arm(self, monkeypatch, tmp_path, upload_result=True):
        from unittest.mock import MagicMock

        oh.model = 'HL-L2865DW'
        oh.spec = '0906'
        monkeypatch.setattr(oh, '_http_post', lambda *a, **k: (XML_UPDATE_124, None))
        monkeypatch.setattr(oh.urllib.request, 'urlopen', lambda *a, **k: _download_response())
        sock = MagicMock()
        sock.__enter__.return_value = sock
        monkeypatch.setattr(
            oh.socket, 'getaddrinfo', lambda *a, **k: [(2, 1, 6, '', ('1.2.3.4', 9100))]
        )
        monkeypatch.setattr(oh.socket, 'socket', lambda *a: sock)
        monkeypatch.setattr(oh, '_tcp_upload', lambda f, s: upload_result)
        monkeypatch.setattr(oh, '_verify_flash', lambda *a, **k: ('ok', '1.24'))
        monkeypatch.chdir(tmp_path)

    def test_skipped_under_test(self, monkeypatch, tmp_path):
        oh.args = _args(test=True)
        attempts = []
        monkeypatch.setattr(oh, '_printer_port_open', lambda *a: attempts.append(a) or True)
        self._arm(monkeypatch, tmp_path)

        assert oh.update_firmware('MAIN', '1.24') == oh.EXIT_OK
        assert attempts == []

    def test_skipped_with_password(self, monkeypatch, tmp_path):
        from unittest.mock import MagicMock

        oh.args = _args(password='admin')
        attempts = []
        monkeypatch.setattr(oh, '_printer_port_open', lambda *a: attempts.append(a) or True)
        self._arm(monkeypatch, tmp_path)
        ftp = MagicMock()
        monkeypatch.setattr(oh, 'FTP', lambda *a, **k: ftp)

        assert oh.update_firmware('MAIN', '1.24') == oh.EXIT_OK
        assert attempts == []

    def test_attempted_on_tcp_flash(self, monkeypatch, tmp_path):
        oh.args = _args()
        attempts = []
        monkeypatch.setattr(oh, '_printer_port_open', lambda *a: attempts.append(a) or True)
        self._arm(monkeypatch, tmp_path)

        assert oh.update_firmware('MAIN', '1.24') == oh.EXIT_OK
        assert len(attempts) == 1
        assert attempts[0][0] == '1.2.3.4'
        assert attempts[0][1] == 9100
        assert attempts[0][2] == oh.PREFLIGHT_TIMEOUT

    def test_refused_preflight_warns_and_continues(self, monkeypatch, tmp_path, capsys):
        """A closed port warns; it never refuses or aborts the download."""
        oh.args = _args()
        monkeypatch.setattr(oh, '_printer_port_open', lambda *a: False)
        self._arm(monkeypatch, tmp_path, upload_result=False)

        code = oh.update_firmware('MAIN', '1.24')
        out = capsys.readouterr().out

        assert code == oh.EXIT_UPLOAD
        assert code == 7
        assert '1.2.3.4:9100' in out
        assert 'will likely fail' in out
        assert 'retained' in out
        assert 'FTP' in out
        assert list(tmp_path.rglob('*.djf'))  # the download still happened
