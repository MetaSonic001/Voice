"""File storage for uploaded originals. Same three operations on local disk or S3 / S3-compatible (MinIO)."""

import shutil
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


class LocalFileStore:
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)

    def put(self, key: str, data: bytes) -> None:
        target = self.root / key
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    @contextmanager
    def local_path(self, key: str) -> Iterator[Path]:
        yield self.root / key

    def delete_prefix(self, prefix: str) -> None:
        target = self.root / prefix
        if target.is_dir():
            shutil.rmtree(target, ignore_errors=True)
        elif target.exists():
            target.unlink()


class S3FileStore:
    """AWS S3, or MinIO / any S3-compatible service via `endpoint_url`. Server-side encryption on every object."""

    def __init__(self, bucket: str, prefix: str = "", region: str | None = None, endpoint_url: str | None = None,
                 client=None):
        if not bucket:
            raise ValueError("S3_BUCKET is required when FILE_STORE=s3")
        if client is None:
            import boto3

            client = boto3.client("s3", region_name=region, endpoint_url=endpoint_url or None)
        self._s3, self.bucket, self.prefix = client, bucket, prefix
        self._sse = {} if endpoint_url else {"ServerSideEncryption": "AES256"}  # MinIO may not have KMS configured

    def put(self, key: str, data: bytes) -> None:
        self._s3.put_object(Bucket=self.bucket, Key=self.prefix + key, Body=data, **self._sse)

    @contextmanager
    def local_path(self, key: str) -> Iterator[Path]:
        """Download to a temporary file for parsing; removed afterwards."""
        with tempfile.TemporaryDirectory(prefix="brain-") as tmp:
            path = Path(tmp) / Path(key).name
            self._s3.download_file(self.bucket, self.prefix + key, str(path))
            yield path

    def delete_prefix(self, prefix: str) -> None:
        paginator = self._s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=self.prefix + prefix):
            keys = [{"Key": o["Key"]} for o in page.get("Contents", [])]
            if keys:
                self._s3.delete_objects(Bucket=self.bucket, Delete={"Objects": keys})
