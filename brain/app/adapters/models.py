"""Local embedding and reranking models, run with Hugging Face `transformers` + PyTorch. Loaded once at startup.

- Embedder (bge-m3): the [CLS] token vector, L2-normalised (the model's own pooling config).
- Reranker (bge-reranker-v2-m3): one relevance logit per (question, passage) pair, squashed to 0..1.
On a GPU both run in half precision (fp16): ~10x faster than CPU and both fit in 4 GB of VRAM.
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
