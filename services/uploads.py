# VULN-05 / VULN-10: extension allow-list + magic-byte check + size cap.
# S3 when S3_BUCKET is set, local disk otherwise. None on rejection so
# the caller can flash a generic error.
import io
import logging
import os
import uuid

from botocore.exceptions import BotoCoreError, ClientError
import pikepdf
from PIL import Image
from werkzeug.utils import secure_filename

logger = logging.getLogger(__name__)

ALLOWED_EXTENSIONS = {"jpg", "jpeg", "png", "gif", "webp", "pdf"}
UPLOAD_DIR = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "static", "uploads"
)

# Public subtree allow-list for the /uploads/<key> proxy: adding here is
# an explicit choice to skip the authz gate.
PUBLIC_UPLOAD_SUBFOLDERS = frozenset({"caterers"})
MAX_FILENAME_LENGTH = 80
MAX_FILE_SIZE = 10 * 1024 * 1024  # global request cap is 16 MB total

_CONTENT_TYPES = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "gif": "image/gif",
    "webp": "image/webp",
    "pdf": "application/pdf",
}

_MAGIC_SIGNATURES = {
    "png": [(b"\x89PNG\r\n\x1a\n", 0)],
    "jpg": [(b"\xff\xd8\xff", 0)],
    "jpeg": [(b"\xff\xd8\xff", 0)],
    "gif": [(b"GIF87a", 0), (b"GIF89a", 0)],
    # WEBP: RIFF + 4-byte size + WEBP
    "webp": [(b"RIFF", 0), (b"WEBP", 8)],
    "pdf": [(b"%PDF-", 0)],
}

_EXTENSION_ALIASES = {
    "jpg": {"jpg", "jpeg"},
    "jpeg": {"jpg", "jpeg"},
}

_s3_client = None


def _get_s3():
    global _s3_client
    if _s3_client is None:
        import boto3
        from config import settings

        kwargs = {
            "aws_access_key_id": settings.s3_access_key,
            "aws_secret_access_key": settings.s3_secret_key.get_secret_value()
            if settings.s3_secret_key
            else None,
            "region_name": settings.s3_region,
        }
        if settings.s3_endpoint_url:
            kwargs["endpoint_url"] = settings.s3_endpoint_url
        _s3_client = boto3.client("s3", **kwargs)
    return _s3_client


def _s3_enabled() -> bool:
    from config import settings

    return bool(settings.s3_bucket)


def _detect_real_type(head: bytes) -> str | None:
    # Same-offset signatures are OR'd (GIF87a/GIF89a); distinct offsets are
    # AND'd (WEBP needs RIFF@0 AND WEBP@8). Naive all-pairs rejected GIFs.
    for ext, sigs in _MAGIC_SIGNATURES.items():
        by_offset: dict[int, list[bytes]] = {}
        for sig, off in sigs:
            by_offset.setdefault(off, []).append(sig)
        if all(
            any(head[off : off + len(sig)] == sig for sig in alts)
            for off, alts in by_offset.items()
        ):
            return ext
    return None


def _file_size(stream) -> int:
    pos = stream.tell()
    stream.seek(0, os.SEEK_END)
    size = stream.tell()
    stream.seek(pos)
    return size


