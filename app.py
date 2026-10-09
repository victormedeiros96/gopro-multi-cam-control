import sys
import cv2
import time
import os
import threading
from PyQt6.QtWidgets import (QApplication, QMainWindow, QVBoxLayout, QHBoxLayout,
                             QPushButton, QLabel, QWidget, QGridLayout,
                             QMessageBox, QGroupBox, QLineEdit,
                             QScrollArea, QTextEdit, QSizePolicy)
from PyQt6.QtCore import QThread, pyqtSignal, Qt, QTimer, QObject
from PyQt6.QtGui import QImage, QPixmap, QFont, QPainter, QPen, QColor
from gopro_manager import GoProManager, check_gopro_ip


class VideoStreamThread(QObject):
    change_pixmap_signal = pyqtSignal(int, QImage)

    def __init__(self, index, camera, port):
        super().__init__()
        self.index = index
        self.camera = camera
        self.port = port
        self._run_flag = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        cap = None
        try:
            self.camera.start_stream(self.port)
            time.sleep(1.0)

            os.environ.setdefault(
                "OPENCV_FFMPEG_CAPTURE_OPTIONS",
                "fflags;nobuffer|flags;low_delay|probesize;32|analyzeduration;0|rw_timeout;3000000",
            )
            stream_url = f"udp://0.0.0.0:{self.port}"
            cap = cv2.VideoCapture(stream_url, cv2.CAP_FFMPEG)

            while self._run_flag:
                ret, frame = cap.read()
                if ret:
                    h, w = frame.shape[:2]
                    scale = 640 / max(h, w)
                    frame = cv2.resize(frame, (int(w * scale), int(h * scale)))
                    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    h, w, ch = rgb.shape
                    qimg = QImage(rgb.data, w, h, ch * w, QImage.Format.Format_RGB888).copy()
                    self.change_pixmap_signal.emit(self.index, qimg)
                else:
                    time.sleep(0.5)
        except Exception as e:
            print(f"[Stream {self.index}] Erro: {e}")
        finally:
            if cap is not None:
                cap.release()
            self.camera.stop_stream()

    def stop(self):
        self._run_flag = False
        self.camera.stop_stream()
        self._thread.join(timeout=3)


class DiscoveryThread(QThread):
    finished_cameras = pyqtSignal(list)
    error_signal = pyqtSignal(str)

    def __init__(self, manager, connection_type=None):
        super().__init__()
        self.manager = manager
        self.connection_type = connection_type

    def run(self):
        try:
            cameras = self.manager.discover_cameras(timeout=10, connection_type=self.connection_type)
            self.finished_cameras.emit(cameras)
        except Exception as e:
            self.error_signal.emit(str(e))
            self.finished_cameras.emit([])


class BLEThread(QThread):
    result_signal = pyqtSignal(dict)
    error_signal = pyqtSignal(str)
    def run(self):
        try:
            from gopro_ble import scan_gopro, enable_wifi, connect_wifi
            devices = scan_gopro(timeout=8)
            if not devices:
                self.error_signal.emit(
                    "Nenhuma GoPro BLE encontrada.\n"
                    "Verifique se o Bluetooth está ligado e a câmera está próxima e ligada."
                )
                return
            dev = devices[0]
            creds = enable_wifi(dev["address"])
            ssid = creds.get("ssid", "")
            password = creds.get("password", "")

            wifi_msg = ""
            if ssid and password:
                try:
                    connect_wifi(ssid, password)
                    wifi_msg = f"\nPC conectado na rede '{ssid}' automaticamente."
                except Exception as e:
                    wifi_msg = f"\n(Não consegui conectar o PC no WiFi: {e})"

            self.result_signal.emit({
                "name": dev["name"],
                "ssid": ssid,
                "password": password,
                "wifi_msg": wifi_msg,
            })
        except Exception as e:
            self.error_signal.emit(str(e))


class SyncSettingsThread(QThread):
    finished_signal = pyqtSignal(list)

    def __init__(self, manager):
        super().__init__()
        self.manager = manager

    def run(self):
        try:
            results = self.manager.copy_settings_from(source_index=0)
            self.finished_signal.emit(results)
        except Exception as e:
            self.finished_signal.emit([])


