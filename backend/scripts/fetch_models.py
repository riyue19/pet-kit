"""Downloads the local models into backend/models/ (not tracked by git).

  uv run python scripts/fetch_models.py

models/asr/:   SenseVoice-Small (int8, sherpa-onnx export) speech recognition + Silero VAD
models/embed/: bge-small-zh-v1.5 (int8 ONNX) text embeddings for memory search
models/camera/: YuNet face detector (ONNX, ~230KB) for camera presence detection
"""

import hashlib
import shutil
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

BASE = "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models"
SENSE_VOICE = "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17"
MODELS_DIR = Path(__file__).resolve().parents[1] / "models" / "asr"
EMBED_DIR = Path(__file__).resolve().parents[1] / "models" / "embed" / "bge-small-zh"
# hf-mirror.com serves the same files as huggingface.co and is reachable from mainland China.
EMBED_BASES = ("https://hf-mirror.com", "https://huggingface.co")
EMBED_REPO = "Xenova/bge-small-zh-v1.5"
# tokenizer first: model_quantized.onnx appearing last marks the directory complete.
CAMERA_DIR = Path(__file__).resolve().parents[1] / "models" / "camera"
CAMERA_FILE = "face_detection_yunet.onnx"
CAMERA_SHA256 = "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"
CAMERA_URLS = (
    "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx",
    "https://hf-mirror.com/opencv/face_detection_yunet/resolve/main/face_detection_yunet_2023mar.onnx",
)
EMBED_FILES = (("tokenizer.json", "tokenizer.json"), ("onnx/model_quantized.onnx", "model_quantized.onnx"))


def download(url: str, dest: Path) -> None:
    print(f"下载 {url}")
    # Written to .part and renamed when complete, so an interrupted download never looks finished.
    part = dest.with_name(dest.name + ".part")
    with urllib.request.urlopen(url, timeout=60) as r, part.open("wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        while chunk := r.read(1 << 20):
            f.write(chunk)
            done += len(chunk)
            if total:
                print(f"\r  {done / total:6.1%}  {done >> 20} / {total >> 20} MB", end="", flush=True)
    print()
    if total and done != total:
        raise SystemExit(f"下载不完整（{done}/{total} 字节），请重试")
    part.replace(dest)


def main() -> int:
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    vad = MODELS_DIR / "silero_vad.onnx"
    if not vad.exists():
        download(f"{BASE}/silero_vad.onnx", vad)

    target = MODELS_DIR / "sense-voice"
    if not (target / "model.int8.onnx").exists():
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "sense-voice.tar.bz2"
            download(f"{BASE}/{SENSE_VOICE}.tar.bz2", archive)
            print("解压…")
            with tarfile.open(archive) as tar:
                # filter="data" rejects absolute paths, links and traversal outside tmp.
                tar.extractall(tmp, filter="data")
            src = Path(tmp) / SENSE_VOICE
            target.mkdir(exist_ok=True)
            # model.int8.onnx moves last: its presence marks the directory complete.
            for name in ("tokens.txt", "model.int8.onnx"):
                shutil.move(src / name, target / name)
    EMBED_DIR.mkdir(parents=True, exist_ok=True)
    for remote, local in EMBED_FILES:
        dest = EMBED_DIR / local
        if dest.exists():
            continue
        for base in EMBED_BASES:
            try:
                download(f"{base}/{EMBED_REPO}/resolve/main/{remote}", dest)
                break
            except (OSError, SystemExit) as e:
                print(f"  失败：{e}，换下一个源")
        else:
            raise SystemExit(f"无法下载 {remote}")
    CAMERA_DIR.mkdir(parents=True, exist_ok=True)
    cam = CAMERA_DIR / CAMERA_FILE
    if not cam.exists():
        for url in CAMERA_URLS:
            try:
                download(url, cam)
                if hashlib.sha256(cam.read_bytes()).hexdigest() != CAMERA_SHA256:
                    cam.unlink()
                    raise SystemExit("校验失败（sha256 不符）")
                break
            except (OSError, SystemExit) as e:
                print(f"  失败：{e}，换下一个源")
        else:
            raise SystemExit("无法下载人脸检测模型")
    print(f"完成：{MODELS_DIR.parent}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
