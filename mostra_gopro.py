#!/usr/bin/env python3
"""
mostra_gopro.py - Exibe o vídeo em tempo real da câmera GoPro na tela.
Suporta modos de alta resolução (Webcam 1080p Full HD) e Preview (720p/480p).

Atalhos no teclado:
  [Q] ou [ESC] : Sair e parar a transmissão na câmera
  [S]          : Tirar foto / Salvar frame atual em alta qualidade
  [N]          : Alternar filtro de nitidez adaptativo (melhora detalhes)
  [M]          : Alternar entre modo WEBCAM (1080p) e PREVIEW (720p)
  [F]          : Alternar tela cheia (Fullscreen)
  [H]          : Ocultar / Mostrar painel de informações (HUD)
  [R]          : Reiniciar stream / reconectar
"""

import argparse
import atexit
from datetime import datetime
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

# Suprime logs de baixo nível do FFmpeg que disparam antes do primeiro keyframe
os.environ.setdefault("OPENCV_FFMPEG_LOGLEVEL", "-8")

import cv2
import requests

DEFAULT_IP = "10.5.5.9"
DEFAULT_PORT = 8080
DEFAULT_STREAM_PORT = 8554
RETRY_STATUS = {502, 503, 504}


class GoProKeepAlive:
    """Envia sinais periódicos (UDP e HTTP) para manter a GoPro transmitindo sem desligar."""

    def __init__(self, camera_ip: str, base_url: str, stream_port: int = DEFAULT_STREAM_PORT, interval: float = 2.5):
        self.camera_ip = camera_ip
        self.base_url = base_url
        self.stream_port = stream_port
        self.interval = interval
        self.running = False
        self.thread = None
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def start(self):
        self.running = True
        self.thread = threading.Thread(target=self._run, daemon=True, name="GoProKeepAlive")
        self.thread.start()

    def _run(self):
        udp_payloads = [
            b"_GPHD_:1:0:2:0.000000\n",
            b"_GPHD_:0:0:2:0.000000\n",
        ]
        while self.running:
            # 1. Envia UDP keep-alive para a GoPro
            for payload in udp_payloads:
                try:
                    self._sock.sendto(payload, (self.camera_ip, self.stream_port))
                except Exception:
                    pass

            # 2. Envia GET keep_alive HTTP se suportado pela câmera
            try:
                requests.get(f"{self.base_url}/gopro/camera/keep_alive", timeout=1.5)
            except Exception:
                pass

            time.sleep(self.interval)

    def stop(self):
        self.running = False
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=1.0)
        try:
            self._sock.close()
        except Exception:
            pass


class RealtimeCapture:
    """Captura de vídeo em tempo real com thread dedicada para evitar acúmulo de buffer (latência zero)."""

    def __init__(self, src: str):
        self.src = src
        self.cap = None
        self.running = False
        self.thread = None
        self.lock = threading.Lock()
        self.latest_frame = None
        self.fps = 0.0
        self.frame_size = (0, 0)
        self.last_frame_time = 0.0

    def start(self, timeout: float = 12.0) -> bool:
        self.cap = cv2.VideoCapture(self.src, cv2.CAP_FFMPEG)
        if not self.cap.isOpened():
            return False

        # Reduz buffer para latência mínima
        try:
            self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        except Exception:
            pass

        self.running = True
        self.thread = threading.Thread(target=self._reader, daemon=True, name="RealtimeCapture")
        self.thread.start()

        # Aguarda chegada do primeiro frame decodificado
        start_wait = time.time()
        while time.time() - start_wait < timeout:
            with self.lock:
                if self.latest_frame is not None:
                    return True
            time.sleep(0.05)

        return False

    def _reader(self):
        fps_calc_time = time.time()
        fps_counter = 0

        while self.running:
            if self.cap is None or not self.cap.isOpened():
                break

            # grab() descarta imediatamente frames defasados da fila do FFmpeg
            ret = self.cap.grab()
            if not ret:
                time.sleep(0.005)
                continue

            ret, frame = self.cap.retrieve()
            if ret and frame is not None:
                now = time.time()
                fps_counter += 1
                if now - fps_calc_time >= 1.0:
                    self.fps = fps_counter / (now - fps_calc_time)
                    fps_counter = 0
                    fps_calc_time = now

                with self.lock:
                    self.latest_frame = frame
                    self.frame_size = (frame.shape[1], frame.shape[0])
                    self.last_frame_time = now
            else:
                time.sleep(0.005)

    def read(self):
        with self.lock:
            if self.latest_frame is not None:
                return True, self.latest_frame.copy()
            return False, None

    def is_receiving(self, max_delay: float = 3.5) -> bool:
        if not self.running:
            return False
        with self.lock:
            if self.last_frame_time == 0:
                return True
            return (time.time() - self.last_frame_time) < max_delay

    def stop(self):
        self.running = False
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=1.0)
        if self.cap:
            self.cap.release()
            self.cap = None


