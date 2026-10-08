# GoPro Multi-Cam Control

Aplicativo desktop (Python + PyQt6) para controlar **múltiplas câmeras GoPro** (Hero 13) simultaneamente — disparo de gravação, monitoramento de bateria/status e preview em tempo real com baixa latência.

## Recursos

- **Descoberta automática** de câmeras por **USB (cabo)** e **WiFi**, separadamente
- **Ativação de WiFi via Bluetooth (BLE)** — liga o WiFi AP da câmera e conecta o PC automaticamente
- **Preview em tempo real** com threads dedicadas (baixa latência) e **grade 3x3** para calibração
- **Gravar/Parar todas** as câmeras simultaneamente
- **Sincronização de data/hora** das câmeras antes de gravar
- **Sincronização de configurações** (copia da câmera master para as demais)
- Monitoramento de **bateria** e estado de gravação em tempo real
- Extração de **timecode** dos vídeos (`extract_timecodes.py`) para sincronizar quadros no pós-processamento

## Requisitos

- Windows 10/11
- Python 3.11+
- Câmeras GoPro Hero 10/11/12/13 (testado com Hero 13)
- Bluetooth no PC (para ativar WiFi via BLE)

## Instalação

```bash
git clone https://github.com/SEU_USUARIO/gopro-multi-cam-control.git
cd gopro-multi-cam-control
pip install -r requirements.txt
```

## Uso

```bash
python app.py
```

1. **Localizar por Cabo (USB)** — acha câmeras conectadas via USB (modo GoPro Connect)
2. **Localizar por WiFi** — acha câmeras na rede WiFi
3. **Ativar WiFi (Bluetooth)** — liga o WiFi AP da câmera e conecta o PC automaticamente
4. **Gravar Todas** / **Parar Todas** — disparo simultâneo
5. **Sincronizar Configurações** — copia configurações da câmera 1 para as demais

## GoPro Labs (opcional, recomendado)

Para sincronização de timecode por GPS e controle de capítulos:

1. Instale o firmware [GoPro Labs](https://gopro.github.io/labs/) (Hero 13 → v2.10.70)
2. Escaneie os QR codes gerados (na pasta do projeto) para:
   - `*SYNC=1` — sincronização de relógio via GPS
   - `*ZONE=-240` — fuso horário
   - `*64BT=22500` — capítulos de ~30 min (ajuste conforme bitrate)

## Sincronização de quadros no pós-processamento

```bash
python extract_timecodes.py "pasta_com_os_videos"
```

Lê o timecode (`tmcd`) de cada MP4 e mostra a diferença entre câmeras.

## Arquivos principais

- `app.py` — interface gráfica (PyQt6)
- `gopro_manager.py` — comunicação HTTP com as câmeras + descoberta
- `gopro_ble.py` — ativação de WiFi via Bluetooth (BLE)
- `extract_timecodes.py` — extração de timecode (requer ffmpeg)
