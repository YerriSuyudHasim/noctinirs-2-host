from noctinirs_host.control import protocol as p
from noctinirs_host.control.engineering_fake import EngineeringFake
from noctinirs_host.control.pairing import ADDRESS, PORT, SSID, AccessPoint, candidates
from noctinirs_host.control.session import LoopbackTransport
from noctinirs_host.engineering import EngineeringRun
from noctinirs_host.geometry import load_geometry
from test_engineering_run import GEOMETRY, poll
import pytest


AP = AccessPoint(b"fake adapter", SSID, b"\0\1\2\3\4\5", -40, True)


class WifiFake:
    joined = None

    def scan(self):
        return [AP, AccessPoint(AP.interface, b"NoctiNIRS-guest", AP.bssid, -10),
                AccessPoint(AP.interface, AP.ssid, AP.bssid, -70),
                AccessPoint(AP.interface, AP.ssid, b"\0\1\2\3\4\6", -50, True)]

    def associate(self, ap, password=None):
        self.joined = ap

    def associated(self, ap):
        return self.joined == ap


class DeviceFake(EngineeringFake):
    def _reply(self, rid, opcode, status, extension=b""):
        if opcode == p.OP_INFO and extension:
            extension = b"NoctiNIRS2/0.1.0;wire=ADR0004/1;" + extension
        return super()._reply(rid, opcode, status, extension)


def test_scan_only_returns_exact_ssid_and_retains_distinct_bssids():
    aps = candidates(WifiFake())
    assert len(aps) == 2 and aps[0] == AP
    assert aps[0].bssid != aps[1].bssid


@pytest.mark.parametrize("valid_identity", [True, False])
def test_pair_and_reconnect_verify_identity_without_start(valid_identity):
    wifi, transports = WifiFake(), []

    def udp(address, port):
        assert (address, port) == (ADDRESS, int(PORT))
        device = DeviceFake() if valid_identity else EngineeringFake()
        transport = LoopbackTransport(device)
        transport.endpoint = address, port
        transports.append(transport)
        return transport

    run = EngineeringRun(load_geometry(GEOMETRY), wifi=wifi, udp_factory=udp)
    try:
        run.discover()
        if valid_identity:
            run.pair(AP)
            assert run.connected and run.stopped
            run._lose_link("test link loss")
            run._reconnect_at = 0
            run.tick()
            assert run.connected and run.stopped and not run.configured and run.calibration is None
            assert len(transports) == 2
        else:
            with pytest.raises(ValueError, match="identity"):
                run.pair(AP)
            assert not run.connected
    finally:
        run.shutdown()
        poll(run, lambda: run.finished)
