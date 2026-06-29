"""
DriveX S3 Service
All AWS S3 operations using per-session boto3 clients.
Credentials are never logged or stored permanently.
"""
import io
import math
import os
from typing import Optional

import boto3
import botocore.exceptions

# Multipart upload threshold: 100MB
MULTIPART_THRESHOLD = 100 * 1024 * 1024
MULTIPART_CHUNK_SIZE = 50 * 1024 * 1024  # 50MB chunks


def get_s3_client(creds: dict):
    """Create a boto3 S3 client from session credentials."""
    session = boto3.Session(
        aws_access_key_id=creds["access_key"],
        aws_secret_access_key=creds["secret_key"],
        region_name=creds["region"],
    )
    return session.client("s3")


def get_sts_client(creds: dict):
    """Create a boto3 STS client for identity verification."""
    session = boto3.Session(
        aws_access_key_id=creds["access_key"],
        aws_secret_access_key=creds["secret_key"],
        region_name=creds["region"],
    )
    return session.client("sts")


def validate_credentials(creds: dict) -> dict:
    """
    Validate AWS credentials via STS GetCallerIdentity.
    Returns identity info on success, raises on failure.
    """
    try:
        sts = get_sts_client(creds)
        identity = sts.get_caller_identity()
        return {
            "account": identity.get("Account", "unknown"),
            "arn": identity.get("Arn", "unknown"),
            "user_id": identity.get("UserId", "unknown"),
        }
    except botocore.exceptions.ClientError as e:
        code = e.response["Error"]["Code"]
        if code in ("InvalidClientTokenId", "AuthFailure"):
            raise ValueError("Invalid AWS Access Key or Secret Key.")
        if code == "AccessDenied":
            raise ValueError("Credentials valid but sts:GetCallerIdentity is denied. Check IAM permissions.")
        raise ValueError(f"AWS error: {e.response['Error']['Message']}")
    except botocore.exceptions.NoCredentialsError:
        raise ValueError("No credentials provided.")
    except botocore.exceptions.EndpointResolutionError:
        raise ValueError("Cannot reach AWS endpoint. Check region and network.")
    except Exception as e:
        raise ValueError(f"Credential validation failed: {str(e)}")


def validate_bucket_access(creds: dict, bucket_name: str) -> dict:
    """
    Validate that the credentials can access the given bucket.
    Returns bucket metadata on success.
    """
    s3 = get_s3_client(creds)
    try:
        # Check bucket exists and is accessible
        s3.head_bucket(Bucket=bucket_name)
        # Get bucket region
        location = s3.get_bucket_location(Bucket=bucket_name)
        region = location.get("LocationConstraint") or "us-east-1"
        return {"bucket": bucket_name, "region": region, "accessible": True}
    except botocore.exceptions.ClientError as e:
        code = e.response["Error"]["Code"]
        http_code = e.response["ResponseMetadata"]["HTTPStatusCode"]
        if http_code == 404:
            raise ValueError(f"Bucket '{bucket_name}' not found.")
        if http_code == 403:
            raise ValueError(f"Access denied to bucket '{bucket_name}'. Check IAM permissions.")
        if code == "NoSuchBucket":
            raise ValueError(f"Bucket '{bucket_name}' does not exist.")
        raise ValueError(f"Cannot access bucket '{bucket_name}': {e.response['Error']['Message']}")
    except Exception as e:
        raise ValueError(f"Bucket validation failed for '{bucket_name}': {str(e)}")


def list_objects(creds: dict, bucket: str, prefix: str = "", delimiter: str = "/") -> dict:
    """
    List objects and common prefixes (folders) in an S3 bucket/prefix.
    Returns structured file/folder data.
    """
    s3 = get_s3_client(creds)
    folders = []
    files = []

    paginator = s3.get_paginator("list_objects_v2")
    pages = paginator.paginate(
        Bucket=bucket,
        Prefix=prefix,
        Delimiter=delimiter,
    )

    try:
        for page in pages:
            # Folders (common prefixes)
            for cp in page.get("CommonPrefixes", []):
                folder_key = cp["Prefix"]
                folder_name = folder_key[len(prefix):].rstrip("/")
                if folder_name:
                    folders.append({
                        "key": folder_key,
                        "name": folder_name,
                        "type": "folder",
                        "size": None,
                        "last_modified": None,
                        "size_human": "-",
                        "etag": None,
                    })

            # Files (objects)
            for obj in page.get("Contents", []):
                obj_key = obj["Key"]
                if obj_key == prefix:
                    continue  # Skip the folder placeholder itself
                name = obj_key[len(prefix):]
                if not name:
                    continue
                size = obj.get("Size", 0)
                files.append({
                    "key": obj_key,
                    "name": name,
                    "type": _guess_type(name),
                    "size": size,
                    "size_human": _human_size(size),
                    "last_modified": obj["LastModified"].isoformat(),
                    "last_modified_human": obj["LastModified"].strftime("%b %d, %Y %H:%M"),
                    "etag": obj.get("ETag", "").strip('"'),
                    "storage_class": obj.get("StorageClass", "STANDARD"),
                })
    except botocore.exceptions.ClientError as e:
        _raise_s3_error(e, bucket)

    return {
        "prefix": prefix,
        "folders": folders,
        "files": files,
        "total_items": len(folders) + len(files),
    }


