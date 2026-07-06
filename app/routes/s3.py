"""
DriveX S3 API Routes
All S3 operations exposed as REST endpoints.
Every route validates authentication, CSRF, and bucket access.
"""
import os
import urllib.parse
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse

from app.security import (
    require_auth_api,
    validate_s3_key,
    validate_bucket_name,
    sanitize_filename,
    validate_system_metadata,
    validate_user_metadata,
    audit_log,
    validate_csrf_token,
)
from app.services import s3_service, metadata_service

router = APIRouter()

MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_SIZE_MB", 5000))
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024


def _get_creds_and_buckets(request: Request) -> tuple:
    """Extract credentials and allowed buckets from session."""
    creds = require_auth_api(request)
    allowed_buckets = request.session.get("buckets", [])
    return creds, allowed_buckets


def _check_csrf(request: Request, token: str):
    """Validate CSRF token for mutating operations."""
    if not validate_csrf_token(request, token):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")


# --- List & Browse ---

@router.get("/list")
async def list_objects(
    request: Request,
    bucket: str,
    prefix: str = "",
):
    """List objects in a bucket/prefix."""
    creds, allowed = _get_creds_and_buckets(request)
    bucket = validate_bucket_name(bucket, allowed)
    # Sanitize prefix
    if prefix:
        prefix = prefix.lstrip("/")

    try:
        result = s3_service.list_objects(creds, bucket, prefix)
        audit_log(request, "LIST_OBJECTS", bucket=bucket, key=prefix or "/")
        return JSONResponse(result)
    except HTTPException:
        raise
    except Exception as e:
        audit_log(request, "LIST_OBJECTS", bucket=bucket, key=prefix, status="FAILURE", detail=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/search")
async def search_objects(
    request: Request,
    bucket: str,
    query: str,
    prefix: str = "",
):
    """Search objects in a bucket."""
    creds, allowed = _get_creds_and_buckets(request)
    bucket = validate_bucket_name(bucket, allowed)

    if len(query) < 1:
        raise HTTPException(status_code=400, detail="Search query too short")

    try:
        results = s3_service.search_objects(creds, bucket, query, prefix)
        return JSONResponse({"results": results, "count": len(results)})
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# --- Download ---

@router.get("/download")
async def download_object(
    request: Request,
    bucket: str,
    key: str,
):
    """Download an S3 object."""
    creds, allowed = _get_creds_and_buckets(request)
    bucket = validate_bucket_name(bucket, allowed)
    key = validate_s3_key(urllib.parse.unquote(key))

    try:
        body, content_type, content_length = s3_service.download_object(creds, bucket, key)
        filename = key.split("/")[-1]
        filename_encoded = urllib.parse.quote(filename)

        audit_log(request, "DOWNLOAD", bucket=bucket, key=key)

        def iter_content():
            chunk_size = 1024 * 1024  # 1MB chunks
            while True:
                chunk = body.read(chunk_size)
                if not chunk:
                    break
                yield chunk

        return StreamingResponse(
            iter_content(),
            media_type=content_type,
            headers={
                "Content-Disposition": f"attachment; filename*=UTF-8''{filename_encoded}",
                "Content-Length": str(content_length),
            },
        )
    except HTTPException:
        raise
    except Exception as e:
        audit_log(request, "DOWNLOAD", bucket=bucket, key=key, status="FAILURE", detail=str(e))
        raise HTTPException(status_code=500, detail=str(e))


# --- Presigned URL ---

@router.get("/presign")
async def get_presigned_url(
    request: Request,
    bucket: str,
    key: str,
    expiry: int = 3600,
):
    """Generate a pre-signed URL for an object."""
    creds, allowed = _get_creds_and_buckets(request)
    bucket = validate_bucket_name(bucket, allowed)
    key = validate_s3_key(urllib.parse.unquote(key))

    if expiry > 604800:  # Max 7 days
        expiry = 604800

    try:
        url = s3_service.get_presigned_url(creds, bucket, key, expiry)
        audit_log(request, "PRESIGN", bucket=bucket, key=key)
        return JSONResponse({"url": url, "expires_in": expiry})
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# --- Upload ---

@router.post("/upload")
async def upload_object(
    request: Request,
    bucket: str = Form(...),
    prefix: str = Form(""),
    csrf_token: str = Form(...),
    system_metadata: str = Form(""),   # JSON array of {key, value} for system-defined headers
    user_metadata: str = Form(""),     # JSON array of {key, value} for x-amz-meta-* headers
    file: UploadFile = File(...),
):
    """
    Upload a file to S3 with optional system-defined and user-defined metadata.
    system_metadata maps to boto3 ExtraArgs (ContentType, CacheControl, etc.)
    user_metadata    maps to boto3 ExtraArgs['Metadata'] (x-amz-meta-* headers).
    """
    _check_csrf(request, csrf_token)
    creds, allowed = _get_creds_and_buckets(request)
    bucket = validate_bucket_name(bucket, allowed)

    # Validate both metadata payloads (raises HTTPException on bad input)
    sys_args  = validate_system_metadata(system_metadata)   # e.g. {"ContentType": "image/png"}
    user_meta = validate_user_metadata(user_metadata)        # e.g. {"department": "finance"}

    # Sanitize filename and build S3 key
    safe_name = sanitize_filename(file.filename or "unnamed")
    key = f"{prefix.lstrip('/')}{safe_name}" if prefix else safe_name

    content_length = int(request.headers.get("content-length", 0))
    if content_length > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=f"File exceeds max size ({MAX_UPLOAD_MB} MB)")

    try:
        import io
        file_content = await file.read()
        file_size    = len(file_content)
        if file_size > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail=f"File exceeds max size ({MAX_UPLOAD_MB} MB)")

        content_type = file.content_type or "application/octet-stream"

        s3_service.upload_object(
            creds, bucket, key,
            io.BytesIO(file_content),
            content_type, file_size,
            user_metadata=user_meta,
            system_extra_args=sys_args,
        )

        sys_keys  = list(sys_args.keys())
        user_keys = list(user_meta.keys())
        audit_log(
            request, "UPLOAD", bucket=bucket, key=key,
            detail=f"size={file_size} sys={sys_keys} user={user_keys}",
        )
        return JSONResponse({"success": True, "key": key,
                             "message": f"'{safe_name}' uploaded successfully"})
    except HTTPException:
        raise
    except Exception as e:
        audit_log(request, "UPLOAD", bucket=bucket, key=key, status="FAILURE", detail=str(e))
        raise HTTPException(status_code=500, detail=str(e))