def test_camera_connection(base_url: str, timeout: float = 2.5) -> bool:
    """Verifica se a GoPro responde na porta HTTP."""
    for path in ["/gopro/camera/state", "/gp/gpControl/status"]:
        try:
            r = requests.get(f"{base_url}{path}", timeout=timeout)
            if r.status_code in {200, 403, 404, 500}:
                return True
        except Exception:
            pass
    return False


def start_gopro_stream(
    base_url: str,
    mode: str = "auto",
    res: int = 1080,
    fov: str = "wide",
    stream_port: int = DEFAULT_STREAM_PORT,
) -> tuple[bool, str]:
    """Inicia a transmissão na GoPro com a melhor resolução disponível.

    Retorna: (sucesso: bool, modo_ativado: str ['webcam', 'preview'])
    """
    res_code = 12 if res >= 1080 else (7 if res >= 720 else 4)
    fov_map = {"wide": 0, "linear": 4, "narrow": 2, "superview": 3}
    fov_code = fov_map.get(fov.lower(), 0)

    # 1. Modo Webcam (1080p Full HD)
    if mode in ("auto", "webcam"):
        webcam_urls = [
            f"{base_url}/gopro/webcam/start?res={res_code}&fov={fov_code}&port={stream_port}",
            f"{base_url}/gopro/webcam/start?res={res_code}",
            f"{base_url}/gopro/webcam/start",
        ]
        print(f"[+] Tentando modo WEBCAM de alta qualidade ({res}p)...")
        for url in webcam_urls:
            try:
                r = requests.get(url, timeout=5)
                if r.status_code in {200, 409}:
                    print(f"[✓] Modo WEBCAM ({res}p Full HD) ativado com sucesso!")
                    return True, "webcam"
            except Exception:
                pass

        if mode == "webcam":
            raise RuntimeError(
                "Não foi possível ativar o modo Webcam.\n"
                "Para 1080p Webcam, a GoPro geralmente requer conexão via cabo USB-C "
                "no modo 'GoPro Connect' (Preferências -> Conexões -> Conexão USB)."
            )
        print("[!] Modo Webcam não aceito nesta conexão (comum no Wi-Fi). Alternando para modo PREVIEW...")

    # 2. Modo Preview (720p/480p)
    preview_urls = [
        f"{base_url}/gopro/camera/stream/start",
        f"{base_url}/gp/gpControl/execute?p1=gpStreamStart&a1=yes",
    ]
    print("[+] Iniciando modo PREVIEW da câmera...")
    last_err = None
    for url in preview_urls:
        try:
            r = requests.get(url, timeout=6)
            if r.status_code in {200, 409}:
                print("[✓] Modo PREVIEW ativo.")
                return True, "preview"
            r.raise_for_status()
            return True, "preview"
        except requests.HTTPError as e:
            if getattr(e.response, "status_code", None) == 409:
                return True, "preview"
            last_err = e
        except Exception as e:
            last_err = e

    if last_err:
        raise last_err
    return False, "failed"


def stop_gopro_stream(base_url: str):
    """Envia comandos para parar tanto o preview quanto o modo webcam."""
    urls = [
        f"{base_url}/gopro/webcam/stop",
        f"{base_url}/gopro/camera/stream/stop",
        f"{base_url}/gp/gpControl/execute?p1=gpStreamStop",
    ]
    for url in urls:
        try:
            requests.get(url, timeout=2.0)
        except Exception:
            pass


def apply_sharpen(img, strength: float = 0.75):
    """Aplica filtro de nitidez adaptativo (unsharp mask) para realçar detalhes e amenizar compressão."""
    blurred = cv2.GaussianBlur(img, (0, 0), 1.8)
    return cv2.addWeighted(img, 1.0 + strength, blurred, -strength, 0)


