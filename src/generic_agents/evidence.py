"""Workspace-scoped immutable file evidence and explicit per-model cloud consent."""
import base64
import hashlib
import io
import os
import stat
import tempfile
import warnings
from pathlib import Path
from PIL import Image, UnidentifiedImageError
from fastapi import HTTPException
from .models import Evidence
from .config import settings
from .services import scoped, published, audit
from .security import require


def inspect_content(content, media_type):
    if not content or len(content) > settings().max_upload_bytes:
        raise HTTPException(413, "文件为空或超过大小上限")
    if media_type == "text/plain":
        try:
            text = content.decode("utf-8-sig")
        except UnicodeError:
            raise HTTPException(415, "文本文件必须为UTF-8编码")
        if "\x00" in text or len(content) > 20000:
            raise HTTPException(415, "文本文件含二进制内容或超过20KB上限")
        return {"modality": "text", "characters": len(text)}
    if media_type not in {"image/png", "image/jpeg"}:
        raise HTTPException(415, "当前支持PNG、JPEG和UTF-8文本；其他格式需要注册处理器")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(content)) as image:
                expected = {"image/png": "PNG", "image/jpeg": "JPEG"}[media_type]
                if image.format != expected or image.width * image.height > 16000000:
                    raise ValueError()
                dimensions = {"width": image.width, "height": image.height, "modality": "image"}
                image.verify()
        return dimensions
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise HTTPException(415, "图片损坏、格式不匹配或像素超过上限")


def save_evidence(db, p, content, media_type, cloud_model_id=None):
    require(p, "admin", "operator")
    metadata = inspect_content(content, media_type)
    if cloud_model_id:
        model = published(db, p, cloud_model_id, "foundation_model")
        if not model.data["data_policy"].get("cloud_allowed"):
            raise HTTPException(409, "模型尚未批准数据出域")
        if metadata["modality"] == "image" and not model.data["execution"].get("supports_images"):
            raise HTTPException(409, "所选模型未声明图像处理能力")
    return save_immutable(db, p, content, media_type, metadata, cloud_model_id)


