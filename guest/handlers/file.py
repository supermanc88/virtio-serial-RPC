# -*- coding: utf-8 -*-
"""
文件操作处理器
处理文件上传、下载和信息查询相关的API
支持分块上传/下载大文件
"""

import os
import stat
import logging
import hashlib
import tempfile
import shutil
from typing import Dict, Any, List
import pwd
import grp

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from common.errors import ErrorCode, RPCError
from common.utils import (
    encode_base64, decode_base64, calculate_md5, calculate_file_md5,
    normalize_path, is_path_safe
)

logger = logging.getLogger(__name__)

# 默认允许访问的路径
DEFAULT_ALLOWED_PATHS = [
    '/tmp/',
    '/var/log/',
    '/home/',
    '/opt/',
]

# 禁止访问的路径
FORBIDDEN_PATHS = [
    '/etc/shadow',
    '/etc/sudoers',
    '/root/.ssh/',
    '/proc/',
    '/sys/',
]

# 分块上传会话存储
# key: session_id, value: {path, temp_file, size, md5_hash, created_time}
_upload_sessions: Dict[str, Dict[str, Any]] = {}

# 默认分块大小 (2MB)
DEFAULT_CHUNK_SIZE = 2 * 1024 * 1024


def check_path_access(path: str, config: Dict, write: bool = False) -> str:
    """
    检查路径访问权限
    
    Args:
        path: 要检查的路径
        config: 配置字典
        write: 是否需要写权限
    
    Returns:
        规范化后的路径
    
    Raises:
        RPCError: 如果路径不允许访问
    """
    normalized = normalize_path(path)
    
    # 检查禁止访问的路径
    for forbidden in FORBIDDEN_PATHS:
        if normalized.startswith(forbidden) or normalized == forbidden.rstrip('/'):
            raise RPCError(ErrorCode.PERMISSION_DENIED, f"Access denied: {path}")
    
    # 获取允许的路径列表
    security_config = config.get("security", {})
    allowed_paths = security_config.get("allowed_paths", DEFAULT_ALLOWED_PATHS)
    
    # 检查是否在允许的路径中
    if allowed_paths:  # 如果配置了允许路径，则检查
        if not is_path_safe(normalized, allowed_paths):
            raise RPCError(ErrorCode.PERMISSION_DENIED, 
                          f"Path not in allowed list: {path}")
    
    return normalized


def handle_file_upload(context: Dict[str, Any]) -> Dict[str, Any]:
    """
    处理文件上传请求（Host -> Guest）
    
    Args:
        context: 请求上下文，body中应包含:
            - path: 目标文件路径
            - content: Base64编码的文件内容
            - mode: 文件权限（可选，如"0644"）
            - owner: 文件所有者（可选）
            - group: 文件组（可选）
            - overwrite: 是否覆盖已存在的文件（默认True）
    
    Returns:
        上传结果字典
    """
    body = context.get("body", {})
    config = context.get("config", {})
    
    # 获取参数
    path = body.get("path")
    content_b64 = body.get("content")
    mode = body.get("mode", "0644")
    owner = body.get("owner")
    group = body.get("group")
    overwrite = body.get("overwrite", True)
    
    if not path:
        raise RPCError(ErrorCode.MISSING_REQUIRED, "Missing required parameter: path")
    if content_b64 is None:
        raise RPCError(ErrorCode.MISSING_REQUIRED, "Missing required parameter: content")
    
    # 检查路径权限
    normalized_path = check_path_access(path, config, write=True)
    
    # 检查文件是否存在
    if os.path.exists(normalized_path) and not overwrite:
        raise RPCError(ErrorCode.PERMISSION_DENIED, f"File already exists: {path}")
    
    # 解码内容
    try:
        content = decode_base64(content_b64)
    except Exception as e:
        raise RPCError(ErrorCode.INVALID_PARAMS, f"Invalid base64 content: {e}")
    
    # 确保目录存在
    dir_path = os.path.dirname(normalized_path)
    if dir_path and not os.path.exists(dir_path):
        try:
            os.makedirs(dir_path, mode=0o755)
        except OSError as e:
            raise RPCError(ErrorCode.PERMISSION_DENIED, f"Failed to create directory: {e}")
    
    # 写入文件
    try:
        with open(normalized_path, 'wb') as f:
            f.write(content)
    except IOError as e:
        raise RPCError(ErrorCode.PERMISSION_DENIED, f"Failed to write file: {e}")
    
    # 设置权限
    try:
        if mode:
            os.chmod(normalized_path, int(mode, 8))
    except (ValueError, OSError) as e:
        logger.warning(f"Failed to set file mode: {e}")
    
    # 设置所有者
    try:
        uid = -1
        gid = -1
        if owner:
            try:
                uid = pwd.getpwnam(owner).pw_uid
            except KeyError:
                logger.warning(f"User not found: {owner}")
        if group:
            try:
                gid = grp.getgrnam(group).gr_gid
            except KeyError:
                logger.warning(f"Group not found: {group}")
        if uid != -1 or gid != -1:
            os.chown(normalized_path, uid, gid)
    except OSError as e:
        logger.warning(f"Failed to set file ownership: {e}")
    
    # 计算MD5
    md5 = calculate_md5(content)
    
    return {
        "path": normalized_path,
        "size": len(content),
        "md5": md5,
    }


