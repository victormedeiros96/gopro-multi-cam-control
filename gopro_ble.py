import asyncio
import os
import subprocess
import tempfile
import time
from xml.sax.saxutils import escape
from bleak import BleakScanner, BleakClient

COMMAND_CHAR = "b5f90072-aa8d-11e3-9046-0002a5d5c51b"
SSID_CHAR = "b5f90002-aa8d-11e3-9046-0002a5d5c51b"
PASS_CHAR = "b5f90003-aa8d-11e3-9046-0002a5d5c51b"

WIFI_ON = bytes([0x03, 0x17, 0x01, 0x01])
WIFI_OFF = bytes([0x03, 0x17, 0x01, 0x00])


def scan_gopro(timeout=8):
    """Procura GoPros via Bluetooth (BLE) e retorna lista de {address, name}."""
    async def _scan():
        devices = await BleakScanner.discover(timeout=timeout, return_adv=True)
        results = []
        for addr, (dev, adv) in devices.items():
            name = dev.name or adv.local_name or ""
            if "gopro" in name.lower():
                results.append({"address": addr, "name": name})
        return results

    return asyncio.run(_scan())


def enable_wifi(address):
    """Conecta na GoPro via BLE, liga o WiFi AP e retorna {ssid, password}."""
    async def _enable():
        async with BleakClient(address) as client:
            await client.write_gatt_char(COMMAND_CHAR, WIFI_ON, response=True)
            await asyncio.sleep(0.5)
            ssid = ""
            password = ""
            try:
                ssid = (await client.read_gatt_char(SSID_CHAR)).decode("utf-8", errors="replace")
            except Exception:
                pass
            try:
                password = (await client.read_gatt_char(PASS_CHAR)).decode("utf-8", errors="replace")
            except Exception:
                pass
            return {"ssid": ssid, "password": password}

    return asyncio.run(_enable())


def _build_profile_xml(ssid, password):
    return f'''<?xml version="1.0"?>
<WLANProfile xmlns="http://www.microsoft.com/networking/WLAN/profile/v1">
    <name>{escape(ssid)}</name>
    <SSIDConfig>
        <SSID>
            <name>{escape(ssid)}</name>
        </SSID>
    </SSIDConfig>
    <connectionType>ESS</connectionType>
    <connectionMode>manual</connectionMode>
    <MSM>
        <security>
            <authEncryption>
                <authentication>WPA2PSK</authentication>
                <encryption>AES</encryption>
                <useOneX>false</useOneX>
            </authEncryption>
            <sharedKey>
                <keyType>passPhrase</keyType>
                <protected>false</protected>
                <keyMaterial>{escape(password)}</keyMaterial>
            </sharedKey>
        </security>
    </MSM>
</WLANProfile>'''


def connect_wifi(ssid, password):
    """Cria o perfil WiFi no Windows e conecta automaticamente."""
    fd, path = tempfile.mkstemp(suffix=".xml")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(_build_profile_xml(ssid, password))

    try:
        subprocess.run(
            ["netsh", "wlan", "add", "profile", f"filename={path}", "user=current"],
            check=True, capture_output=True, text=True,
        )
        subprocess.run(
            ["netsh", "wlan", "connect", f"ssid={ssid}", f"name={ssid}"],
            check=True, capture_output=True, text=True,
        )
    finally:
        try:
            os.remove(path)
        except OSError:
            pass

