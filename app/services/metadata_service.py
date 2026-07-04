"""
DriveX Metadata Service
-----------------------
Handles reading and replacing S3 object metadata.

AWS IMPORTANT: S3 does NOT support in-place metadata updates.
The only supported pattern is CopyObject with MetadataDirective=REPLACE,
copying the object onto itself with the new metadata payload.
This preserves object data, storage class, encryption, tags, and ACLs.
"""

from typing import Optional

import botocore.exceptions

from app.services.s3_service import get_s3_client, _human_size, _raise_s3_error

# ── Canonical mapping: UI label → boto3 head_object response field ──────────
# Used to extract current system-defined values from HeadObject.
SYSTEM_KEY_TO_HEAD_FIELD = {
    "Content-Type":              "ContentType",
    "Cache-Control":             "CacheControl",
    "Content-Disposition":       "ContentDisposition",
    "Content-Encoding":          "ContentEncoding",
    "Content-Language":          "ContentLanguage",
    "Expires":                   "Expires",
    "Website-Redirect-Location": "WebsiteRedirectLocation",
}

# boto3 CopyObject / head_object key → UI label (reverse mapping)
HEAD_FIELD_TO_SYSTEM_KEY = {v: k for k, v in SYSTEM_KEY_TO_HEAD_FIELD.items()}

# Which boto3 CopyObject kwargs correspond to each system key
SYSTEM_KEY_TO_BOTO3_COPY = {
    "Content-Type":              "ContentType",
    "Cache-Control":             "CacheControl",
    "Content-Disposition":       "ContentDisposition",
    "Content-Encoding":          "ContentEncoding",
    "Content-Language":          "ContentLanguage",
    "Expires":                   "Expires",
    "Website-Redirect-Location": "WebsiteRedirectLocation",
}


def get_full_object_metadata(creds: dict, bucket: str, key: str) -> dict:
    """
    Retrieve complete object metadata via HeadObject.

    Returns a structured dict with:
      - Object properties (read-only display fields)
      - system_metadata: list of {key, value} for the Edit Metadata UI
      - user_metadata:   list of {key, value} for the Edit Metadata UI

    Splits metadata into system-defined (HTTP headers on the object) and
    user-defined (x-amz-meta-* headers stored in Metadata dict).
    """
    s3 = get_s3_client(creds)
    try:
        head = s3.head_object(Bucket=bucket, Key=key)
    except botocore.exceptions.ClientError as e:
        _raise_s3_error(e, bucket, key)

    # ── Object properties (read-only in UI) ───────────────────────────────
    size = head.get("ContentLength", 0)
    last_modified = head.get("LastModified")

    # Expires may be a datetime or a string depending on boto3 version
    expires_raw = head.get("Expires")
    if expires_raw and hasattr(expires_raw, "strftime"):
        expires_str = expires_raw.strftime("%a, %d %b %Y %H:%M:%S GMT")
    else:
        expires_str = str(expires_raw) if expires_raw else ""

    props = {
        "key":            key,
        "name":           key.split("/")[-1] if "/" in key else key,
        "bucket":         bucket,
        "size":           size,
        "size_human":     _human_size(size),
        "last_modified":  last_modified.isoformat() if last_modified else "",
        "last_modified_human": last_modified.strftime("%b %d, %Y %H:%M UTC") if last_modified else "",
        "storage_class":  head.get("StorageClass", "STANDARD"),
        "content_type":   head.get("ContentType", "application/octet-stream"),
        "etag":           head.get("ETag", "").strip('"'),
        "encryption":     head.get("ServerSideEncryption", ""),
        "version_id":     head.get("VersionId", ""),
    }

    # ── System-defined metadata ───────────────────────────────────────────
    # Extract the values we know how to map from HeadObject response.
    system_metadata = []
    for ui_key, boto_field in SYSTEM_KEY_TO_HEAD_FIELD.items():
        raw = head.get(boto_field)
        if raw:
            # Normalise datetime Expires to string
            if boto_field == "Expires" and hasattr(raw, "strftime"):
                raw = raw.strftime("%a, %d %b %Y %H:%M:%S GMT")
            system_metadata.append({"key": ui_key, "value": str(raw)})

    # ── User-defined metadata ─────────────────────────────────────────────
    # boto3 returns these as a plain dict with lowercased keys.
    user_meta_raw = head.get("Metadata", {})
    user_metadata = [
        {"key": k, "value": v}
        for k, v in user_meta_raw.items()
    ]

    return {
        **props,
        "system_metadata": system_metadata,
        "user_metadata":   user_metadata,
    }


def replace_object_metadata(
    creds: dict,
    bucket: str,
    key: str,
    system_extra_args: dict,   # boto3 kwargs: ContentType, CacheControl, …
    user_metadata: dict,       # clean {key: value} for x-amz-meta-*
) -> dict:
    """
    Replace S3 object metadata using CopyObject with MetadataDirective=REPLACE.

    Steps (transparent to caller):
      1. HeadObject  — fetch current storage class, encryption, tags, ACL info
      2. CopyObject  — same src/dst, MetadataDirective=REPLACE, new metadata
      3. Preserve storage class, SSE, object tagging (TaggingDirective=COPY)

    Raises HTTPException on any AWS error.
    Returns the updated metadata for confirmation.
    """
    s3 = get_s3_client(creds)

    # ── Step 1: fetch current object attributes we must preserve ──────────
    try:
        head = s3.head_object(Bucket=bucket, Key=key)
    except botocore.exceptions.ClientError as e:
        _raise_s3_error(e, bucket, key)

    storage_class     = head.get("StorageClass", "STANDARD")
    sse               = head.get("ServerSideEncryption")
    sse_kms_key_id    = head.get("SSEKMSKeyId")

    # ContentType is mandatory for CopyObject; use existing if not overridden
    effective_content_type = system_extra_args.pop(
        "ContentType",
        head.get("ContentType", "application/octet-stream")
    )

    # ── Step 2: build CopyObject kwargs ───────────────────────────────────
    copy_kwargs: dict = {
        "Bucket":            bucket,
        "Key":               key,
        "CopySource":        {"Bucket": bucket, "Key": key},
        "MetadataDirective": "REPLACE",      # ← tells S3 to use new metadata
        "TaggingDirective":  "COPY",          # ← preserve existing tags
        "ContentType":       effective_content_type,
        "StorageClass":      storage_class,
    }

    # Attach remaining system-defined args (CacheControl, ContentDisposition…)
    copy_kwargs.update(system_extra_args)

    # Attach user-defined metadata (will be stored as x-amz-meta-* headers)
    if user_metadata:
        copy_kwargs["Metadata"] = user_metadata

    # Preserve server-side encryption
    if sse:
        copy_kwargs["ServerSideEncryption"] = sse
    if sse_kms_key_id:
        copy_kwargs["SSEKMSKeyId"] = sse_kms_key_id

    # ── Step 3: execute the copy ───────────────────────────────────────────
    try:
        s3.copy_object(**copy_kwargs)
    except botocore.exceptions.ClientError as e:
        _raise_s3_error(e, bucket, key)

    # Return a summary for audit logging and API response
    return {
        "key":          key,
        "bucket":       bucket,
        "content_type": effective_content_type,
        "sys_keys":     list(copy_kwargs.keys()),
        "user_keys":    list(user_metadata.keys()),
    }