# --- Delete ---

@router.delete("/delete")
async def delete_object(
    request: Request,
    bucket: str,
    key: str,
    is_folder: bool = False,
    csrf_token: str = "",
):
    """Delete an object or folder from S3."""
    # CSRF from query param for DELETE
    csrf_from_header = request.headers.get("X-CSRF-Token", csrf_token)
    _check_csrf(request, csrf_from_header)

    creds, allowed = _get_creds_and_buckets(request)
    bucket = validate_bucket_name(bucket, allowed)
    key = validate_s3_key(urllib.parse.unquote(key))

    try:
        if is_folder:
            s3_service.delete_folder(creds, bucket, key)
            audit_log(request, "DELETE_FOLDER", bucket=bucket, key=key)
        else:
            s3_service.delete_object(creds, bucket, key)
            audit_log(request, "DELETE_OBJECT", bucket=bucket, key=key)
        return JSONResponse({"success": True, "message": "Deleted successfully"})
    except HTTPException:
        raise
    except Exception as e:
        audit_log(request, "DELETE", bucket=bucket, key=key, status="FAILURE", detail=str(e))
        raise HTTPException(status_code=500, detail=str(e))


# --- Create Folder ---

@router.post("/folder/create")
async def create_folder(
    request: Request,
    bucket: str = Form(...),
    prefix: str = Form(""),
    folder_name: str = Form(...),
    csrf_token: str = Form(...),
):
    """Create a folder in S3."""
    _check_csrf(request, csrf_token)
    creds, allowed = _get_creds_and_buckets(request)
    bucket = validate_bucket_name(bucket, allowed)

    folder_name = sanitize_filename(folder_name.strip())
    if not folder_name:
        raise HTTPException(status_code=400, detail="Folder name cannot be empty")

    folder_key = f"{prefix.lstrip('/')}{folder_name}/"

    try:
        s3_service.create_folder(creds, bucket, folder_key)
        audit_log(request, "CREATE_FOLDER", bucket=bucket, key=folder_key)
        return JSONResponse({"success": True, "key": folder_key, "message": f"Folder '{folder_name}' created"})
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# --- Rename ---

