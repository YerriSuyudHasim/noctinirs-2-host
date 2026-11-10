"""Windows SoftAP discovery. WLAN credentials stay in the Windows per-user store."""

from __future__ import annotations

import ctypes as c
import sys
import time
from dataclasses import dataclass
from typing import Protocol
from xml.sax.saxutils import escape

# Firmware FIX6 d11548065d0c24c6b46cb7672827d323d1f69d29, nn2_wifi.c and
# docs/SOFTAP_CURRENT.md. All units use the same name, so keep the BSSID.
SSID = b"NoctiNIRS"
ADDRESS = "192.168.4.1"
PORT = "3333"


@dataclass(frozen=True)
class AccessPoint:
    interface: bytes
    ssid: bytes
    bssid: bytes
    rssi: int
    saved: bool = False

    @property
    def label(self):
        return f"{self.ssid.decode('ascii')} · {self.bssid.hex(':')} ({self.rssi} dBm)"

    @property
    def profile(self):
        return "NoctiNIRS-" + self.bssid.hex()


class Wifi(Protocol):
    def scan(self) -> list[AccessPoint]: ...
    def associate(self, access_point: AccessPoint, password: str | None = None) -> None: ...
    def associated(self, access_point: AccessPoint) -> bool: ...


def candidates(wifi: Wifi):
    seen = {}
    for item in wifi.scan():
        if item.ssid == SSID and len(item.bssid) == 6:
            key = (item.interface, item.bssid)
            if key not in seen or seen[key].rssi < item.rssi:
                seen[key] = item
    return sorted(seen.values(), key=lambda item: (-item.rssi, item.bssid))


def confirm_device(banner, info):
    # INFO comes from a validated ADR-0004 request/reply at the selected endpoint.
    # This is a product identity check, not cryptographic authentication.
    return banner.split(";", 1)[0].startswith("NoctiNIRS2/") and info.get("wire") == "ADR0004/1"


# Layouts from Windows SDK wlanapi.h / windot11.h, Wi-Fi client API v2.
U32 = c.c_uint32
Mac = c.c_ubyte * 6
Guid = c.c_ubyte * 16
Pointer = c.c_void_p
INFRASTRUCTURE = 1


class _Ssid(c.Structure):
    _fields_ = [("length", U32), ("data", c.c_ubyte * 32)]


class _Interface(c.Structure):
    _fields_ = [("guid", Guid), ("description", c.c_wchar * 256), ("state", U32)]


class _Bss(c.Structure):
    _fields_ = [("ssid", _Ssid), ("phy_id", U32), ("mac", Mac), ("bss_type", U32),
                ("phy_type", U32), ("rssi", c.c_int32), ("quality", U32),
                ("in_domain", c.c_ubyte), ("beacon_period", c.c_uint16),
                ("timestamp", c.c_uint64), ("host_timestamp", c.c_uint64),
                ("capability", c.c_uint16), ("frequency", U32), ("rate_count", U32),
                ("rates", c.c_uint16 * 126), ("ie_offset", U32), ("ie_size", U32)]


class _AssociationPrefix(c.Structure):
    _fields_ = [("state", U32), ("mode", U32), ("profile", c.c_wchar * 256),
                ("ssid", _Ssid), ("bss_type", U32), ("mac", Mac)]


class _BssidList(c.Structure):
    _fields_ = [("kind", c.c_ubyte), ("revision", c.c_ubyte), ("size", c.c_uint16),
                ("count", U32), ("total", U32), ("mac", Mac)]


class _Connection(c.Structure):
    _fields_ = [("mode", U32), ("profile", c.c_wchar_p), ("ssid", Pointer),
                ("desired", c.POINTER(_BssidList)), ("bss_type", U32), ("flags", U32)]


def _profile_xml(ap, password):
    if not (8 <= len(password) <= 63 and all(32 <= ord(char) <= 126 for char in password)):
        raise ValueError("Enter the device Wi-Fi password (8–63 printable ASCII characters).")
    return (
        '<WLANProfile xmlns="http://www.microsoft.com/networking/WLAN/profile/v1">'
        f'<name>{ap.profile}</name><SSIDConfig><SSID><hex>{SSID.hex().upper()}</hex>'
        '</SSID></SSIDConfig><connectionType>ESS</connectionType>'
        '<connectionMode>manual</connectionMode><MSM><security><authEncryption>'
        '<authentication>WPA2PSK</authentication><encryption>AES</encryption><useOneX>false</useOneX>'
        '</authEncryption><sharedKey><keyType>passPhrase</keyType><protected>false</protected>'
        f'<keyMaterial>{escape(password)}</keyMaterial></sharedKey></security></MSM></WLANProfile>'
    )