def handle_file_download(context: Dict[str, Any]) -> Dict[str, Any]:
    """
    处理文件下载请求（Guest -> Host）
    
    Args:
        context: 请求上下文，body中应包含:
            - path: 源文件路径
            - offset: 读取起始位置（可选，默认0）
            - length: 读取长度（可选，默认全部）
    
    Returns:
        文件内容字典
    """
    body = context.get("body", {})
    config = context.get("config", {})
    
    # 获取参数
    path = body.get("path")
    offset = body.get("offset", 0)
    length = body.get("length")
    
    if not path:
        raise RPCError(ErrorCode.MISSING_REQUIRED, "Missing required parameter: path")
    
    # 检查路径权限
    normalized_path = check_path_access(path, config, write=False)
    
    # 检查文件是否存在
    if not os.path.exists(normalized_path):
        raise RPCError(ErrorCode.FILE_NOT_FOUND, f"File not found: {path}")
    
    if not os.path.isfile(normalized_path):
        raise RPCError(ErrorCode.INVALID_PARAMS, f"Not a file: {path}")
    
    # 获取文件大小
    total_size = os.path.getsize(normalized_path)
    
    # 读取文件内容
    try:
        with open(normalized_path, 'rb') as f:
            if offset > 0:
                f.seek(offset)
            if length:
                content = f.read(length)
            else:
                content = f.read()
    except IOError as e:
        raise RPCError(ErrorCode.PERMISSION_DENIED, f"Failed to read file: {e}")
    
    # 计算MD5
    md5 = calculate_md5(content)
    
    return {
        "path": normalized_path,
        "content": encode_base64(content),
        "size": len(content),
        "total_size": total_size,
        "offset": offset,
        "md5": md5,
    }


def handle_file_info(context: Dict[str, Any]) -> Dict[str, Any]:
    """
    查询文件信息
    
    Args:
        context: 请求上下文，query_params中应包含:
            - path: 文件路径
    
    Returns:
        文件信息字典
    """
    query_params = context.get("query_params", {})
    body = context.get("body", {})
    config = context.get("config", {})
    
    # 优先从query_params获取，否则从body获取
    path = query_params.get("path") or body.get("path")
    
    if not path:
        raise RPCError(ErrorCode.MISSING_REQUIRED, "Missing required parameter: path")
    
    # 检查路径权限
    try:
        normalized_path = check_path_access(path, config, write=False)
    except RPCError:
        # 如果权限检查失败，返回exists=False
        return {
            "path": path,
            "exists": False,
        }
    
    # 检查文件是否存在
    if not os.path.exists(normalized_path):
        return {
            "path": normalized_path,
            "exists": False,
        }
    
    # 获取文件信息
    try:
        file_stat = os.stat(normalized_path)
    except OSError as e:
        raise RPCError(ErrorCode.PERMISSION_DENIED, f"Failed to stat file: {e}")
    
    # 确定文件类型
    if stat.S_ISREG(file_stat.st_mode):
        file_type = "file"
    elif stat.S_ISDIR(file_stat.st_mode):
        file_type = "directory"
    elif stat.S_ISLNK(file_stat.st_mode):
        file_type = "symlink"
    else:
        file_type = "other"
    
    # 获取所有者和组
    try:
        owner = pwd.getpwuid(file_stat.st_uid).pw_name
    except KeyError:
        owner = str(file_stat.st_uid)
    
    try:
        group = grp.getgrgid(file_stat.st_gid).gr_name
    except KeyError:
        group = str(file_stat.st_gid)
    
    result = {
        "path": normalized_path,
        "exists": True,
        "type": file_type,
        "size": file_stat.st_size,
        "mode": oct(file_stat.st_mode & 0o777)[2:].zfill(4),
        "owner": owner,
        "group": group,
        "mtime": int(file_stat.st_mtime),
        "atime": int(file_stat.st_atime),
        "ctime": int(file_stat.st_ctime),
    }
    
    # 如果是普通文件，计算MD5
    if file_type == "file" and file_stat.st_size < 100 * 1024 * 1024:  # 小于100MB才计算
        try:
            result["md5"] = calculate_file_md5(normalized_path)
        except:
            pass
    
    return result


