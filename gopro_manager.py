import requests
import threading
import time
import ipaddress
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import ifaddr


class GoProCamera:
    def __init__(self, ip, name="", model="", serial=""):
        self.ip = ip
        self.name = name or f"GoPro_{ip}"
        self.model = model
        self.serial = serial
        self.base_url = f"http://{ip}:8080/gopro"
        self.status = {}
        self.battery_level = 0
        self.is_recording = False
        self._lock = threading.Lock()

    def get_status(self):
        try:
            response = requests.get(f"{self.base_url}/camera/state", timeout=3)
            if response.status_code == 200:
                data = response.json()
                with self._lock:
                    self.status = data
                    self.battery_level = self._parse_battery(data)
                    self.is_recording = self._parse_recording(data)
                return data
        except Exception as e:
            print(f"Erro ao obter status de {self.ip}: {e}")
        return None

    def get_info(self):
        try:
            response = requests.get(f"{self.base_url}/camera/info", timeout=3)
            if response.status_code == 200:
                return response.json()
        except Exception as e:
            print(f"Erro ao obter info de {self.ip}: {e}")
        return None

    def _parse_battery(self, data):
        status = data.get("status", {})
        level = status.get("70")
        if isinstance(level, int) and 0 <= level <= 100:
            return level
        bars = status.get("2")
        if isinstance(bars, int) and 0 <= bars <= 4:
            return bars * 25
        return 0

    def _parse_recording(self, data):
        status = data.get("status", {})
        return bool(status.get("8", 0))

    def enable_usb_control(self):
        try:
            response = requests.get(
                f"{self.base_url}/camera/control/wired_usb", params={"p": 1}, timeout=3
            )
            return response.status_code == 200
        except Exception as e:
            print(f"Erro ao ativar controle USB em {self.ip}: {e}")
        return False

    def sync_datetime(self):
        """Sincroniza data/hora da câmera com o relógio do PC."""
        now = datetime.now()
        params = {
            "date": now.strftime("%Y_%m_%d"),
            "time": now.strftime("%H_%M_%S"),
            "tzone": -time.timezone // 60,
            "dst": 1 if time.localtime().tm_isdst > 0 else 0,
        }
        try:
            response = requests.get(
                f"{self.base_url}/camera/set_date_time", params=params, timeout=3
            )
            if response.status_code == 200:
                return (True, "ok")
            return (False, f"HTTP {response.status_code}")
        except Exception as e:
            return (False, str(e))

    def start_recording(self):
        self.enable_usb_control()
        try:
            response = requests.get(f"{self.base_url}/camera/shutter/start", timeout=3)
            if response.status_code == 200:
                with self._lock:
                    self.is_recording = True
                return (True, "ok")
            msg = f"Erro HTTP {response.status_code}"
            if response.status_code == 500:
                msg = "Recusado (bateria baixa ou sem cartão SD?)"
            return (False, msg)
        except Exception as e:
            return (False, str(e))

    def stop_recording(self):
        try:
            response = requests.get(f"{self.base_url}/camera/shutter/stop", timeout=3)
            if response.status_code == 200:
                with self._lock:
                    self.is_recording = False
                return (True, "ok")
            return (False, f"Erro HTTP {response.status_code}")
        except Exception as e:
            return (False, str(e))

    def get_settings(self):
        """Lê todas as configurações da câmera (dict setting_id -> option)."""
        try:
            response = requests.get(f"{self.base_url}/camera/state", timeout=5)
            if response.status_code == 200:
                return response.json().get("settings", {})
        except Exception as e:
            print(f"Erro ao ler settings de {self.ip}: {e}")
        return {}

    def set_setting(self, setting_id, option):
        """Aplica uma única configuração na câmera."""
        try:
            response = requests.get(
                f"{self.base_url}/camera/setting",
                params={"setting": setting_id, "option": option},
                timeout=5,
            )
            return response.status_code == 200
        except Exception:
            return False

    def start_stream(self, port=8554):
        try:
            response = requests.get(
                f"{self.base_url}/camera/stream/start", params={"port": port}, timeout=3
            )
            return response.status_code == 200
        except Exception as e:
            print(f"Erro ao iniciar stream em {self.ip}: {e}")
        return False

    def stop_stream(self):
        try:
            requests.get(f"{self.base_url}/camera/stream/stop", timeout=3)
        except Exception:
            pass

    def get_battery_level(self):
        with self._lock:
            return self.battery_level

    def is_currently_recording(self):
        with self._lock:
            return self.is_recording


def check_gopro_ip(ip):
    """Verifica se um IP é realmente uma GoPro via API Open GoPro"""
    try:
        r = requests.get(f"http://{ip}:8080/gopro/camera/info", timeout=1.0)
        if r.status_code == 200:
            data = r.json()
            model = data.get("model_name", "")
            if model and "HERO" in model.upper():
                return {
                    "ip": ip,
                    "name": data.get("ap_ssid", "") or f"GoPro_{ip}",
                    "model": model,
                    "serial": data.get("serial_number", ""),
                    "service_name": "",
                }
    except Exception:
        pass
    return None