@router.post("/rename")
async def rename_object(
    request: Request,
    bucket: str = Form(...),
    old_key: str = Form(...),
    new_name: str = Form(...),
    csrf_token: str = Form(...),
):
    """Rename an S3 object."""
    _check_csrf(request, csrf_token)
    creds, allowed = _get_creds_and_buckets(request)
    bucket = validate_bucket_name(bucket, allowed)
    old_key = validate_s3_key(old_key)

    # Build new key preserving prefix
    prefix = old_key.rsplit("/", 1)[0] + "/" if "/" in old_key else ""
    new_name_safe = sanitize_filename(new_name.strip())
    new_key = f"{prefix}{new_name_safe}"
    if old_key.endswith("/"):  # folder rename
        new_key = new_key.rstrip("/") + "/"

    if old_key == new_key:
        raise HTTPException(status_code=400, detail="New name is the same as current name")

    try:
        s3_service.rename_object(creds, bucket, old_key, new_key)
        audit_log(request, "RENAME", bucket=bucket, key=old_key, detail=f"new_key={new_key}")
        return JSONResponse({"success": True, "new_key": new_key, "message": "Renamed successfully"})
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# --- Copy (file or folder) ---

@router.post("/copy")
async def copy_object_route(
    request: Request,
    bucket: str      = Form(...),
    src_key: str     = Form(...),
    dst_prefix: str  = Form(""),      # destination folder prefix (may be empty = root)
    is_folder: bool  = Form(False),
    csrf_token: str  = Form(...),
):
    """
    Copy a file or folder to a destination prefix within the same bucket.
    Preserves metadata, tags, encryption, storage class on every object.
    Folder copies are recursive.
    """
    _check_csrf(request, csrf_token)
    creds, allowed = _get_creds_and_buckets(request)
    bucket  = validate_bucket_name(bucket, allowed)
    src_key = validate_s3_key(src_key)
    if dst_prefix:
        dst_prefix = validate_s3_key(dst_prefix)

    try:
        if is_folder:
            # Ensure trailing slash on both src and dst prefixes
            src_prefix = src_key if src_key.endswith("/") else src_key + "/"
            folder_name = src_prefix.rstrip("/").split("/")[-1]
            dst_full    = (dst_prefix.rstrip("/") + "/" if dst_prefix else "") + folder_name + "/"
            count = s3_service.copy_folder(creds, bucket, src_prefix, bucket, dst_full)
            audit_log(request, "COPY_FOLDER", bucket=bucket, key=src_key,
                      detail=f"dst={dst_full} objects={count}")
            return JSONResponse({"success": True,
                                 "message": f"Folder copied ({count} object{'s' if count != 1 else ''}).",
                                 "dst_key": dst_full})
        else:
            # File copy: place into dst_prefix with original filename
            filename = src_key.split("/")[-1]
            dst_key  = (dst_prefix.rstrip("/") + "/" if dst_prefix else "") + filename
            if dst_key == src_key:
                # Same destination — auto-add copy suffix
                name, _, ext = filename.rpartition(".")
                dst_key = (dst_prefix.rstrip("/") + "/" if dst_prefix else "") + \
                           (f"{name}_copy.{ext}" if ext else f"{filename}_copy")
            s3_service.copy_object(creds, bucket, src_key, bucket, dst_key)
            audit_log(request, "COPY", bucket=bucket, key=src_key, detail=f"dst={dst_key}")
            return JSONResponse({"success": True,
                                 "message": "File copied successfully.",
                                 "dst_key": dst_key})
    except HTTPException:
        raise
    except Exception as e:
        audit_log(request, "COPY", bucket=bucket, key=src_key, status="FAILURE", detail=str(e))
        raise HTTPException(status_code=500, detail=str(e))


# --- Move (file or folder) ---