def allowed_extension(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


_PILLOW_FORMAT = {
    "jpg": "JPEG",
    "jpeg": "JPEG",
    "png": "PNG",
    "gif": "GIF",
    "webp": "WEBP",
}

_MAX_IMAGE_PIXELS = 25_000_000  # 5000×5000 — decompression-bomb guard


def _reencode_image(stream, ext: str):
    # Re-encode strips embedded payloads (EXIF/SVG comments etc.).
    fmt = _PILLOW_FORMAT.get(ext)
    if fmt is None:
        return None
    try:
        Image.MAX_IMAGE_PIXELS = _MAX_IMAGE_PIXELS
        img = Image.open(stream)
        img.load()
        if img.mode not in ("RGB", "RGBA", "L", "P"):
            img = img.convert("RGBA" if fmt == "PNG" else "RGB")
        buf = io.BytesIO()
        save_kwargs = {}
        if fmt == "JPEG":
            img = img.convert("RGB")
            save_kwargs["quality"] = 85
        if fmt == "GIF" and getattr(img, "is_animated", False):
            save_kwargs["save_all"] = True
        img.save(buf, format=fmt, **save_kwargs)
        buf.seek(0)
        return buf
    except Exception:
        logger.warning("Pillow re-encode failed for %s", ext, exc_info=True)
        return None


def _reencode_pdf(stream):
    # Strips JavaScript, auto-open actions, embedded files.
    try:
        pdf = pikepdf.open(stream)
        if pikepdf.Name.Names in pdf.Root:
            names = pdf.Root[pikepdf.Name.Names]
            for key in (pikepdf.Name.JavaScript, pikepdf.Name.EmbeddedFiles):
                if key in names:
                    del names[key]
        if pikepdf.Name.OpenAction in pdf.Root:
            del pdf.Root[pikepdf.Name.OpenAction]
        if pikepdf.Name.AA in pdf.Root:
            del pdf.Root[pikepdf.Name.AA]
        buf = io.BytesIO()
        pdf.save(buf)
        buf.seek(0)
        pdf.close()
        return buf
    except Exception:
        logger.warning("pikepdf re-encode failed", exc_info=True)
        return None


def _validate(file):
    if not file or not file.filename:
        return None

    declared_ext = (
        file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
    )
    if declared_ext not in ALLOWED_EXTENSIONS:
        logger.warning("upload rejected: extension '%s' not allowed", declared_ext)
        return None

    head = file.stream.read(16)
    file.stream.seek(0)
    real_ext = _detect_real_type(head)
    if real_ext is None:
        logger.warning(
            "upload rejected: no matching magic bytes (declared %s)", declared_ext
        )
        return None

    allowed_aliases = _EXTENSION_ALIASES.get(declared_ext, {declared_ext})
    if real_ext not in allowed_aliases:
        logger.warning(
            "upload rejected: magic bytes say %s but extension says %s",
            real_ext,
            declared_ext,
        )
        return None

    size = _file_size(file.stream)
    if size > MAX_FILE_SIZE:
        logger.warning("upload rejected: %d bytes exceeds %d", size, MAX_FILE_SIZE)
        return None
    if size == 0:
        logger.warning("upload rejected: empty file")
        return None

    clean = secure_filename(file.filename)[:MAX_FILENAME_LENGTH] or "file"
    safe_name = f"{uuid.uuid4().hex}_{clean}"
    return declared_ext, safe_name


def _save_local(file, subfolder: str, safe_name: str) -> str:
    target_dir = os.path.join(UPLOAD_DIR, subfolder)
    os.makedirs(target_dir, exist_ok=True)
    file.save(os.path.join(target_dir, safe_name))
    return f"/static/uploads/{subfolder}/{safe_name}"


def _s3_key_from_url(url: str) -> str | None:
    # None for legacy /static/uploads/* or absolute URLs.
    if not url or not url.startswith("/uploads/"):
        return None
    return f"uploads/{url[len('/uploads/') :]}"


def _save_s3(file, subfolder: str, safe_name: str, declared_ext: str) -> str:
    from config import settings

    key = f"uploads/{subfolder}/{safe_name}"
    _get_s3().upload_fileobj(
        file.stream,
        settings.s3_bucket,
        key,
        ExtraArgs={
            "ContentType": _CONTENT_TYPES.get(declared_ext, "application/octet-stream"),
            "CacheControl": "public, max-age=31536000, immutable",
            # Per-object ACL so a misconfigured bucket policy doesn't flip
            # uploads to public.
            "ACL": "private",
        },
    )
    # Path is served by the Flask /uploads/<key> proxy — never a direct
    # Scaleway URL (the bucket is private, would 403).
    return f"/{key}"


def delete_upload(url: str) -> bool:
    # Best-effort: True when nothing to do or delete issued; False on S3
    # error so the caller can retry. Legacy /static/uploads/* URLs are
    # left alone — the migration script handles them.
    key = _s3_key_from_url(url)
    if key is None:
        return True
    if not _s3_enabled():
        return True
    from config import settings

    try:
        _get_s3().delete_object(Bucket=settings.s3_bucket, Key=key)
        return True
    except (BotoCoreError, ClientError):
        logger.exception("S3 delete failed for %s", key)
        return False


def save_upload(file, subfolder: str = "general") -> str | None:
    result = _validate(file)
    if result is None:
        return None
    declared_ext, safe_name = result

    if declared_ext == "pdf":
        clean_buf = _reencode_pdf(file.stream)
    else:
        clean_buf = _reencode_image(file.stream, declared_ext)
    if clean_buf is None:
        logger.warning("upload rejected: re-encode failed for %s", declared_ext)
        return None
    file.stream = clean_buf

    if _s3_enabled():
        try:
            return _save_s3(file, subfolder, safe_name, declared_ext)
        except (BotoCoreError, ClientError):
            logger.exception("S3 upload failed for %s", safe_name)
            return None
        except Exception:
            # _get_s3() can fail with ValueError / ImportError on bad
            # credentials or missing boto3 — bucket as misconfiguration.
            logger.exception("S3 client configuration error for %s", safe_name)
            return None

    return _save_local(file, subfolder, safe_name)
