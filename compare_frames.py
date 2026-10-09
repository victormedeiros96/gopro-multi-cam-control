import sys
import os
import glob
import subprocess
import json

def find_ffprobe():
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

def get_video_info(mp4):
    """Retorna (timecode_em_segundos, fps_real) do video."""
    cmd = [FFPROBE, "-v", "error", "-show_entries",
           "stream=avg_frame_rate:stream_tags=timecode",
           "-of", "json", mp4]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    data = json.loads(out.stdout)
    tc = None
    fr = "240000/1001"
    for s in data.get("streams", []):
        tags = s.get("tags", {})
        if "timecode" in tags:
            tc = tags["timecode"]
            fr = s.get("avg_frame_rate", fr)
            break
    if not tc:
        return None, None
    num, den = fr.split("/")
    fps = int(num) / int(den)
    h, m, s, f = [int(x) for x in tc.split(":")]
    int_fps = round(fps)
    base = (3600 * h + 60 * m + s) + f / int_fps
    return base, fps

def frame_timestamp(base, fps, frame):
    """Timestamp (segundos desde meia-noite) do frame N."""
    return base + frame / fps

def main():
    target = sys.argv[1] if len(sys.argv) > 1 else "videos"
    cam1_dir = os.path.join(target, "camera 1")
    cam2_dir = os.path.join(target, "camera 2")

    cam1_files = sorted(glob.glob(os.path.join(cam1_dir, "*.MP4")))
    cam2_files = sorted(glob.glob(os.path.join(cam2_dir, "*.MP4")))

    if not cam1_files or not cam2_files:
        print("Pastas 'camera 1' e 'camera 2' nao encontradas (ou sem .MP4).")
        return

    n = min(len(cam1_files), len(cam2_files))

    # frames a comparar
    frames_alvo = [0, 1000, 2000, 5000, 10000, 20000, 30000, 40000, 50000, 60000]

    print(f"{'CAP':<4} {'FRAME':>8} {'CAM1 (s)':>14} {'CAM2 (s)':>14} {'DIFF (ms)':>12} {'DIFF (frames)':>14}")
    print("-" * 70)

    for i in range(n):
        base1, fps1 = get_video_info(cam1_files[i])
        base2, fps2 = get_video_info(cam2_files[i])
        if base1 is None or base2 is None:
            print(f"cap {i+1}: timecode nao encontrado")
            continue
        for fr in frames_alvo:
            t1 = frame_timestamp(base1, fps1, fr)
            t2 = frame_timestamp(base2, fps2, fr)
            diff = (t2 - t1) * 1000.0  # ms
            diff_frames = (t2 - t1) * 240
            print(f"{i+1:<4} {fr:>8} {t1:>14.3f} {t2:>14.3f} {diff:>+12.2f} {diff_frames:>+14.2f}")

if __name__ == "__main__":
    main()