def draw_hud(
    frame,
    fps: float,
    size: tuple,
    mode_name: str = "PREVIEW",
    is_sharp: bool = False,
    snapshot_msg: str = "",
):
    """Desenha painel informativo translúcido com informações de resolução e atalhos."""
    h, w = frame.shape[:2]
    overlay = frame.copy()

    # Barra translúcida superior
    bar_height = 42
    cv2.rectangle(overlay, (0, 0), (w, bar_height), (20, 20, 20), -1)
    cv2.addWeighted(overlay, 0.70, frame, 0.30, 0, frame)

    # Texto de status e resolução real
    sharp_tag = "LIGADA" if is_sharp else "DESLIGADA"
    mode_color = (0, 255, 200) if mode_name.lower() == "webcam" else (0, 220, 100)
    text_info = f"GOPRO [{mode_name.upper()}] | {size[0]}x{size[1]} @ {fps:.1f} FPS | Nitidez: {sharp_tag}"
    cv2.putText(frame, text_info, (14, 27), cv2.FONT_HERSHEY_SIMPLEX, 0.55, mode_color, 2, cv2.LINE_AA)

    # Dicas de atalho
    help_text = "[Q] Sair  [S] Foto  [N] Nitidez  [M] Modo  [F] Tela Cheia  [H] Info"
    text_size = cv2.getTextSize(help_text, cv2.FONT_HERSHEY_SIMPLEX, 0.44, 1)[0]
    help_x = max(w - text_size[0] - 14, 14)
    cv2.putText(frame, help_text, (help_x, 26), cv2.FONT_HERSHEY_SIMPLEX, 0.44, (230, 230, 230), 1, cv2.LINE_AA)

    # Mensagem de snapshot temporária
    if snapshot_msg:
        snap_box_w = 480
        snap_box_h = 40
        box_x = (w - snap_box_w) // 2
        box_y = h - snap_box_h - 20
        cv2.rectangle(overlay, (box_x, box_y), (box_x + snap_box_w, box_y + snap_box_h), (0, 100, 0), -1)
        cv2.addWeighted(overlay, 0.80, frame, 0.20, 0, frame)
        cv2.putText(
            frame,
            snapshot_msg,
            (box_x + 15, box_y + 26),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            (255, 255, 255),
            2,
            cv2.LINE_AA,
        )


