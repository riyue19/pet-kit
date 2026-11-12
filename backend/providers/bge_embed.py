"""Local text embeddings: bge-small-zh-v1.5 (int8 ONNX, 512-d), CPU only, no network."""

import threading
from pathlib import Path

import numpy as np
import onnxruntime as ort
from tokenizers import Tokenizer

MODEL_FILE = "bge-small-zh/model_quantized.onnx"
TOKENIZER_FILE = "bge-small-zh/tokenizer.json"
MODEL_ID = "bge-small-zh-v1.5-int8"
DIM = 512
MAX_TOKENS = 512
BATCH = 16
# bge-zh: short queries retrieving passages get this instruction prefix; stored passages don't.
QUERY_PREFIX = "为这个句子生成表示以用于检索相关文章："


def embed_model_ready(model_dir: Path) -> bool:
    return (model_dir / MODEL_FILE).is_file() and (model_dir / TOKENIZER_FILE).is_file()


class BgeEmbedder:
    model_id = MODEL_ID
    dim = DIM

    def __init__(self, model_dir: Path, threads: int = 2) -> None:
        self._tok = Tokenizer.from_file(str(model_dir / TOKENIZER_FILE))
        self._tok.enable_truncation(MAX_TOKENS)
        self._tok.enable_padding()
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = threads
        opts.log_severity_level = 3
        self._sess = ort.InferenceSession(str(model_dir / MODEL_FILE), opts, providers=["CPUExecutionProvider"])
        self._inputs = {i.name for i in self._sess.get_inputs()}
        self._lock = threading.Lock()

    def embed(self, texts: list[str], *, query: bool = False) -> np.ndarray:
        """L2-normalized float32 vectors, shape (len(texts), DIM). Blocking; call from a worker thread."""
        if not texts:
            return np.zeros((0, DIM), dtype=np.float32)
        if query:
            texts = [QUERY_PREFIX + t for t in texts]
        out = []
        for i in range(0, len(texts), BATCH):
            enc = self._tok.encode_batch(texts[i : i + BATCH])
            ids = np.array([e.ids for e in enc], dtype=np.int64)
            feeds = {"input_ids": ids, "attention_mask": np.array([e.attention_mask for e in enc], dtype=np.int64)}
            if "token_type_ids" in self._inputs:
                feeds["token_type_ids"] = np.zeros_like(ids)
            with self._lock:
                cls = self._sess.run(None, feeds)[0][:, 0]
            out.append(cls / np.maximum(np.linalg.norm(cls, axis=1, keepdims=True), 1e-12))
        return np.concatenate(out).astype(np.float32)
