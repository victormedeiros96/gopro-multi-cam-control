import sys
import os
import glob
import subprocess
import json

def find_ffprobe():
    """Localiza o ffprobe (PATH ou locais comuns do winget)."""
    import shutil
    p = shutil.which("ffprobe")
    if p:
        return p
    base = os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Packages")
    for root, dirs, files in os.walk(base):
        if "ffprobe.exe" in files:
            return os.path.join(root, "ffprobe.exe")
    return "ffprobe"

FFPROBE = find_ffprobe()

def extract_timecode(mp4_path):
    """Extrai o timecode (tmcd) de um arquivo MP4 via ffprobe."""
    cmd = [
        FFPROBE, "-v", "error",
        "-select_streams", "t:tmcd",
        "-show_entries", "stream=avg_frame_rate:stream_tags=timecode",
        "-of", "json", mp4_path,
    ]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if out.returncode != 0:
            return None
        data = json.loads(out.stdout)
        for stream in data.get("streams", []):
            tc = stream.get("tags", {}).get("timecode")
            fr = stream.get("avg_frame_rate", "0/1")
            if tc:
                return {"timecode": tc, "frame_rate": fr}
    except Exception as e:
        print(f"  Erro em {mp4_path}: {e}")
    return None

def tc_to_seconds(tc, fr_str):
    """Converte timecode HH:MM:SS:FF em segundos."""
    try:
        num, den = fr_str.split("/")
        fps = int(num) / int(den)
    except Exception:
        fps = 30.0
    h, m, s, f = [int(x) for x in tc.split(":")]
    int_fps = round(fps)
    frames = (3600 * h + 60 * m + s) * int_fps + f
    return frames / fps

def main():
    target = sys.argv[1] if len(sys.argv) > 1 else "."
    if os.path.isdir(target):
        files = sorted(glob.glob(os.path.join(target, "**", "*.MP4"), recursive=True))
    else:
        files = [target]

    if not files:
        print("Nenhum arquivo .MP4 encontrado.")
        return

    rows = []
    for f in files:
        info = extract_timecode(f)
        if info:
            secs = tc_to_seconds(info["timecode"], info["frame_rate"])
            rows.append((os.path.basename(f), info["timecode"], info["frame_rate"], secs))

    if not rows:
        print("Nenhum timecode encontrado (câmera tem Labs instalado e SYNC ativado?).")
        return

    rows.sort(key=lambda r: r[3])
    print(f"{'ARQUIVO':<20} {'TIMECODE':<16} {'FPS':<10} {'SEGUNDOS':>12}")
    print("-" * 60)
    for name, tc, fr, secs in rows:
        print(f"{name:<20} {tc:<16} {fr:<10} {secs:>12.3f}")

    print("\nDiferença entre câmeras (último - primeiro):")
    if len(rows) >= 2:
        diff = rows[-1][3] - rows[0][3]
        print(f"  {diff:.3f} s  (~{diff * 240:.0f} frames a 240fps)")

if __name__ == "__main__":
    main()
