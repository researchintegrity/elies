"""
Storage of user uploads (images and PDF documents).

Files are written into the user's workspace as ``<ObjectId><ext>``. The MongoDB
id is allocated before anything touches the disk, so a client-supplied filename
never influences where a file is stored: it is kept only as display metadata
(see issue #50). Ownership, size, quota and content are checked before a record
is created, and a partially written file is removed on every failure path.
"""
import logging
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO, Callable, Iterable, Optional

from bson import ObjectId
from PIL import Image, UnidentifiedImageError

from app.config.settings import MAX_IMAGE_PIXELS, convert_host_path_to_container
from app.config.storage_quota import MAX_IMAGE_FILE_SIZE, MAX_PDF_FILE_SIZE, format_bytes
from app.db.mongodb import get_documents_collection, get_images_collection
from app.exceptions import ResourceNotFoundError, ValidationError
from app.services.storage_service import add_storage, release_storage, reserve_storage
from app.utils.file_storage import (
    ALLOWED_IMAGE_EXTENSIONS,
    ALLOWED_PDF_EXTENSIONS,
    get_user_upload_path,
)
from app.utils.metadata_parser import extract_exif_metadata

logger = logging.getLogger(__name__)

CHUNK_SIZE = 1024 * 1024
MAX_DISPLAY_NAME_LENGTH = 255
PDF_HEADER_WINDOW = 1024


