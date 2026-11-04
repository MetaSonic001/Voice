"""AU SFB voice agent - knowledge service ("brain")."""

import os

# Hugging Face's "xet" download path can stall on Windows; plain HTTPS downloads are reliable.
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