def get_presigned_url(creds: dict, bucket: str, key: str, expiry: int = 3600) -> str:
    """Generate a pre-signed URL for an S3 object."""
    s3 = get_s3_client(creds)
    try:
        url = s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=expiry,
        )
        return url
    except botocore.exceptions.ClientError as e:
        _raise_s3_error(e, bucket, key)


def download_object(creds: dict, bucket: str, key: str):
    """Stream an S3 object for download. Returns (streaming_body, content_type, content_length)."""
    s3 = get_s3_client(creds)
    try:
        response = s3.get_object(Bucket=bucket, Key=key)
        return (
            response["Body"],
            response.get("ContentType", "application/octet-stream"),
            response.get("ContentLength", 0),
        )
    except botocore.exceptions.ClientError as e:
        _raise_s3_error(e, bucket, key)


def upload_object(creds: dict, bucket: str, key: str, file_obj, content_type: str = "application/octet-stream", file_size: int = 0):
    """
    Upload a file to S3. Uses multipart upload for files > MULTIPART_THRESHOLD.
    """
    s3 = get_s3_client(creds)
    try:
        if file_size > MULTIPART_THRESHOLD:
            _multipart_upload(s3, bucket, key, file_obj, content_type)
        else:
            s3.upload_fileobj(
                file_obj,
                bucket,
                key,
                ExtraArgs={"ContentType": content_type},
            )
    except botocore.exceptions.ClientError as e:
        _raise_s3_error(e, bucket, key)


def _multipart_upload(s3_client, bucket: str, key: str, file_obj, content_type: str):
    """Perform multipart upload for large files."""
    mpu = s3_client.create_multipart_upload(Bucket=bucket, Key=key, ContentType=content_type)
    upload_id = mpu["UploadId"]
    parts = []
    part_number = 1

    try:
        while True:
            chunk = file_obj.read(MULTIPART_CHUNK_SIZE)
            if not chunk:
                break
            part = s3_client.upload_part(
                Bucket=bucket,
                Key=key,
                PartNumber=part_number,
                UploadId=upload_id,
                Body=chunk,
            )
            parts.append({"PartNumber": part_number, "ETag": part["ETag"]})
            part_number += 1

        s3_client.complete_multipart_upload(
            Bucket=bucket,
            Key=key,
            UploadId=upload_id,
            MultipartUpload={"Parts": parts},
        )
    except Exception as e:
        s3_client.abort_multipart_upload(Bucket=bucket, Key=key, UploadId=upload_id)
        raise e


def delete_object(creds: dict, bucket: str, key: str):
    """Delete a single S3 object."""
    s3 = get_s3_client(creds)
    try:
        s3.delete_object(Bucket=bucket, Key=key)
    except botocore.exceptions.ClientError as e:
        _raise_s3_error(e, bucket, key)


def delete_folder(creds: dict, bucket: str, prefix: str):
    """Delete all objects under a prefix (folder)."""
    s3 = get_s3_client(creds)
    paginator = s3.get_paginator("list_objects_v2")
    pages = paginator.paginate(Bucket=bucket, Prefix=prefix)
    delete_keys = []

    try:
        for page in pages:
            for obj in page.get("Contents", []):
                delete_keys.append({"Key": obj["Key"]})
                if len(delete_keys) >= 1000:
                    s3.delete_objects(Bucket=bucket, Delete={"Objects": delete_keys})
                    delete_keys = []
        if delete_keys:
            s3.delete_objects(Bucket=bucket, Delete={"Objects": delete_keys})
    except botocore.exceptions.ClientError as e:
        _raise_s3_error(e, bucket, prefix)


def create_folder(creds: dict, bucket: str, prefix: str):
    """Create a 'folder' in S3 by writing an empty object with trailing slash."""
    s3 = get_s3_client(creds)
    folder_key = prefix if prefix.endswith("/") else prefix + "/"
    try:
        s3.put_object(Bucket=bucket, Key=folder_key, Body=b"")
    except botocore.exceptions.ClientError as e:
        _raise_s3_error(e, bucket, folder_key)


