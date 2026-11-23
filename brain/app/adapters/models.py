"""Local embedding and reranking models, run with Hugging Face `transformers` + PyTorch. Loaded once at startup.

- Embedder (bge-m3): the [CLS] token vector, L2-normalised (the model's own pooling config).
- Reranker (bge-reranker-v2-m3): one relevance logit per (question, passage) pair, squashed to 0..1.
On a GPU both run in half precision (fp16): ~10x faster than CPU and both fit in 4 GB of VRAM.

SEARCH_RUNTIME=onnx (ADR 0029): the same two models as ONNX files, run with ONNX Runtime on the CPU (`OnnxEmbedder`,
`OnnxReranker`). Same tokenizer, same maths after the model; for laptops without a GPU.
"""

import threading
from pathlib import Path


def resolve(model: str, models_dir: Path) -> str:
    """Use a local copy in models/<name> when present (offline, no download), else the Hugging Face id."""
    local = models_dir / model.split("/")[-1]
    has_weights = any((local / f).exists() for f in ("model.safetensors", "pytorch_model.bin"))
    return str(local) if (local / "config.json").exists() and has_weights else model


def pick_device(device: str) -> str:
    """'auto' -> 'cuda' when a CUDA GPU is usable, else 'cpu'."""
    if device != "auto":
        return device
    import torch

    return "cuda" if torch.cuda.is_available() else "cpu"


def _load(model_cls, model: str, models_dir: Path, device: str):
    import torch
    from transformers import AutoTokenizer

    path = resolve(model, models_dir)
    dtype = torch.float16 if device.startswith("cuda") else torch.float32
    net = model_cls.from_pretrained(path, dtype=dtype).to(device).eval()
    return AutoTokenizer.from_pretrained(path), net


class TransformerEmbedder:
    def __init__(self, model: str, dim: int, device: str = "auto", max_seq_length: int = 512,
                 models_dir: Path = Path("models")):
        from transformers import AutoModel

        self.device = pick_device(device)
        self.name, self.dim, self.max_len = model, dim, max_seq_length  # the name identifies stored embeddings
        self._tok, self._net = _load(AutoModel, model, models_dir, self.device)
        self._lock = threading.Lock()  # one model, many threads: run one batch at a time

    def embed(self, texts: list[str], batch_size: int = 16) -> list[list[float]]:
        import torch

        out: list[list[float]] = []
        for i in range(0, len(texts), batch_size):
            # Lock per batch, not per call: while a large document is being indexed, a live caller's query
            # waits for at most one batch (~0.1 s), not for the whole file.
            with self._lock, torch.inference_mode():
                batch = self._tok(texts[i:i + batch_size], padding=True, truncation=True, max_length=self.max_len,
                                  return_tensors="pt").to(self.device)
                cls = self._net(**batch).last_hidden_state[:, 0].float()
                out += torch.nn.functional.normalize(cls, dim=-1).cpu().tolist()
        return out

    def embed_query(self, text: str) -> list[float]:
        return self.embed([text])[0]


class CrossEncoderReranker:
    def __init__(self, model: str, device: str = "auto", max_length: int = 384, models_dir: Path = Path("models")):
        from transformers import AutoModelForSequenceClassification

        self.device = pick_device(device)
        self.name, self.max_len = model, max_length
        self._tok, self._net = _load(AutoModelForSequenceClassification, model, models_dir, self.device)
        self._lock = threading.Lock()

    def score(self, query: str, passages: list[str], batch_size: int = 16) -> list[float]:
        import torch

        scores: list[float] = []
        with self._lock, torch.inference_mode():
            for i in range(0, len(passages), batch_size):
                chunk = passages[i:i + batch_size]
                batch = self._tok([query] * len(chunk), chunk, padding=True, truncation="only_second",
                                  max_length=self.max_len, return_tensors="pt").to(self.device)
                scores += torch.sigmoid(self._net(**batch).logits.view(-1).float()).cpu().tolist()
        return scores