# ==================== 分块上传 ====================

def handle_chunked_upload_init(context: Dict[str, Any]) -> Dict[str, Any]:
    """
    初始化分块上传会话
    
    Args:
        context: 请求上下文，body中应包含:
            - path: 目标文件路径
            - size: 文件总大小
            - mode: 文件权限（可选）
            - overwrite: 是否覆盖（可选）
    
    Returns:
        session_id 和上传参数
    """
    import uuid
    import time
    
    body = context.get("body", {})
    config = context.get("config", {})
    
    path = body.get("path")
    total_size = body.get("size", 0)
    mode = body.get("mode", "0644")
    overwrite = body.get("overwrite", True)
    
    if not path:
        raise RPCError(ErrorCode.MISSING_REQUIRED, "Missing required parameter: path")
    
    # 检查路径权限
    normalized_path = check_path_access(path, config, write=True)
    
    # 检查文件是否存在
    if os.path.exists(normalized_path) and not overwrite:
        raise RPCError(ErrorCode.PERMISSION_DENIED, f"File already exists: {path}")
    
    # 创建临时文件
    temp_dir = tempfile.gettempdir()
    session_id = str(uuid.uuid4())
    temp_file = os.path.join(temp_dir, f"virtio_upload_{session_id}")
    
    # 创建会话
    _upload_sessions[session_id] = {
        "path": normalized_path,
        "temp_file": temp_file,
        "total_size": total_size,
        "received_size": 0,
        "mode": mode,
        "md5_hash": hashlib.md5(),
        "created_time": time.time(),
        "chunks_received": 0,
    }
    
    # 初始化临时文件
    with open(temp_file, 'wb') as f:
        pass
    
    logger.info(f"Chunked upload session created: {session_id} -> {normalized_path}")
    
    return {
        "session_id": session_id,
        "chunk_size": DEFAULT_CHUNK_SIZE,
        "path": normalized_path,
    }


def handle_chunked_upload_chunk(context: Dict[str, Any]) -> Dict[str, Any]:
    """
    上传一个分块
    
    Args:
        context: 请求上下文，body中应包含:
            - session_id: 上传会话ID
            - chunk_index: 分块索引
            - content: Base64编码的分块内容
    
    Returns:
        分块上传状态
    """
    body = context.get("body", {})
    
    session_id = body.get("session_id")
    chunk_index = body.get("chunk_index", 0)
    content_b64 = body.get("content")
    
    if not session_id:
        raise RPCError(ErrorCode.MISSING_REQUIRED, "Missing required parameter: session_id")
    if content_b64 is None:
        raise RPCError(ErrorCode.MISSING_REQUIRED, "Missing required parameter: content")
    
    # 获取会话
    session = _upload_sessions.get(session_id)
    if not session:
        raise RPCError(ErrorCode.INVALID_PARAMS, f"Invalid session_id: {session_id}")
    
    # 解码内容
    try:
        content = decode_base64(content_b64)
    except Exception as e:
        raise RPCError(ErrorCode.INVALID_PARAMS, f"Invalid base64 content: {e}")
    
    # 写入临时文件
    try:
        with open(session["temp_file"], 'ab') as f:
            f.write(content)
    except IOError as e:
        raise RPCError(ErrorCode.PERMISSION_DENIED, f"Failed to write chunk: {e}")
    
    # 更新会话状态
    session["received_size"] += len(content)
    session["chunks_received"] += 1
    session["md5_hash"].update(content)
    
    progress = (session["received_size"] / session["total_size"] * 100) if session["total_size"] > 0 else 100
    
    logger.debug(f"Chunk {chunk_index} received, progress: {progress:.1f}%")
    
    return {
        "session_id": session_id,
        "chunk_index": chunk_index,
        "received_size": session["received_size"],
        "total_size": session["total_size"],
        "progress": round(progress, 2),
    }


