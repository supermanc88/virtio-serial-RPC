# -*- coding: utf-8 -*-
"""
Common module for virtio-serial RPC
共用模块，包含协议定义、错误码和工具函数
"""

from .errors import ErrorCode, RPCError
from .protocol import HTTPRequest, HTTPResponse, build_request, parse_response
from .utils import generate_request_id, get_timestamp

__all__ = [
    'ErrorCode',
    'RPCError',
    'HTTPRequest',
    'HTTPResponse',
    'build_request',
    'parse_response',
    'generate_request_id',
    'get_timestamp',
]