@router.post("/move")
async def move_object_route(
    request: Request,
    bucket: str      = Form(...),
    src_key: str     = Form(...),
    dst_prefix: str  = Form(""),      # destination folder prefix (may be empty = root)
    is_folder: bool  = Form(False),
    csrf_token: str  = Form(...),
):
    """
    Move a file or folder to a destination prefix within the same bucket.
    Implemented as copy-then-delete; source is removed only after successful copy.
    Preserves metadata, tags, encryption, storage class on every object.
    """
    _check_csrf(request, csrf_token)
    creds, allowed = _get_creds_and_buckets(request)
    bucket  = validate_bucket_name(bucket, allowed)
    src_key = validate_s3_key(src_key)
    if dst_prefix:
        dst_prefix = validate_s3_key(dst_prefix)

    try:
        if is_folder:
            src_prefix  = src_key if src_key.endswith("/") else src_key + "/"
            folder_name = src_prefix.rstrip("/").split("/")[-1]
            dst_full    = (dst_prefix.rstrip("/") + "/" if dst_prefix else "") + folder_name + "/"
            if dst_full == src_prefix:
                raise HTTPException(status_code=400,
                                    detail="Source and destination are the same.")
            count = s3_service.move_folder(creds, bucket, src_prefix, bucket, dst_full)
            audit_log(request, "MOVE_FOLDER", bucket=bucket, key=src_key,
                      detail=f"dst={dst_full} objects={count}")
            return JSONResponse({"success": True,
                                 "message": f"Folder moved ({count} object{'s' if count != 1 else ''}).",
                                 "dst_key": dst_full})
        else:
            filename = src_key.split("/")[-1]
            dst_key  = (dst_prefix.rstrip("/") + "/" if dst_prefix else "") + filename
            if dst_key == src_key:
                raise HTTPException(status_code=400,
                                    detail="Source and destination are the same.")
            s3_service.move_object(creds, bucket, src_key, bucket, dst_key)
            audit_log(request, "MOVE", bucket=bucket, key=src_key, detail=f"dst={dst_key}")
            return JSONResponse({"success": True,
                                 "message": "File moved successfully.",
                                 "dst_key": dst_key})
    except HTTPException:
        raise
    except Exception as e:
        audit_log(request, "MOVE", bucket=bucket, key=src_key, status="FAILURE", detail=str(e))
        raise HTTPException(status_code=500, detail=str(e))


# --- List folders (destination picker) ---

@router.get("/folders")
async def list_folders_route(
    request: Request,
    bucket: str,
    prefix: str = "",
):
    """
    Return immediate sub-folders under prefix.
    Used by the Copy/Move destination-picker modal to build a folder tree.
    """
    creds, allowed = _get_creds_and_buckets(request)
    bucket = validate_bucket_name(bucket, allowed)
    if prefix:
        prefix = validate_s3_key(prefix)

    try:
        folders = s3_service.list_folders(creds, bucket, prefix)
        return JSONResponse({"folders": folders, "prefix": prefix})
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# --- Metadata GET (object properties + structured system/user metadata) ---

@router.get("/metadata")
async def get_metadata(
    request: Request,
    bucket: str,
    key: str,
):
    """
    Return object properties AND split metadata (system-defined + user-defined).
    Used by both the View Info panel and the new Edit Metadata modal.
    """
    creds, allowed = _get_creds_and_buckets(request)
    bucket = validate_bucket_name(bucket, allowed)
    key    = validate_s3_key(urllib.parse.unquote(key))

    try:
        data = metadata_service.get_full_object_metadata(creds, bucket, key)
        return JSONResponse(data)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# --- Metadata PUT (replace via CopyObject MetadataDirective=REPLACE) --------

@router.put("/metadata")
async def update_metadata(
    request: Request,
    bucket: str         = Form(...),
    key: str            = Form(...),
    csrf_token: str     = Form(...),
    system_metadata: str = Form(""),  # JSON array [{key, value}, …]
    user_metadata: str  = Form(""),   # JSON array [{key, value}, …]
):
    """
    Replace S3 object metadata using CopyObject with MetadataDirective=REPLACE.
    Object data is preserved; only metadata headers change.

    Both system_metadata and user_metadata are validated server-side before
    any AWS call is made. Credentials are never exposed to the frontend.
    """
    _check_csrf(request, csrf_token)
    creds, allowed = _get_creds_and_buckets(request)
    bucket = validate_bucket_name(bucket, allowed)
    key    = validate_s3_key(urllib.parse.unquote(key))

    # Server-side validation (raises HTTPException on bad input)
    sys_args  = validate_system_metadata(system_metadata)
    user_meta = validate_user_metadata(user_metadata)

    try:
        result = metadata_service.replace_object_metadata(
            creds, bucket, key,
            system_extra_args=sys_args,
            user_metadata=user_meta,
        )
        audit_log(
            request, "UPDATE_METADATA", bucket=bucket, key=key,
            detail=f"sys_keys={result['sys_keys']} user_keys={result['user_keys']}",
        )
        return JSONResponse({
            "success": True,
            "message": "Metadata updated successfully.",
            "key":     key,
        })
    except HTTPException:
        raise
    except Exception as e:
        audit_log(
            request, "UPDATE_METADATA", bucket=bucket, key=key,
            status="FAILURE", detail=str(e),
        )
        raise HTTPException(status_code=500, detail=str(e))