def handle_chunked_upload_finish(context: Dict[str, Any]) -> Dict[str, Any]:
    """
    完成分块上传，将临时文件移动到目标位置
    
    Args:
        context: 请求上下文，body中应包含:
            - session_id: 上传会话ID
            - md5: 文件MD5校验值（可选）
    
    Returns:
        上传完成结果
    """
    body = context.get("body", {})
    
    session_id = body.get("session_id")
    expected_md5 = body.get("md5")
    
    if not session_id:
        raise RPCError(ErrorCode.MISSING_REQUIRED, "Missing required parameter: session_id")
    
    # 获取会话
    session = _upload_sessions.get(session_id)
    if not session:
        raise RPCError(ErrorCode.INVALID_PARAMS, f"Invalid session_id: {session_id}")
    
    temp_file = session["temp_file"]
    target_path = session["path"]
    
    try:
        # 计算MD5
        actual_md5 = session["md5_hash"].hexdigest()
        
        # 校验MD5
        if expected_md5 and actual_md5 != expected_md5:
            raise RPCError(ErrorCode.INVALID_PARAMS, 
                          f"MD5 mismatch: expected {expected_md5}, got {actual_md5}")
        
        # 确保目标目录存在
        dir_path = os.path.dirname(target_path)
        if dir_path and not os.path.exists(dir_path):
            os.makedirs(dir_path, mode=0o755)
        
        # 移动临时文件到目标位置
        shutil.move(temp_file, target_path)
        
        # 设置权限
        try:
            os.chmod(target_path, int(session["mode"], 8))
        except (ValueError, OSError) as e:
            logger.warning(f"Failed to set file mode: {e}")
        
        final_size = os.path.getsize(target_path)
        
        logger.info(f"Chunked upload completed: {target_path} ({final_size} bytes)")
        
        return {
            "path": target_path,
            "size": final_size,
            "md5": actual_md5,
            "chunks_received": session["chunks_received"],
        }
    
    finally:
        # 清理会话
        if session_id in _upload_sessions:
            del _upload_sessions[session_id]
        # 清理临时文件
        if os.path.exists(temp_file):
            try:
                os.remove(temp_file)
            except:
                pass


def handle_chunked_upload_abort(context: Dict[str, Any]) -> Dict[str, Any]:
    """
    取消分块上传会话
    
    Args:
        context: 请求上下文，body中应包含:
            - session_id: 上传会话ID
    
    Returns:
        取消结果
    """
    body = context.get("body", {})
    session_id = body.get("session_id")
    
    if not session_id:
        raise RPCError(ErrorCode.MISSING_REQUIRED, "Missing required parameter: session_id")
    
    session = _upload_sessions.get(session_id)
    if not session:
        return {"message": "Session not found or already cleaned up"}
    
    # 清理临时文件
    temp_file = session.get("temp_file")
    if temp_file and os.path.exists(temp_file):
        try:
            os.remove(temp_file)
        except:
            pass
    
    # 清理会话
    del _upload_sessions[session_id]
    
    logger.info(f"Chunked upload session aborted: {session_id}")
    
    return {"message": "Upload session aborted", "session_id": session_id}


# ==================== 分块下载 ====================

def handle_chunked_download(context: Dict[str, Any]) -> Dict[str, Any]:
    """
    分块下载文件
    
    Args:
        context: 请求上下文，body或query_params中应包含:
            - path: 文件路径
            - offset: 读取起始位置（默认0）
            - size: 读取大小（默认DEFAULT_CHUNK_SIZE）
    
    Returns:
        分块内容和元信息
    """
    body = context.get("body", {})
    query_params = context.get("query_params", {})
    config = context.get("config", {})
    
    # 优先从 body 获取参数
    path = body.get("path") or query_params.get("path")
    offset = int(body.get("offset", query_params.get("offset", 0)))
    size = int(body.get("size", query_params.get("size", DEFAULT_CHUNK_SIZE)))
    
    if not path:
        raise RPCError(ErrorCode.MISSING_REQUIRED, "Missing required parameter: path")
    
    # 检查路径权限
    normalized_path = check_path_access(path, config, write=False)
    
    # 检查文件是否存在
    if not os.path.exists(normalized_path):
        raise RPCError(ErrorCode.FILE_NOT_FOUND, f"File not found: {path}")
    
    if not os.path.isfile(normalized_path):
        raise RPCError(ErrorCode.INVALID_PARAMS, f"Not a file: {path}")
    
    # 获取文件大小
    total_size = os.path.getsize(normalized_path)
    
    # 验证 offset
    if offset < 0 or offset > total_size:
        raise RPCError(ErrorCode.INVALID_PARAMS, f"Invalid offset: {offset}")
    
    # 限制单次读取大小
    max_chunk_size = 5 * 1024 * 1024  # 最大 5MB
    size = min(size, max_chunk_size, total_size - offset)
    
    # 读取分块内容
    try:
        with open(normalized_path, 'rb') as f:
            f.seek(offset)
            content = f.read(size)
    except IOError as e:
        raise RPCError(ErrorCode.PERMISSION_DENIED, f"Failed to read file: {e}")
    
    # 计算分块 MD5
    chunk_md5 = calculate_md5(content)
    
    # 判断是否还有更多数据
    has_more = (offset + len(content)) < total_size
    
    return {
        "path": normalized_path,
        "content": encode_base64(content),
        "offset": offset,
        "size": len(content),
        "total_size": total_size,
        "chunk_md5": chunk_md5,
        "has_more": has_more,
        "next_offset": offset + len(content) if has_more else None,
    }
