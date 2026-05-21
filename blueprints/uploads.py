# Proxy private S3 objects through Flask: the bucket ACL is `private` so
# direct Scaleway URLs would 403. URLs live outside /static/* on purpose
# (Whitenoise serves /static/* straight from disk; we want Flask in the
# loop for user uploads). Trade-off: bytes transit through Scalingo —
# fine for image-sized payloads, revisit if we ever stream large PDFs.
import logging

from botocore.exceptions import BotoCoreError, ClientError
from flask import Blueprint, Response, abort, stream_with_context

from services.uploads import PUBLIC_UPLOAD_SUBFOLDERS, _get_s3, _s3_enabled


logger = logging.getLogger(__name__)

uploads_bp = Blueprint("uploads", __name__, url_prefix="/uploads")


_STREAM_CHUNK = 64 * 1024


@uploads_bp.route("/<path:key>")
def serve(key: str):
    if not _s3_enabled():
        abort(404)

    # Fail-closed allow-list so a future save_upload(subfolder="invoices")
    # stays opaque to unauth'd visitors despite sharing the same bucket.
    top = key.split("/", 1)[0]
    if top not in PUBLIC_UPLOAD_SUBFOLDERS:
        abort(404)

    from config import settings

    full_key = f"uploads/{key}"
    try:
        obj = _get_s3().get_object(Bucket=settings.s3_bucket, Key=full_key)
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        # 502 for broken transport so operators don't drown in fake-404s.
        if code in ("NoSuchKey", "404"):
            abort(404)
        logger.exception("S3 GetObject failed for %s", full_key)
        abort(502)
    except BotoCoreError:
        logger.exception("S3 transport error for %s", full_key)
        abort(502)

    body = obj["Body"]
    headers = {
        "Content-Type": obj.get("ContentType") or "application/octet-stream",
        # Content-addressed by uuid: bytes never change for a given key.
        "Cache-Control": obj.get("CacheControl")
        or "public, max-age=31536000, immutable",
    }
    length = obj.get("ContentLength")
    if length is not None:
        headers["Content-Length"] = str(length)

    def _generate():
        try:
            while True:
                chunk = body.read(_STREAM_CHUNK)
                if not chunk:
                    break
                yield chunk
        finally:
            body.close()

    return Response(
        stream_with_context(_generate()),
        headers=headers,
        direct_passthrough=True,
    )