def copy_object(creds: dict, src_bucket: str, src_key: str, dst_bucket: str, dst_key: str):
    """Copy an S3 object from source to destination."""
    s3 = get_s3_client(creds)
    copy_source = {"Bucket": src_bucket, "Key": src_key}
    try:
        s3.copy_object(CopySource=copy_source, Bucket=dst_bucket, Key=dst_key)
    except botocore.exceptions.ClientError as e:
        _raise_s3_error(e, src_bucket, src_key)


def move_object(creds: dict, src_bucket: str, src_key: str, dst_bucket: str, dst_key: str):
    """Move (copy + delete) an S3 object."""
    copy_object(creds, src_bucket, src_key, dst_bucket, dst_key)
    delete_object(creds, src_bucket, src_key)


def rename_object(creds: dict, bucket: str, old_key: str, new_key: str):
    """Rename an S3 object (copy to new key, delete old)."""
    move_object(creds, bucket, old_key, bucket, new_key)


def get_object_metadata(creds: dict, bucket: str, key: str) -> dict:
    """Get metadata for a specific S3 object."""
    s3 = get_s3_client(creds)
    try:
        head = s3.head_object(Bucket=bucket, Key=key)
        return {
            "key": key,
            "size": head.get("ContentLength", 0),
            "size_human": _human_size(head.get("ContentLength", 0)),
            "last_modified": head["LastModified"].isoformat(),
            "content_type": head.get("ContentType", "application/octet-stream"),
            "etag": head.get("ETag", "").strip('"'),
            "storage_class": head.get("StorageClass", "STANDARD"),
        }
    except botocore.exceptions.ClientError as e:
        _raise_s3_error(e, bucket, key)


def search_objects(creds: dict, bucket: str, query: str, prefix: str = "") -> list:
    """Search for objects in a bucket matching a query string."""
    s3 = get_s3_client(creds)
    results = []
    query_lower = query.lower()
    paginator = s3.get_paginator("list_objects_v2")
    pages = paginator.paginate(Bucket=bucket, Prefix=prefix)

    try:
        for page in pages:
            for obj in page.get("Contents", []):
                key = obj["Key"]
                name = key.split("/")[-1] if "/" in key else key
                if query_lower in name.lower():
                    size = obj.get("Size", 0)
                    results.append({
                        "key": key,
                        "name": name,
                        "bucket": bucket,
                        "type": _guess_type(name),
                        "size": size,
                        "size_human": _human_size(size),
                        "last_modified_human": obj["LastModified"].strftime("%b %d, %Y %H:%M"),
                    })
            if len(results) >= 200:
                break
    except botocore.exceptions.ClientError as e:
        _raise_s3_error(e, bucket)

    return results


# --- Helper functions ---

def _human_size(size: int) -> str:
    """Convert bytes to human-readable string."""
    if size == 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB"]
    i = int(math.floor(math.log(size, 1024)))
    i = min(i, len(units) - 1)
    p = math.pow(1024, i)
    s = round(size / p, 2)
    return f"{s} {units[i]}"


def _guess_type(filename: str) -> str:
    """Guess the file type category from extension."""
    ext = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    type_map = {
        "images": {"jpg", "jpeg", "png", "gif", "webp", "svg", "bmp", "ico", "tiff"},
        "video": {"mp4", "avi", "mov", "mkv", "webm", "flv"},
        "audio": {"mp3", "wav", "flac", "aac", "ogg", "m4a"},
        "document": {"pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "odt"},
        "text": {"txt", "md", "csv", "log", "rst", "xml", "html", "htm"},
        "code": {"py", "js", "ts", "java", "cpp", "c", "go", "rs", "rb", "php", "sh", "yaml", "yml", "json", "tf"},
        "archive": {"zip", "tar", "gz", "bz2", "7z", "rar", "xz"},
    }
    for type_name, extensions in type_map.items():
        if ext in extensions:
            return type_name
    return "file"


def _raise_s3_error(error: botocore.exceptions.ClientError, bucket: str = None, key: str = None):
    """Convert boto3 ClientError to a friendly HTTP exception."""
    from fastapi import HTTPException
    code = error.response["Error"]["Code"]
    message = error.response["Error"]["Message"]
    http_status = error.response["ResponseMetadata"]["HTTPStatusCode"]

    friendly = {
        "NoSuchBucket": f"Bucket not found.",
        "NoSuchKey": f"File not found.",
        "AccessDenied": "Access denied. Check your IAM permissions.",
        "InvalidAccessKeyId": "Invalid AWS Access Key.",
        "SignatureDoesNotMatch": "Invalid AWS Secret Key.",
        "AllAccessDisabled": "Access to this bucket has been disabled.",
        "BucketNotEmpty": "Bucket is not empty.",
    }
    detail = friendly.get(code, f"S3 error ({code}): {message}")
    raise HTTPException(status_code=http_status if http_status >= 400 else 500, detail=detail)
