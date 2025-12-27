# -*- coding: utf-8 -*-
"""
工具函数模块
提供通用的工具函数
"""

import uuid
import time
import hashlib
import base64
import os
from typing import Optional


def generate_request_id() -> str:
    """生成唯一的请求ID"""
    return str(uuid.uuid4())


def get_timestamp() -> int:
    """获取当前Unix时间戳（秒）"""
    return int(time.time())


def get_timestamp_ms() -> int:
    """获取当前Unix时间戳（毫秒）"""
    return int(time.time() * 1000)


def calculate_md5(data: bytes) -> str:
    """
    计算数据的MD5哈希值
    
    Args:
        data: 要计算哈希的数据
    
    Returns:
        MD5哈希值（十六进制字符串）
    """
    return hashlib.md5(data).hexdigest()


def calculate_file_md5(filepath: str) -> str:
    """
    计算文件的MD5哈希值
    
    Args:
        filepath: 文件路径
    
    Returns:
        MD5哈希值（十六进制字符串）
    """
    md5_hash = hashlib.md5()
    with open(filepath, 'rb') as f:
        for chunk in iter(lambda: f.read(8192), b''):
            md5_hash.update(chunk)
    return md5_hash.hexdigest()


def encode_base64(data: bytes) -> str:
    """
    Base64编码
    
    Args:
        data: 要编码的数据
    
    Returns:
        Base64编码字符串
    """
    return base64.b64encode(data).decode('utf-8')


def decode_base64(data: str) -> bytes:
    """
    Base64解码
    
    Args:
        data: Base64编码字符串
    
    Returns:
        解码后的字节数据
    """
    return base64.b64decode(data)


def normalize_path(path: str) -> str:
    """
    规范化文件路径，防止路径遍历攻击
    
    Args:
        path: 原始路径
    
    Returns:
        规范化后的绝对路径
    """
    # 转换为绝对路径并规范化
    normalized = os.path.normpath(os.path.abspath(path))
    return normalized


def is_path_safe(path: str, allowed_prefixes: list) -> bool:
    """
    检查路径是否安全（在允许的前缀范围内）
    
    Args:
        path: 要检查的路径
        allowed_prefixes: 允许的路径前缀列表
    
    Returns:
        路径是否安全
    """
    normalized = normalize_path(path)
    for prefix in allowed_prefixes:
        if normalized.startswith(os.path.normpath(prefix)):
            return True
    return False


def format_size(size: int) -> str:
    """
    格式化文件大小为人类可读格式
    
    Args:
        size: 字节大小
    
    Returns:
        格式化后的字符串（如 "1.5 MB"）
    """
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size < 1024:
            return f"{size:.2f} {unit}" if unit != 'B' else f"{size} {unit}"
        size /= 1024
    return f"{size:.2f} PB"


def safe_int(value, default: int = 0) -> int:
    """
    安全地将值转换为整数
    
    Args:
        value: 要转换的值
        default: 转换失败时的默认值
    
    Returns:
        整数值
    """
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


class Timer:
    """计时器类，用于测量代码执行时间"""
    
    def __init__(self):
        self.start_time: Optional[float] = None
        self.end_time: Optional[float] = None
    
    def start(self):
        """开始计时"""
        self.start_time = time.time()
        self.end_time = None
    
    def stop(self) -> float:
        """
        停止计时
        
        Returns:
            耗时（秒）
        """
        self.end_time = time.time()
        return self.elapsed
    
    @property
    def elapsed(self) -> float:
        """获取耗时（秒）"""
        if self.start_time is None:
            return 0
        end = self.end_time or time.time()
        return end - self.start_time
    
    @property
    def elapsed_ms(self) -> int:
        """获取耗时（毫秒）"""
        return int(self.elapsed * 1000)
    
    def __enter__(self):
        self.start()
        return self
    
    def __exit__(self, *args):
        self.stop()