def display_filename(filename: Optional[str]) -> str:
    """Return the last path component of a client filename, for display only."""
    name = (filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(ch for ch in name if ch.isprintable()).strip()
    return name[:MAX_DISPLAY_NAME_LENGTH] or "upload"


def _extension(filename: str, allowed: Iterable[str]) -> str:
    ext = Path(filename).suffix.lower()
    if ext not in allowed:
        raise ValidationError(
            f"Invalid file type: {ext or 'none'}. Allowed types: {', '.join(sorted(allowed))}."
        )
    return ext


def _stream_size(stream: BinaryIO) -> int:
    """Size of a seekable upload stream, leaving the position at the start."""
    stream.seek(0, 2)
    size = stream.tell()
    stream.seek(0)
    return size


def _check_size(size: int, max_bytes: int) -> None:
    if size == 0:
        raise ValidationError("File is empty.")
    if size > max_bytes:
        raise ValidationError(f"File too large. Maximum size is {format_bytes(max_bytes)}.")


def _copy_limited(stream: BinaryIO, dest: Path, max_bytes: int) -> int:
    """Copy a stream to ``dest`` in chunks, refusing to write more than ``max_bytes``."""
    written = 0
    with open(dest, "wb") as out:
        while chunk := stream.read(CHUNK_SIZE):
            written += len(chunk)
            if written > max_bytes:
                raise ValidationError(f"File too large. Maximum size is {format_bytes(max_bytes)}.")
            out.write(chunk)
    _check_size(written, max_bytes)
    return written


def discard_stored_file(user_id: str, path: Path, size: int) -> None:
    """Undo store_upload: remove the file and give its bytes back."""
    path.unlink(missing_ok=True)
    release_storage(user_id, size)


def store_upload(
    user: dict,
    stream: BinaryIO,
    dest: Path,
    max_bytes: int,
    verify: Callable[[Path], None],
) -> int:
    """
    Write an upload stream to ``dest`` and charge it to the user's quota.

    The size is checked and reserved atomically before writing (the stream is
    a spooled temporary file, so its size is known), the copy is capped at
    ``max_bytes`` and ``verify`` checks the content. On any failure the file is
    removed and the reservation released.

    Returns:
        The number of bytes stored.

    Raises:
        ValidationError: empty, too large or invalid content.
        StorageQuotaExceededError: the upload would exceed the user's quota.
    """
    user_id = str(user["_id"])
    size = _stream_size(stream)
    _check_size(size, max_bytes)
    reserve_storage(user, size)
    try:
        written = _copy_limited(stream, dest, max_bytes)
        verify(dest)
    except BaseException:
        discard_stored_file(user_id, dest, size)
        raise
    if written != size:
        add_storage(user_id, written - size)
    return written


def verify_image_file(path: Path) -> None:
    """Raise ValidationError unless ``path`` is a decodable image of acceptable size."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(path) as img:
                width, height = img.size
                if width * height > MAX_IMAGE_PIXELS:
                    raise Image.DecompressionBombError("too many pixels")
                img.verify()
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ValidationError(
            f"Image dimensions are too large (limit: {MAX_IMAGE_PIXELS} pixels)."
        ) from exc
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as exc:
        raise ValidationError("File is not a valid image.") from exc


def _verify_pdf_file(path: Path) -> None:
    with open(path, "rb") as f:
        if b"%PDF-" not in f.read(PDF_HEADER_WINDOW):
            raise ValidationError("File is not a valid PDF.")


def _require_owned_document(document_id: str, user_id: str) -> None:
    if not ObjectId.is_valid(document_id):
        raise ValidationError("Invalid document ID format")
    found = get_documents_collection().find_one(
        {"_id": ObjectId(document_id), "user_id": user_id}, {"_id": 1}
    )
    if not found:
        raise ResourceNotFoundError("Document", document_id)


def save_uploaded_image(
    user: dict,
    filename: Optional[str],
    stream: BinaryIO,
    document_id: Optional[str] = None,
) -> dict:
    """
    Validate and store an uploaded image, then create its ``images`` record.

    Raises:
        ValidationError: bad extension, size, content or document id.
        ResourceNotFoundError: ``document_id`` does not belong to the user.
        StorageQuotaExceededError: the upload would exceed the user's quota.
    """
    user_id = str(user["_id"])
    original_filename = display_filename(filename)
    ext = _extension(original_filename, ALLOWED_IMAGE_EXTENSIONS)
    if document_id:
        _require_owned_document(document_id, user_id)

    image_id = ObjectId()
    stored_name = f"{image_id}{ext}"
    final_path = get_user_upload_path(user_id, "images/uploaded") / stored_name
    saved_size = store_upload(user, stream, final_path, MAX_IMAGE_FILE_SIZE, verify_image_file)

    try:
        image_doc = {
            "_id": image_id,
            "user_id": user_id,
            "filename": stored_name,
            "file_path": str(convert_host_path_to_container(final_path)),
            "file_size": saved_size,
            "source_type": "uploaded",
            "document_id": document_id,
            "pdf_page": None,
            "page_bbox": None,
            "extraction_mode": None,
            "original_filename": original_filename,
            "image_type": [],
            "uploaded_date": datetime.now(timezone.utc),
            "exif_metadata": extract_exif_metadata(str(final_path)),
        }
        get_images_collection().insert_one(image_doc)
    except BaseException:
        discard_stored_file(user_id, final_path, saved_size)
        raise

    return image_doc


def save_uploaded_pdf(user: dict, filename: Optional[str], stream: BinaryIO) -> dict:
    """
    Validate and store an uploaded PDF, then create its ``documents`` record.

    Raises:
        ValidationError: bad extension, size or content.
        StorageQuotaExceededError: the upload would exceed the user's quota.
    """
    user_id = str(user["_id"])
    original_filename = display_filename(filename)
    _extension(original_filename, ALLOWED_PDF_EXTENSIONS)

    doc_id = ObjectId()
    final_path = get_user_upload_path(user_id, "pdfs") / f"{doc_id}.pdf"
    saved_size = store_upload(user, stream, final_path, MAX_PDF_FILE_SIZE, _verify_pdf_file)

    try:
        doc = {
            "_id": doc_id,
            "user_id": user_id,
            "filename": original_filename,
            "file_path": str(convert_host_path_to_container(final_path)),
            "file_size": saved_size,
            "extraction_status": "pending",
            "extracted_image_count": 0,
            "extraction_errors": [],
            "uploaded_date": datetime.now(timezone.utc),
        }
        get_documents_collection().insert_one(doc)
    except BaseException:
        discard_stored_file(user_id, final_path, saved_size)
        raise

    return doc