class NoReranker:
    """RERANK=false: no model is loaded and nothing is scored. Retriever.search keeps the hybrid-search (RRF) order
    and never calls this; it is here so code that names the reranker (logs, /health, warm-up) still works."""

    name, device = "off", "-"

    def score(self, query: str, passages: list[str], batch_size: int = 16) -> list[float]:
        return [0.0] * len(passages)


# ───────── ONNX Runtime (SEARCH_RUNTIME=onnx, CPU) ─────────

def _onnx_load(folder: Path):
    """The tokenizer and an ONNX Runtime session for models/<folder> (from `download_models.py --onnx`)."""
    import onnxruntime as ort
    from transformers import AutoTokenizer

    if not (folder / "onnx" / "model.onnx").exists():
        raise FileNotFoundError(f"{folder}/onnx/model.onnx not found: run `uv run python scripts/download_models.py --onnx`")
    tok_dir = folder / "onnx" if (folder / "onnx" / "tokenizer.json").exists() else folder
    opts = ort.SessionOptions()
    opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    session = ort.InferenceSession(str(folder / "onnx" / "model.onnx"), opts, providers=["CPUExecutionProvider"])
    return AutoTokenizer.from_pretrained(str(tok_dir)), session


def _onnx_run(session, batch) -> "object":
    """Feed the inputs the model declares (input_ids, attention_mask, ...) as int64; return the first output."""
    import numpy as np

    feed = {i.name: np.asarray(batch[i.name], dtype=np.int64) for i in session.get_inputs() if i.name in batch}
    return session.run(None, feed)[0]


class OnnxEmbedder:
    """bge-m3 as ONNX: the [CLS] vector, L2-normalised - the same as TransformerEmbedder."""

    def __init__(self, model: str, dim: int, max_seq_length: int = 512, models_dir: Path = Path("models")):
        self.device = "cpu (onnx)"
        self.name, self.dim, self.max_len = model, dim, max_seq_length  # same name: the stored embeddings stay valid
        self._tok, self._session = _onnx_load(models_dir / f"{model.split('/')[-1]}-onnx")
        self._lock = threading.Lock()

    def embed(self, texts: list[str], batch_size: int = 16) -> list[list[float]]:
        import numpy as np

        out: list[list[float]] = []
        for i in range(0, len(texts), batch_size):
            with self._lock:
                batch = self._tok(texts[i:i + batch_size], padding=True, truncation=True, max_length=self.max_len,
                                  return_tensors="np")
                hidden = _onnx_run(self._session, batch)
            cls = hidden[:, 0] if hidden.ndim == 3 else hidden  # last_hidden_state -> [CLS]; a pooled output as is
            cls = cls.astype(np.float32)
            out += (cls / np.maximum(np.linalg.norm(cls, axis=-1, keepdims=True), 1e-12)).tolist()
        return out

    def embed_query(self, text: str) -> list[float]:
        return self.embed([text])[0]


class OnnxReranker:
    """bge-reranker-v2-m3 as ONNX: one logit per (question, passage), squashed to 0..1 - the same as CrossEncoderReranker."""

    def __init__(self, model: str, max_length: int = 384, models_dir: Path = Path("models")):
        self.device = "cpu (onnx)"
        self.name, self.max_len = model, max_length
        self._tok, self._session = _onnx_load(models_dir / f"{model.split('/')[-1]}-onnx")
        self._lock = threading.Lock()

    def score(self, query: str, passages: list[str], batch_size: int = 16) -> list[float]:
        import numpy as np

        scores: list[float] = []
        with self._lock:
            for i in range(0, len(passages), batch_size):
                chunk = passages[i:i + batch_size]
                batch = self._tok([query] * len(chunk), chunk, padding=True, truncation="only_second",
                                  max_length=self.max_len, return_tensors="np")
                logits = _onnx_run(self._session, batch).reshape(-1).astype(np.float32)
                scores += (1 / (1 + np.exp(-logits))).tolist()
        return scores

