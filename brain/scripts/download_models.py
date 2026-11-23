"""Download every model the brain needs into brain/models/ (run once, when the internet is good).

    uv run python scripts/download_models.py

- bge-m3 (embeddings) and bge-reranker-v2-m3 (reranker) from Hugging Face. Resumes interrupted
  downloads and verifies each weights file against its published SHA-256.
- Weights are `.safetensors` only (no `.bin`: a pickle file can run code when loaded, so bank laptops block it).
  BAAI publishes bge-m3 only as `pytorch_model.bin`; its `.safetensors` comes from Hugging Face's own conversion bot
  (SFconvertbot, pull request 130 on BAAI/bge-m3), pinned to one commit. On 20 Nov 2026 it was checked to hold
  exactly the same tensors as BAAI's `.bin`, so embeddings are identical and the KB needs no re-indexing.
- `--onnx`: the ONNX versions instead, for SEARCH_RUNTIME=onnx (CPU machines; ADR 0029). bge-m3 from BAAI's own `onnx/`
  folder; the reranker from onnx-community (Hugging Face's converter, full precision). Both pinned to one commit.
Safe to re-run: complete, verified files are skipped.

    uv run python scripts/download_models.py --onnx
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
                    "model.safetensors"],
    "BAAI/bge-reranker-v2-m3": ["config.json", "special_tokens_map.json", "tokenizer.json", "tokenizer_config.json",
                                "sentencepiece.bpe.model", "model.safetensors"],
}
WEIGHTS = ("pytorch_model.bin", "model.safetensors")
# Download from this commit instead of main (bge-m3: SFconvertbot's safetensors conversion, refs/pr/130)
REVISIONS = {"BAAI/bge-m3": "9a0624b896d81da7492a910ffa53731274b6cf3d"}
# ONNX (--onnx): local folder -> (repo, commit, files). Each folder holds onnx/model.onnx + its weights and the tokenizer.
ONNX = {
    "bge-m3-onnx": ("BAAI/bge-m3", "5617a9f61b028005a4858fdac845db406aefb181",
                    ["onnx/config.json", "onnx/tokenizer.json", "onnx/tokenizer_config.json", "onnx/special_tokens_map.json",
                     "onnx/sentencepiece.bpe.model", "onnx/model.onnx", "onnx/model.onnx_data", "onnx/Constant_7_attr__value"]),
    "bge-reranker-v2-m3-onnx": ("onnx-community/bge-reranker-v2-m3-ONNX", "6f5ff65298512715a1e669753bc754d2bc8f367b",
                                ["config.json", "tokenizer.json", "tokenizer_config.json", "special_tokens_map.json",
                                 "onnx/model.onnx", "onnx/model.onnx_data"]),
}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(8 << 20), b""):
            h.update(block)
    return h.hexdigest()


def fetch_repo(repo: str, files: list[str], attempts: int = 8, target: Path | None = None, revision: str | None = None) -> None:
    target = target or MODELS / repo.split("/")[-1]
    revision = revision or REVISIONS.get(repo)
    # every large (LFS) file is checked against its published SHA-256: the weights, and the ONNX graph and data files
    expected = {p.path: p.lfs.sha256 for p in HfApi().get_paths_info(repo, files, revision=revision)
                if getattr(p, "lfs", None)}
    for name in files:
        path = target / name
        if name in expected and path.exists() and sha256(path) == expected[name]:
            print(f"  ok (verified) {repo}/{name}")
            continue
        for attempt in range(1, attempts + 1):
            try:
                start = time.time()
                hf_hub_download(repo, name, local_dir=target, revision=revision)  # resumes partial downloads
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
    if "--onnx" in sys.argv[1:]:
        for folder, (repo, revision, files) in ONNX.items():
            print(f"{repo} -> models/{folder}:")
            fetch_repo(repo, files, target=MODELS / folder, revision=revision)
    else:
        for repo, files in REPOS.items():
            print(f"{repo}:")
            fetch_repo(repo, files)
    print("All models ready.")