class WindowsWifi:
    """Native Wi-Fi calls run on the session worker, with bounded waits."""

    def __init__(self):
        self._api = None

    def _open(self):
        if sys.platform != "win32":
            raise ValueError("Wi-Fi discovery requires Windows. Use the advanced UDP connection.")
        if self._api is None:
            self._api = c.WinDLL("wlanapi")
            h, p, n, w = Pointer, Pointer, U32, c.c_wchar_p
            signatures = {
                "WlanOpenHandle": (n, p, p, p),
                "WlanCloseHandle": (h, p),
                "WlanFreeMemory": (p,),
                "WlanEnumInterfaces": (h, p, p),
                "WlanScan": (h, p, p, p, p),
                "WlanGetNetworkBssList": (h, p, p, n, c.c_int, p, p),
                "WlanQueryInterface": (h, p, n, p, p, p, p),
                "WlanGetProfile": (h, p, w, p, p, p, p),
                "WlanSetProfile": (h, p, n, w, w, c.c_int, p, p),
                "WlanConnect": (h, p, p, p),
            }
            for name, arguments in signatures.items():
                fn = getattr(self._api, name)
                fn.argtypes = arguments
                fn.restype = None if name == "WlanFreeMemory" else U32
        version, handle = U32(), Pointer()
        self._check(self._api.WlanOpenHandle(2, None, c.byref(version), c.byref(handle)))
        return handle

    @staticmethod
    def _check(code):
        if code == 5:
            raise ValueError("Windows blocked Wi-Fi access. Enable Location for desktop apps, or use advanced UDP.")
        if code:
            raise ValueError(f"Windows Wi-Fi request failed ({code}). Check that Wi-Fi is enabled and try again.")

    def _interfaces(self, handle):
        ptr = Pointer()
        self._check(self._api.WlanEnumInterfaces(handle, None, c.byref(ptr)))
        try:
            count = U32.from_address(ptr.value).value
            return [bytes(_Interface.from_address(ptr.value + 8 + i * c.sizeof(_Interface)).guid)
                    for i in range(count)]
        finally:
            self._api.WlanFreeMemory(ptr)

    def _saved(self, handle, ap):
        ptr, flags = Pointer(), U32()
        rc = self._api.WlanGetProfile(handle, Guid.from_buffer_copy(ap.interface),
                                      ap.profile, None, c.byref(ptr), c.byref(flags), None)
        if rc == 1168:  # ERROR_NOT_FOUND
            return False
        self._check(rc)
        self._api.WlanFreeMemory(ptr)
        return True

    def _bss(self, handle, interface):
        ptr = Pointer()
        self._check(self._api.WlanGetNetworkBssList(handle, Guid.from_buffer_copy(interface),
                                                   None, INFRASTRUCTURE, False, None, c.byref(ptr)))
        try:
            count = U32.from_address(ptr.value + 4).value
            result = []
            for i in range(count):
                row = _Bss.from_address(ptr.value + 8 + i * c.sizeof(_Bss))
                if bytes(row.ssid.data[:row.ssid.length]) == SSID:
                    ap = AccessPoint(interface, SSID, bytes(row.mac), row.rssi)
                    result.append(AccessPoint(interface, SSID, ap.bssid, ap.rssi, self._saved(handle, ap)))
            return result
        finally:
            self._api.WlanFreeMemory(ptr)

    def scan(self):
        handle = self._open()
        try:
            interfaces = self._interfaces(handle)
            if not interfaces:
                raise ValueError("No Windows Wi-Fi adapter found. Enable Wi-Fi or use advanced UDP.")
            for interface in interfaces:
                self._check(self._api.WlanScan(handle, Guid.from_buffer_copy(interface), None, None, None))
            # WlanScan initiates an asynchronous refresh. The list is cached by Windows.
            time.sleep(3)
            return [ap for interface in interfaces for ap in self._bss(handle, interface)]
        finally:
            self._api.WlanCloseHandle(handle, None)

    def _associated(self, handle, ap):
        ptr, size = Pointer(), U32()
        rc = self._api.WlanQueryInterface(handle, Guid.from_buffer_copy(ap.interface), 7,
                                          None, c.byref(size), c.byref(ptr), None)
        if rc == 5023:  # ERROR_INVALID_STATE when the interface has no association
            return False
        self._check(rc)
        try:
            row = _AssociationPrefix.from_address(ptr.value)
            return (row.state == 1 and bytes(row.mac) == ap.bssid
                    and bytes(row.ssid.data[:row.ssid.length]) == SSID)
        finally:
            self._api.WlanFreeMemory(ptr)

    def associated(self, ap):
        handle = self._open()
        try:
            return self._associated(handle, ap)
        finally:
            self._api.WlanCloseHandle(handle, None)

    def associate(self, ap, password=None):
        if ap.ssid != SSID or len(ap.bssid) != 6:
            raise ValueError("Choose a discovered NoctiNIRS access point.")
        handle = self._open()
        guid = Guid.from_buffer_copy(ap.interface)
        try:
            if self._associated(handle, ap):
                return
            if password is not None:
                reason = U32()
                # WLAN_PROFILE_USER=2. Windows encrypts the key in its profile store.
                self._check(self._api.WlanSetProfile(handle, guid, 2, _profile_xml(ap, password),
                                                     None, True, None, c.byref(reason)))
            elif not self._saved(handle, ap):
                raise ValueError("Enter the device Wi-Fi password once to pair.")
            desired = _BssidList(0x80, 1, c.sizeof(_BssidList), 1, 1, Mac(*ap.bssid))
            args = _Connection(0, ap.profile, None, c.pointer(desired), INFRASTRUCTURE, 0)
            self._check(self._api.WlanConnect(handle, guid, c.byref(args), None))
            for _ in range(20):
                if self._associated(handle, ap):
                    return
                time.sleep(.25)
            raise ValueError("Windows could not join the selected NoctiNIRS. Check power, range and password.")
        finally:
            self._api.WlanCloseHandle(handle, None)