def run_cv2_player(
    stream_url: str,
    base_url: str,
    snap_dir: Path,
    mode_name: str = "preview",
    fullscreen: bool = False,
    show_hud: bool = True,
    sharpen: bool = False,
    res: int = 1080,
    fov: str = "wide",
    stream_port: int = DEFAULT_STREAM_PORT,
):
    """Executa a interface gráfica via OpenCV com suporte a nitidez e alternância de modos."""
    window_name = "GoPro - Visualizacao em Tempo Real"
    cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)

    if fullscreen:
        cv2.setWindowProperty(window_name, cv2.WINDOW_AUTOSIZE, cv2.WINDOW_NORMAL)
        cv2.setWindowProperty(window_name, cv2.WINDOW_FULLSCREEN, cv2.WINDOW_FULLSCREEN)
    else:
        cv2.resizeWindow(window_name, 1280, 720)

    print(f"[+] Conectando ao feed de vídeo em {stream_url}...")
    cap = RealtimeCapture(stream_url)
    started = cap.start(timeout=14.0)

    if not started:
        cap.stop()
        cv2.destroyAllWindows()
        raise RuntimeError("Não foi possível receber pacotes de vídeo da GoPro (tempo limite esgotado).")

    print("[✓] Feed de vídeo conectado com sucesso!")
    print("    [Q] Sair | [S] Foto | [N] Nitidez | [M] Alternar Modo | [F] Tela Cheia | [H] Info")

    is_fs = fullscreen
    is_sharp = sharpen
    current_mode = mode_name
    snap_msg = ""
    snap_msg_time = 0.0

    try:
        while True:
            ret, frame = cap.read()
            if not ret or frame is None:
                # Checa se o sinal caiu
                if not cap.is_receiving(max_delay=4.0):
                    print("[!] Sinal pausado. Tentando reconectar...")
                    cap.stop()
                    time.sleep(0.5)
                    cap = RealtimeCapture(stream_url)
                    if not cap.start(timeout=6.0):
                        print("[!] Falha ao reconectar. Aguardando sinal...")
                time.sleep(0.01)
                continue

            # Limpa mensagem de snapshot após 2.5s
            if snap_msg and (time.time() - snap_msg_time > 2.5):
                snap_msg = ""

            # Aplica filtro de nitidez se ativado
            display_frame = frame
            if is_sharp:
                display_frame = apply_sharpen(frame, strength=0.75)
            else:
                display_frame = frame.copy()

            if show_hud:
                draw_hud(
                    display_frame,
                    cap.fps,
                    cap.frame_size,
                    mode_name=current_mode,
                    is_sharp=is_sharp,
                    snapshot_msg=snap_msg,
                )

            cv2.imshow(window_name, display_frame)

            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), ord("Q"), 27):  # 'q' ou ESC
                print("[+] Encerrando visualização...")
                break
            elif key in (ord("s"), ord("S")):  # Salvar frame
                snap_dir.mkdir(parents=True, exist_ok=True)
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = snap_dir / f"gopro_snap_{timestamp}.png"
                cv2.imwrite(str(filename), frame, [cv2.IMWRITE_PNG_COMPRESSION, 1])
                snap_msg = f"Foto salva: {filename.name}"
                snap_msg_time = time.time()
                print(f"[✓] Foto salva em: {filename.resolve()}")
            elif key in (ord("n"), ord("N")):  # Alternar nitidez
                is_sharp = not is_sharp
                status_str = "LIGADA" if is_sharp else "DESLIGADA"
                print(f"[+] Nitidez adaptativa: {status_str}")
            elif key in (ord("m"), ord("M")):  # Alternar modo Webcam / Preview
                new_mode = "preview" if current_mode.lower() == "webcam" else "webcam"
                print(f"[+] Alternando modo da GoPro para '{new_mode.upper()}'...")
                cap.stop()
                stop_gopro_stream(base_url)
                time.sleep(0.8)
                try:
                    _, active_mode = start_gopro_stream(
                        base_url,
                        mode=new_mode,
                        res=res,
                        fov=fov,
                        stream_port=stream_port,
                    )
                    current_mode = active_mode
                except Exception as e:
                    print(f"[!] Falha ao mudar para {new_mode}: {e}. Retornando ao modo anterior...")
                    _, current_mode = start_gopro_stream(
                        base_url,
                        mode="preview",
                        res=res,
                        fov=fov,
                        stream_port=stream_port,
                    )
                cap = RealtimeCapture(stream_url)
                cap.start(timeout=10.0)
            elif key in (ord("f"), ord("F")):  # Alternar tela cheia
                is_fs = not is_fs
                if is_fs:
                    cv2.setWindowProperty(window_name, cv2.WINDOW_FULLSCREEN, cv2.WINDOW_FULLSCREEN)
                else:
                    cv2.setWindowProperty(window_name, cv2.WINDOW_FULLSCREEN, cv2.WINDOW_NORMAL)
                    cv2.resizeWindow(window_name, 1280, 720)
            elif key in (ord("h"), ord("H")):  # Alternar HUD
                show_hud = not show_hud
            elif key in (ord("r"), ord("R")):  # Reiniciar stream
                print("[+] Reiniciando stream...")
                cap.stop()
                start_gopro_stream(
                    base_url,
                    mode=current_mode,
                    res=res,
                    fov=fov,
                    stream_port=stream_port,
                )
                cap = RealtimeCapture(stream_url)
                cap.start(timeout=8.0)

            # Detecta se a janela foi fechada pelo botão 'X' da janela
            if cv2.getWindowProperty(window_name, cv2.WND_PROP_VISIBLE) < 1:
                break

    finally:
        cap.stop()
        cv2.destroyAllWindows()


def run_ffplay_player(stream_url: str):
    """Executa o feed de vídeo via ffplay nativo."""
    cmd = [
        "ffplay",
        "-fflags",
        "nobuffer",
        "-flags",
        "low_delay",
        "-framedrop",
        "-strict",
        "experimental",
        "-window_title",
        "GoPro Live - FFplay",
        "-i",
        stream_url,
    ]
    print(f"[+] Iniciando FFplay: {' '.join(cmd)}")
    proc = subprocess.Popen(cmd)
    try:
        proc.wait()
    except KeyboardInterrupt:
        proc.terminate()
        proc.wait()