def _existing_generated_blob(p, content, sha, directory_fd):
    """Server-only metadata registration; no path creation or file writes."""
    from uuid import UUID
    def need(ok):
        if not ok: raise HTTPException(409, '内部技术文件目录或固定内容无法核对，未登记为已保存成果')
    need(type(directory_fd) is int and directory_fd >= 0 and type(content) is bytes
        and 0 < len(content) <= 20 * 1024 * 1024 and str(UUID(p.workspace_id)) == p.workspace_id)
    directory = os.fstat(directory_fd)
    path = settings().storage_dir / p.workspace_id
    current = path.lstat()
    identity = lambda item: (item.st_dev, item.st_ino)
    need(stat.S_ISDIR(directory.st_mode) and stat.S_ISDIR(current.st_mode)
        and identity(directory) == identity(current))
    fd = os.open(sha + '.bin', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
    try:
        before = os.fstat(fd)
        need(stat.S_ISREG(before.st_mode) and before.st_size == len(content))
        offset, actual = 0, hashlib.sha256()
        while True:
            part = os.read(fd, min(65536, len(content) + 1 - offset))
            if not part: break
            need(offset + len(part) <= len(content) and part == content[offset:offset + len(part)])
            offset += len(part); actual.update(part)
        after = os.fstat(fd)
        stable = lambda item: (item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns, item.st_ctime_ns)
        need(offset == len(content) and actual.hexdigest() == sha and stable(before) == stable(after)
            and identity(path.lstat()) == identity(directory))
    finally:
        os.close(fd)


def save_immutable(db, p, content, media_type, metadata, cloud_model_id=None, *, existing_directory_fd=None):
    """Private storage primitive. Callers must validate their modality first."""
    sha = hashlib.sha256(content).hexdigest()
    storage_key = p.workspace_id + "/" + sha + ".bin"
    if existing_directory_fd is not None:
        _existing_generated_blob(p, content, sha, existing_directory_fd)
    else:
        path = settings().storage_dir / storage_key
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not path.exists():
            fd, temporary = tempfile.mkstemp(dir=path.parent)
            try:
                with os.fdopen(fd, "wb") as file:
                    file.write(content); file.flush(); os.fsync(file.fileno())
                os.replace(temporary, path)
            finally:
                Path(temporary).unlink(missing_ok=True)
    row = Evidence(workspace_id=p.workspace_id, sha256=sha, media_type=media_type,
        size_bytes=len(content), storage_key=storage_key, data={**metadata,
        "cloud_model_ids": [cloud_model_id] if cloud_model_id else [], "uploaded_by": p.user_id})
    db.add(row); db.flush()
    audit(db, p, "evidence.upload", row.id, {"media_type": media_type, "sha256": sha,
        "cloud_model_id": cloud_model_id, "consent": bool(cloud_model_id)})
    return row


def save_video_evidence(db, p, content, media_type):
    require(p, 'admin', 'operator')
    if not content or len(content) > settings().max_upload_bytes:
        raise HTTPException(413, '视频为空或超过20MB上限')
    if media_type != 'video/mp4' or len(content) < 16 or content[4:8] != b'ftyp':
        raise HTTPException(415, '当前只接收MP4视频；解码检查在本地后台完成')
    # Header inspection is not a successful decode. Keep the original if decode fails.
    return save_immutable(db, p, content, media_type, {'modality': 'video', 'decode_status': 'pending'})


def evidence_bytes(db, p, evidence_id):
    row = scoped(db, Evidence, evidence_id, p)
    expected = p.workspace_id + "/" + row.sha256 + ".bin"
    if row.storage_key != expected:
        raise HTTPException(409, "证据存储路径不一致")
    path = settings().storage_dir / expected
    try:
        content = path.read_bytes()
    except OSError:
        raise HTTPException(409, "证据文件缺失，需要恢复存储")
    if hashlib.sha256(content).hexdigest() != row.sha256:
        raise HTTPException(409, "证据文件校验失败，不能继续处理")
    return row, content


def cloud_parts(db, p, evidence_ids, model):
    if not evidence_ids or len(evidence_ids) > 3:
        raise HTTPException(422, "文件处理需要1至3项证据")
    parts = []
    for evidence_id in evidence_ids:
        # Reject unsupported/unconsented originals before loading a potentially large raster.
        row = scoped(db, Evidence, evidence_id, p)
        if model.id not in row.data.get("cloud_model_ids", []):
            raise HTTPException(403, "该文件没有授权给此模型，未发送任何文件内容")
        if row.media_type not in {'image/png','image/jpeg','text/plain'}:
            raise HTTPException(409,'此原件格式尚无已验证的云输入处理器；未发送原件')
        row, content = evidence_bytes(db, p, evidence_id)
        if row.media_type == "text/plain":
            parts.append({"type": "text", "text": content.decode("utf-8-sig")})
        else:
            if model.data["execution"].get("provider") != "qwen" or not model.data["execution"].get("supports_images"):
                raise HTTPException(409, "此模型没有已启用的图像适配器")
            # Inference preprocessing strips EXIF and bounds image size; originals remain immutable.
            with Image.open(io.BytesIO(content)) as image:
                image.thumbnail((1024, 1024))
                rgb = image.convert("RGB")
                buffer = io.BytesIO(); rgb.save(buffer, format="JPEG", quality=85)
            url = "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode()
            parts.append({"type": "image_url", "image_url": {"url": url}})
    return parts


def authorize_cloud(db, p, evidence_id, model_id, expected_version, reason):
    require(p, "admin", "operator")
    from .services import check_version
    row = scoped(db, Evidence, evidence_id, p, True); check_version(row, expected_version)
    if row.data.get('modality') == 'video':
        raise HTTPException(409, '视频没有云端发送适配器，原件仅在本地处理')
    model = published(db, p, model_id, "foundation_model")
    if not model.data["data_policy"].get("cloud_allowed"):
        raise HTTPException(409, "所选模型未批准数据出域")
    if row.data["modality"] == "image" and not model.data["execution"].get("supports_images"):
        raise HTTPException(409, "所选模型不支持图像")
    row.data = {**row.data, "cloud_model_ids": sorted(set(row.data.get("cloud_model_ids", []) + [model_id]))}
    row.version += 1
    audit(db, p, "evidence.cloud_authorize", row.id, {"model_id": model_id, "reason": reason,
          "sha256": row.sha256, "version": row.version})
    return row