class CameraWidget(QGroupBox):
    def __init__(self, index, camera):
        self.base_title = f"{camera.name} - {camera.model}" if camera.name else f"GoPro {camera.ip}"
        super().__init__(self.base_title)
        self.index = index
        self.camera = camera
        self.ip = camera.ip
        self.show_grid = True
        layout = QVBoxLayout(self)

        self.image_label = QLabel("Iniciando stream...")
        self.image_label.setMinimumSize(320, 240)
        self.image_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_label.setStyleSheet(
            "background-color: black; color: gray; border: 1px solid #555;"
        )

        info_layout = QHBoxLayout()
        self.record_label = QLabel(" PARADO")
        self.record_label.setFont(QFont("", 10, QFont.Weight.Bold))
        self.record_label.setStyleSheet(
            "color: white; background-color: #000000; padding: 3px 10px; border-radius: 3px;"
        )
        self.gps_label = QLabel(" GPS: --")
        self.gps_label.setFont(QFont("", 10, QFont.Weight.Bold))
        self.gps_label.setStyleSheet(
            "color: #888; background-color: #1a1a1a; padding: 3px 10px; border-radius: 3px;"
        )
        info_layout.addWidget(self.record_label)
        info_layout.addWidget(self.gps_label)
        info_layout.addStretch()

        layout.addWidget(self.image_label, 1)
        layout.addLayout(info_layout)

    def update_preview(self, qimage):
        pixmap = QPixmap.fromImage(qimage)
        scaled = pixmap.scaled(
            self.image_label.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        if self.show_grid and not scaled.isNull():
            painter = QPainter(scaled)
            pen = QPen(QColor(255, 255, 255, 160), 1)
            pen.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(pen)
            w = scaled.width()
            h = scaled.height()
            painter.drawLine(w // 3, 0, w // 3, h)
            painter.drawLine(2 * w // 3, 0, 2 * w // 3, h)
            painter.drawLine(0, h // 3, w, h // 3)
            painter.drawLine(0, 2 * h // 3, w, 2 * h // 3)
            painter.end()
        self.image_label.setPixmap(scaled)

    def update_status(self, battery, recording, gps_lock=False):
        self.setTitle(f"{self.base_title} - {battery}%")
        if recording:
            self.record_label.setText(" GRAVANDO")
            self.record_label.setStyleSheet(
                "color: white; background-color: #27ae60; padding: 3px 10px; border-radius: 3px;"
            )
        else:
            self.record_label.setText(" PARADO")
            self.record_label.setStyleSheet(
                "color: white; background-color: #000000; padding: 3px 10px; border-radius: 3px;"
            )
        if gps_lock:
            self.gps_label.setText(" GPS: travado")
            self.gps_label.setStyleSheet(
                "color: white; background-color: #27ae60; padding: 3px 10px; border-radius: 3px;"
            )
        else:
            self.gps_label.setText(" GPS: sem sinal")
            self.gps_label.setStyleSheet(
                "color: #ccc; background-color: #5a4a00; padding: 3px 10px; border-radius: 3px;"
            )


class GoProControlApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.manager = GoProManager()
        self.camera_widgets = []
        self.threads = []
        self.setWindowTitle("GoPro Multi-Cam Control")
        self.resize(1100, 650)

        main = QWidget()
        self.setCentralWidget(main)
        layout = QVBoxLayout(main)
        layout.setSpacing(8)

        top_row = QHBoxLayout()
        self.btn_discover_usb = QPushButton("Localizar por Cabo (USB)")
        self.btn_discover_usb.clicked.connect(lambda: self.discover_cameras("usb"))
        self.btn_discover_usb.setMinimumHeight(36)

        self.btn_discover_wifi = QPushButton("Localizar por WiFi")
        self.btn_discover_wifi.clicked.connect(lambda: self.discover_cameras("wifi"))
        self.btn_discover_wifi.setMinimumHeight(36)

        self.btn_ble_wifi = QPushButton("Ativar WiFi (Bluetooth)")
        self.btn_ble_wifi.clicked.connect(self.enable_wifi_via_ble)
        self.btn_ble_wifi.setMinimumHeight(36)

        self.ip_input = QLineEdit()
        self.ip_input.setPlaceholderText("IP manual (ex: 172.26.107.51)")
        self.ip_input.setFixedWidth(180)
        self.btn_add_ip = QPushButton("Adicionar IP")
        self.btn_add_ip.clicked.connect(self.add_camera_by_ip)

        top_row.addWidget(self.btn_discover_usb)
        top_row.addWidget(self.btn_discover_wifi)
        top_row.addWidget(self.btn_ble_wifi)
        top_row.addWidget(self.ip_input)
        top_row.addWidget(self.btn_add_ip)
        layout.addLayout(top_row)

        ctrl = QHBoxLayout()
        self.btn_record = QPushButton("  Gravar Todas")
        self.btn_record.setMinimumHeight(40)
        self.btn_record.setStyleSheet(
            "QPushButton { background-color: #c0392b; color: white; font-size: 14px; font-weight: bold; }"
            "QPushButton:hover { background-color: #e74c3c; }"
            "QPushButton:disabled { background-color: #555; color: #888; }"
        )
        self.btn_record.clicked.connect(self.on_record_clicked)
        self.btn_record.setEnabled(False)

        self.btn_stop = QPushButton("  Parar Todas")
        self.btn_stop.setMinimumHeight(40)
        self.btn_stop.setStyleSheet(
            "QPushButton { background-color: #555; color: white; font-size: 14px; }"
            "QPushButton:hover { background-color: #777; }"
            "QPushButton:disabled { background-color: #333; color: #666; }"
        )
        self.btn_stop.clicked.connect(self.on_stop_clicked)
        self.btn_stop.setEnabled(False)

        self.btn_sync_settings = QPushButton("  Sincronizar Configurações")
        self.btn_sync_settings.setMinimumHeight(40)
        self.btn_sync_settings.setStyleSheet(
            "QPushButton { background-color: #2c3e50; color: white; font-size: 14px; }"
            "QPushButton:hover { background-color: #34495e; }"
            "QPushButton:disabled { background-color: #333; color: #666; }"
        )
        self.btn_sync_settings.clicked.connect(self.on_sync_settings_clicked)
        self.btn_sync_settings.setEnabled(False)

        self.btn_grid = QPushButton("Grade 3x3: ON")
        self.btn_grid.setMinimumHeight(40)
        self.btn_grid.setCheckable(True)
        self.btn_grid.setChecked(True)
        self.btn_grid.setStyleSheet(
            "QPushButton { background-color: #1a6b3c; color: white; font-size: 13px; }"
            "QPushButton:checked { background-color: #27ae60; color: white; }"
            "QPushButton:hover { background-color: #2ecc71; }"
        )
        self.btn_grid.clicked.connect(self.on_toggle_grid)

        ctrl.addWidget(self.btn_record)
        ctrl.addWidget(self.btn_stop)
        ctrl.addWidget(self.btn_sync_settings)
        ctrl.addWidget(self.btn_grid)
        layout.addLayout(ctrl)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        grid_container = QWidget()
        self.grid = QGridLayout(grid_container)
        self.grid.setSpacing(10)
        scroll.setWidget(grid_container)
        layout.addWidget(scroll, 1)

        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(100)
        self.log.setPlaceholderText("Log de descoberta aparecerá aqui...")
        self.log.setStyleSheet("background-color: #1a1a1a; color: #aaa; font-size: 11px;")
        layout.addWidget(self.log)

        self.status_timer = QTimer()
        self.status_timer.timeout.connect(self.update_status_labels)

    def _log(self, msg):
        self.log.append(msg)

    def enable_wifi_via_ble(self):
        self.btn_ble_wifi.setEnabled(False)
        self.btn_ble_wifi.setText("Ativando WiFi...")
        self._log("--- Procurando GoPro via Bluetooth ---")
        self.ble_thread = BLEThread()
        self.ble_thread.result_signal.connect(self._on_ble_result)
        self.ble_thread.error_signal.connect(self._on_ble_error)
        self.ble_thread.start()

    def _on_ble_result(self, info):
        self.btn_ble_wifi.setEnabled(True)
        self.btn_ble_wifi.setText("Ativar WiFi (Bluetooth)")
        ssid = info.get("ssid", "")
        pw = info.get("password", "")
        wifi_msg = info.get("wifi_msg", "")
        self._log(f"✓ WiFi ligado: SSID={ssid} | SENHA={pw}")
        QMessageBox.information(
            self, "WiFi ativado",
            f"WiFi da câmera ativado!\n\nSSID: {ssid}\nSenha: {pw}\n{wifi_msg}\n\n"
            "Agora clique em 'Localizar por WiFi' para achar a câmera."
        )

    def _on_ble_error(self, msg):
        self.btn_ble_wifi.setEnabled(True)
        self.btn_ble_wifi.setText("Ativar WiFi (Bluetooth)")
        self._log(f"ERRO BLE: {msg}")
        QMessageBox.warning(self, "Erro Bluetooth", msg)

    def on_record_clicked(self):
        self._log("[sync] Sincronizando data/hora das câmeras...")
        sync_results = self.manager.sync_all_datetime()
        sync_failures = [(cam, msg) for cam, ok, msg in sync_results if not ok]
        if sync_failures:
            lines = [f"{cam.name}: {msg}" for cam, msg in sync_failures]
            self._log(f"[sync] Falhou: {' | '.join(lines)}")
        else:
            self._log(f"[sync] Data/hora sincronizada em {len(sync_results)} câmera(s)")

        results = self.manager.start_all_recording()
        self._show_record_results("gravar", results)

    def on_stop_clicked(self):
        results = self.manager.stop_all_recording()
        self._show_record_results("parar", results)

    def on_toggle_grid(self, checked):
        for w in self.camera_widgets:
            w.show_grid = checked
        self.btn_grid.setText("Grade 3x3: ON" if checked else "Grade 3x3: OFF")

    def on_sync_settings_clicked(self):
        self.btn_sync_settings.setEnabled(False)
        self.btn_sync_settings.setText("  Sincronizando...")
        self._log("[sync] Copiando configurações da câmera 1 para as demais...")
        self.sync_thread = SyncSettingsThread(self.manager)
        self.sync_thread.finished_signal.connect(self._on_sync_settings_finished)
        self.sync_thread.start()

    def _on_sync_settings_finished(self, results):
        self.btn_sync_settings.setEnabled(True)
        self.btn_sync_settings.setText("  Sincronizar Configurações")
        if not results:
            self._log("[sync] Nenhuma configuração copiada (verifique se há 2+ câmeras).")
            return
        for target, ok, fail in results:
            self._log(f"[sync] {target.name}: {ok} configurações OK, {fail} falharam")
        QMessageBox.information(
            self, "Configurações sincronizadas",
            "Configurações copiadas da câmera 1 para as demais.\n\n"
            + "\n".join(f"{t.name}: {ok} OK, {fail} falhas" for t, ok, fail in results)
        )

    def _show_record_results(self, action, results):
        failures = [(cam, msg) for cam, ok, msg in results if not ok]
        if failures:
            lines = [f"{cam.name}: {msg}" for cam, msg in failures]
            self._log(f"[{action}] Falhou: {' | '.join(lines)}")
            QMessageBox.warning(self, f"Falha ao {action}", "\n".join(lines))
        else:
            self._log(f"[{action}] OK em {len(results)} câmera(s)")

    def add_camera_by_ip(self):
        ip = self.ip_input.text().strip()
        if not ip:
            return
        result = check_gopro_ip(ip)
        if result:
            camera = self.manager.add_camera(
                ip,
                result.get("name", ""),
                result.get("model", ""),
                result.get("serial", ""),
            )
            self._add_camera_widget(camera)
            self._log(f"✓ Câmera adicionada: {ip} - {camera.name} ({camera.model})")
            self.ip_input.clear()
            self._enable_controls()
        else:
            QMessageBox.warning(self, "Erro", f"Nenhuma GoPro respondendo em {ip}")

    def _add_camera_widget(self, camera):
        for w in self.camera_widgets:
            if w.ip == camera.ip:
                return

        idx = len(self.camera_widgets)
        w = CameraWidget(idx, camera)
        row = idx // 3
        col = idx % 3
        self.grid.addWidget(w, row, col)
        self.camera_widgets.append(w)

        port = 8554 + idx
        thread = VideoStreamThread(idx, camera, port)
        thread.change_pixmap_signal.connect(self.update_video)
        self.threads.append(thread)

        self._rebalance_grid()

    def _rebalance_grid(self):
        n = len(self.camera_widgets)
        for i in range(3):
            self.grid.setColumnStretch(i, 1 if i < n else 0)

    def _enable_controls(self):
        has = len(self.manager.cameras) > 0
        self.btn_record.setEnabled(has)
        self.btn_stop.setEnabled(has)
        self.btn_sync_settings.setEnabled(len(self.manager.cameras) >= 2)
        if has and not self.status_timer.isActive():
            self.status_timer.start(2000)

    def discover_cameras(self, connection_type=None):
        self._active_discover = connection_type
        btn = self.btn_discover_usb if connection_type == "usb" else self.btn_discover_wifi
        btn.setEnabled(False)
        tipo = "USB (cabo)" if connection_type == "usb" else "WiFi"
        btn.setText(f"Procurando por {tipo}...")
        self._log(f"--- Procurando por {tipo} ---")

        self.discovery_thread = DiscoveryThread(self.manager, connection_type)
        self.discovery_thread.finished_cameras.connect(self._on_discovery_finished)
        self.discovery_thread.error_signal.connect(
            lambda e: self._log(f"ERRO: {e}")
        )
        self.discovery_thread.start()

    def _on_discovery_finished(self, cameras):
        self.btn_discover_usb.setEnabled(True)
        self.btn_discover_wifi.setEnabled(True)
        self.btn_discover_usb.setText("Localizar por Cabo (USB)")
        self.btn_discover_wifi.setText("Localizar por WiFi")

        if not cameras:
            self._log("Nenhuma GoPro encontrada.")
            QMessageBox.information(
                self, "Nenhuma câmera",
                "Nenhuma GoPro encontrada.\n\n"
                "Cabo (USB): modo 'GoPro Connect' + cabo com dados.\n"
                "WiFi: conecte o PC no hotspot da câmera.\n\n"
                "Ou adicione o IP manualmente."
            )
            return

        for camera in cameras:
            self._add_camera_widget(camera)
            self._log(f"✓ {camera.ip} - {camera.name} ({camera.model})")

        self._log(f"{len(cameras)} câmera(s) encontrada(s)")
        self._enable_controls()

    def update_video(self, index, image):
        if index < len(self.camera_widgets):
            self.camera_widgets[index].update_preview(image)

    def update_status_labels(self):
        if not self.manager.cameras:
            return
        self.manager.update_all_status()
        statuses = self.manager.get_all_status()
        for i, cam in enumerate(self.manager.cameras):
            if i < len(self.camera_widgets):
                s = statuses.get(cam.ip, {})
                self.camera_widgets[i].update_status(
                    s.get("battery", 0),
                    s.get("recording", False),
                    s.get("gps_lock", False),
                )

    def closeEvent(self, event):
        self.status_timer.stop()
        for t in self.threads:
            t.stop()
        event.accept()

if __name__ == "__main__":
    def handle_exception(exc_type, exc_value, exc_tb):
        import traceback
        msg = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        print(msg)
        try:
            QMessageBox.critical(None, "Erro inesperado", msg[-2000:])
        except Exception:
            pass

    sys.excepthook = handle_exception

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet("""
        QMainWindow { background-color: #2b2b2b; }
        QWidget { color: #ccc; }
        QPushButton { background-color: #3a3a3a; color: #ccc; border: 1px solid #555; padding: 6px 12px; border-radius: 4px; }
        QPushButton:hover { background-color: #4a4a4a; }
        QPushButton:disabled { background-color: #222; color: #555; }
        QLineEdit { background-color: #1a1a1a; color: #ccc; border: 1px solid #555; padding: 4px; border-radius: 3px; }
        QGroupBox { color: #ddd; border: 1px solid #555; margin-top: 10px; padding-top: 16px; font-weight: bold; }
        QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }
        QProgressBar { background-color: #1a1a1a; border: 1px solid #555; text-align: center; }
        QProgressBar::chunk { background-color: #27ae60; }
        QScrollArea { border: none; }
    """)
    window = GoProControlApp()
    window.show()
    sys.exit(app.exec())