def main():
    parser = argparse.ArgumentParser(
        description="Exibe a imagem da câmera GoPro em tempo real na tela com opções de alta qualidade.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--ip", type=str, default=DEFAULT_IP, help="Endereço IP da GoPro")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Porta HTTP da API da GoPro")
    parser.add_argument("--stream-port", type=int, default=DEFAULT_STREAM_PORT, help="Porta UDP do stream de vídeo")
    parser.add_argument(
        "--mode",
        choices=["auto", "webcam", "preview"],
        default="auto",
        help="Modo: 'auto' (tenta Webcam 1080p, com fallback para Preview), 'webcam' (força 1080p) ou 'preview' (720p)",
    )
    parser.add_argument(
        "--res",
        type=int,
        choices=[1080, 720, 480],
        default=1080,
        help="Resolução desejada (1080 para Full HD em modo webcam)",
    )
    parser.add_argument(
        "--fov",
        choices=["wide", "linear", "narrow", "superview"],
        default="wide",
        help="Campo de visão da lente",
    )
    parser.add_argument(
        "--sharpen",
        action="store_true",
        help="Inicia com filtro de nitidez ativado (também pode ser alternado com tecla 'N')",
    )
    parser.add_argument(
        "--player",
        choices=["cv2", "ffplay"],
        default="cv2",
        help="Mecanismo de exibição: 'cv2' (interface com atalhos e nitidez) ou 'ffplay'",
    )
    parser.add_argument("--fullscreen", action="store_true", help="Inicia a janela em tela cheia")
    parser.add_argument("--no-hud", action="store_true", help="Inicia sem a barra de status sobre o vídeo")
    parser.add_argument(
        "--snap-dir",
        type=str,
        default="gopro_snapshots",
        help="Pasta onde serão salvas as fotos tiradas com a tecla 'S'",
    )
    parser.add_argument(
        "--no-check",
        action="store_true",
        help="Ignora o teste de conectividade inicial e tenta iniciar o stream diretamente",
    )
    args = parser.parse_args()

    base_url = f"http://{args.ip}:{args.port}"
    stream_url = f"udp://@:{args.stream_port}"

    print("=" * 65)
    print("           GOPRO - MONITORAMENTO EM TEMPO REAL           ")
    print("=" * 65)
    print(f"[*] Câmera:     {base_url}")
    print(f"[*] Stream:     {stream_url}")
    print(f"[*] Modo:       {args.mode.upper()} ({args.res}p)")
    print(f"[*] Lente:      {args.fov.upper()}")
    print(f"[*] Exibição:   {args.player.upper()}")
    print("=" * 65)

    # 1. Verifica conectividade com a câmera
    if not args.no_check:
        print(f"[+] Verificando conexão com {base_url}...")
        connected = False
        for attempt in range(1, 4):
            if test_camera_connection(base_url, timeout=2.0):
                connected = True
                break
            print(f"[!] Aguardando resposta da câmera (tentativa {attempt}/3)...")
            time.sleep(1.0)

        if not connected:
            print("\n[ERRO] Não foi possível comunicar com a GoPro.")
            print("Verifique se:")
            print("  1. O computador está conectado à rede Wi-Fi da GoPro (ou via cabo USB no modo GoPro Connect).")
            print("  2. A câmera está ligada.")
            print(f"  3. O IP configurado ({args.ip}) está correto.")
            print("\nSe você tem certeza que a câmera está conectada, use a opção '--no-check'.")
            sys.exit(1)

        print("[✓] GoPro detectada e respondendo!")

    # 2. Inicia o stream na câmera
    print("[+] Enviando comando para iniciar transmissão de vídeo...")
    active_mode = "preview"
    try:
        _, active_mode = start_gopro_stream(
            base_url,
            mode=args.mode,
            res=args.res,
            fov=args.fov,
            stream_port=args.stream_port,
        )
    except Exception as e:
        print(f"[ERRO ao iniciar stream] {e}", file=sys.stderr)
        sys.exit(2)

    # Garante que ao sair (Ctrl+C, erro ou fechamento), o stream seja parado na câmera
    def cleanup():
        print("\n[+] Finalizando transmissão na GoPro...")
        stop_gopro_stream(base_url)
        print("[✓] Conexão encerrada.")

    atexit.register(cleanup)

    # 3. Inicia thread de keepalive
    keepalive = GoProKeepAlive(camera_ip=args.ip, base_url=base_url, stream_port=args.stream_port)
    keepalive.start()

    # 4. Inicia exibição na tela
    try:
        if args.player == "ffplay":
            run_ffplay_player(stream_url)
        else:
            snap_dir = Path(args.snap_dir)
            run_cv2_player(
                stream_url=stream_url,
                base_url=base_url,
                snap_dir=snap_dir,
                mode_name=active_mode,
                fullscreen=args.fullscreen,
                show_hud=not args.no_hud,
                sharpen=args.sharpen,
                res=args.res,
                fov=args.fov,
                stream_port=args.stream_port,
            )
    except KeyboardInterrupt:
        print("\n[!] Interrompido pelo usuário.")
    except Exception as e:
        print(f"\n[ERRO na exibição] {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        keepalive.stop()


if __name__ == "__main__":
    main()
