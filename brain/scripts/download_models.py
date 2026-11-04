"""Download every model the brain needs into brain/models/ (run once, when the internet is good).

    uv run python scripts/download_models.py

- bge-m3 (embeddings) and bge-reranker-v2-m3 (reranker) from Hugging Face. Resumes interrupted
  downloads and verifies each weights file against its published SHA-256.
Safe to re-run: complete, verified files are skipped.
"""

import hashlib
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("HF_HUB_DISABLE_XET", "1")  # the xet path stalls on some Windows networks

from huggingface_hub import HfApi, hf_hub_download  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
MODELS = ROOT / "models"
REPOS = {
    "BAAI/bge-m3": ["config.json", "special_tokens_map.json", "tokenizer.json", "tokenizer_config.json", "sentencepiece.bpe.model",
                    "pytorch_model.bin"],
    "BAAI/bge-reranker-v2-m3": ["config.json", "special_tokens_map.json", "tokenizer.json", "tokenizer_config.json",
                                "sentencepiece.bpe.model", "model.safetensors"],
}
WEIGHTS = ("pytorch_model.bin", "model.safetensors")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(8 << 20), b""):
            h.update(block)
    return h.hexdigest()


def fetch_repo(repo: str, files: list[str], attempts: int = 8) -> None:
    target = MODELS / repo.split("/")[-1]
    expected = {p.path: p.lfs.sha256 for p in HfApi().get_paths_info(repo, [f for f in files if f in WEIGHTS]) if p.lfs}
    for name in files:
        path = target / name
        if name in expected and path.exists() and sha256(path) == expected[name]:
            print(f"  ok (verified) {repo}/{name}")
            continue
        for attempt in range(1, attempts + 1):
            try:
                start = time.time()
                hf_hub_download(repo, name, local_dir=target)  # resumes partial downloads
                if name in expected and sha256(path) != expected[name]:
                    path.unlink(missing_ok=True)
                    raise ValueError("checksum mismatch, re-downloading")
                print(f"  done {repo}/{name} ({path.stat().st_size / 1e6:.1f} MB, {time.time() - start:.0f}s)")
                break
            except Exception as e:  # network drops: wait and resume
                print(f"  retry {attempt}/{attempts} {repo}/{name}: {type(e).__name__}: {str(e)[:120]}")
                time.sleep(min(60, 5 * attempt))
        else:
            sys.exit(f"FAILED: {repo}/{name} - check the internet connection and run again")


if __name__ == "__main__":
    for repo, files in REPOS.items():
        print(f"{repo}:")
        fetch_repo(repo, files)
    print("All models ready.")