def get_local_subnets():
    """Retorna lista de (subrede, nome_adaptador) para cada adaptador ativo"""
    results = []
    for adapter in ifaddr.get_adapters():
        for ip in adapter.ips:
            addr = ip.ip
            if not isinstance(addr, str):
                continue
            if addr.startswith("127.") or addr.startswith("169.254."):
                continue
            if ":" in addr:
                continue
            try:
                prefix = ip.network_prefix
                network = str(ipaddress.ip_network(f"{addr}/{prefix}", strict=False))
                results.append((network, adapter.nice_name))
            except ValueError:
                continue

    seen = set()
    unique = []
    for net, name in results:
        if net not in seen:
            seen.add(net)
            unique.append((net, name))
    return unique


def _is_usb_adapter(adapter_name):
    n = (adapter_name or "").lower()
    return "usbncm" in n or "rndis" in n


def _is_wifi_adapter(adapter_name):
    n = (adapter_name or "").lower()
    return any(k in n for k in ("wifi", "wi-fi", "wireless", "wlan"))


def scan_subnet(network_str):
    """Varre uma sub-rede inteira procurando por GoPros"""
    found = []
    network = ipaddress.ip_network(network_str, strict=False)
    hosts = [str(h) for h in network.hosts()]
    if not hosts:
        return found

    with ThreadPoolExecutor(max_workers=128) as executor:
        futures = {executor.submit(check_gopro_ip, h): h for h in hosts}
        for future in as_completed(futures):
            result = future.result()
            if result:
                found.append(result)
    return found


class GoProDiscovery:
    """Descobre câmeras GoPro varrendo dinamicamente as sub-redes locais"""

    def start_discovery(self, timeout=10, connection_type=None):
        """connection_type: None (tudo), 'usb' ou 'wifi'"""
        found_cameras = []
        subnets = get_local_subnets()
        print(f"Sub-redes locais detectadas: {subnets}")

        for network, adapter_name in subnets:
            if connection_type == "usb" and not _is_usb_adapter(adapter_name):
                continue
            if connection_type == "wifi" and not _is_wifi_adapter(adapter_name):
                continue

            print(f"Varrendo {network} ({adapter_name}) ...")
            cams = scan_subnet(network)
            for cam in cams:
                found_cameras.append(cam)
                print(f"  Câmera encontrada: {cam['ip']} - {cam.get('name', '')} ({cam.get('model', '')})")

        if not found_cameras:
            print("Nenhuma câmera respondendo. Verifique se as câmeras estão "
                  "em modo 'GoPro Connect' e desbloqueadas.")

        return found_cameras


class GoProManager:
    def __init__(self, ips=None):
        self.cameras = []
        if ips:
            for ip in ips:
                self.cameras.append(GoProCamera(ip))

    def add_camera(self, ip, name="", model="", serial=""):
        for c in self.cameras:
            if c.ip == ip:
                return c
        cam = GoProCamera(ip, name, model, serial)
        self.cameras.append(cam)
        return cam

    def discover_cameras(self, timeout=10, connection_type=None):
        discovery = GoProDiscovery()
        found = discovery.start_discovery(timeout, connection_type)
        new_cameras = []
        for cam_info in found:
            cam = self.add_camera(
                cam_info["ip"],
                cam_info.get("name", ""),
                cam_info.get("model", ""),
                cam_info.get("serial", ""),
            )
            if cam not in new_cameras:
                new_cameras.append(cam)
        return new_cameras

    def start_all_recording(self):
        results = []
        lock = threading.Lock()

        def _start(cam):
            ok, msg = cam.start_recording()
            with lock:
                results.append((cam, ok, msg))

        threads = []
        for cam in self.cameras:
            t = threading.Thread(target=_start, args=(cam,))
            threads.append(t)
            t.start()
        for t in threads:
            t.join()
        return results

    def sync_all_datetime(self):
        """Sincroniza a data/hora de todas as câmeras com o PC."""
        results = []
        lock = threading.Lock()

        def _sync(cam):
            ok, msg = cam.sync_datetime()
            with lock:
                results.append((cam, ok, msg))

        threads = []
        for cam in self.cameras:
            t = threading.Thread(target=_sync, args=(cam,))
            threads.append(t)
            t.start()
        for t in threads:
            t.join()
        return results

    def copy_settings_from(self, source_index=0):
        """Copia as configurações da câmera source_index para todas as outras."""
        if len(self.cameras) < 2:
            return []
        source = self.cameras[source_index]
        settings = source.get_settings()
        if not settings:
            return []

        targets = [c for i, c in enumerate(self.cameras) if i != source_index]
        results = []
        for target in targets:
            ok = 0
            fail = 0
            for setting_id, value in settings.items():
                if target.set_setting(setting_id, value):
                    ok += 1
                else:
                    fail += 1
            results.append((target, ok, fail))
        return results

    def stop_all_recording(self):
        results = []
        lock = threading.Lock()

        def _stop(cam):
            ok, msg = cam.stop_recording()
            with lock:
                results.append((cam, ok, msg))

        threads = []
        for cam in self.cameras:
            t = threading.Thread(target=_stop, args=(cam,))
            threads.append(t)
            t.start()
        for t in threads:
            t.join()
        return results

    def update_all_status(self):
        threads = []
        for cam in self.cameras:
            t = threading.Thread(target=cam.get_status)
            threads.append(t)
            t.start()
        for t in threads:
            t.join()

    def get_all_status(self):
        results = {}
        for cam in self.cameras:
            results[cam.ip] = {
                "battery": cam.get_battery_level(),
                "recording": cam.is_currently_recording(),
                "status": cam.status,
            }
        return results
