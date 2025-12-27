# -*- coding: utf-8 -*-
"""
错误码定义模块
定义所有RPC通信的错误码和异常类
"""

from enum import IntEnum
from typing import Optional, Any


class ErrorCode(IntEnum):
    """错误码枚举"""
    # 成功
    SUCCESS = 0
    
    # 1XXX - 命令执行错误
    CMD_EXEC_FAILED = 1001
    CMD_TIMEOUT = 1002
    CMD_NOT_FOUND = 1003
    
    # 2XXX - 请求参数错误
    INVALID_PARAMS = 2001
    JSON_PARSE_ERROR = 2002
    MISSING_REQUIRED = 2003
    
    # 3XXX - 资源不存在错误
    ENDPOINT_NOT_FOUND = 3001
    FILE_NOT_FOUND = 3002
    
    # 4XXX - 权限错误
    PERMISSION_DENIED = 4001
    
    # 5XXX - 服务器错误
    INTERNAL_ERROR = 5001
    SERVICE_UNAVAILABLE = 5002
    
    # 6XXX - 网络/通信错误
    CONNECTION_LOST = 6001
    READ_TIMEOUT = 6002
    WRITE_TIMEOUT = 6003
    CONNECTION_REFUSED = 6004


# 错误码对应的HTTP状态码
ERROR_TO_HTTP_STATUS = {
    ErrorCode.SUCCESS: 200,
    ErrorCode.CMD_EXEC_FAILED: 200,
    ErrorCode.CMD_TIMEOUT: 200,
    ErrorCode.CMD_NOT_FOUND: 200,
    ErrorCode.INVALID_PARAMS: 400,
    ErrorCode.JSON_PARSE_ERROR: 400,
    ErrorCode.MISSING_REQUIRED: 400,
    ErrorCode.ENDPOINT_NOT_FOUND: 404,
    ErrorCode.FILE_NOT_FOUND: 404,
    ErrorCode.PERMISSION_DENIED: 403,
    ErrorCode.INTERNAL_ERROR: 500,
    ErrorCode.SERVICE_UNAVAILABLE: 503,
    ErrorCode.CONNECTION_LOST: 500,
    ErrorCode.READ_TIMEOUT: 500,
    ErrorCode.WRITE_TIMEOUT: 500,
    ErrorCode.CONNECTION_REFUSED: 500,
}


# 错误码对应的默认消息
ERROR_MESSAGES = {
    ErrorCode.SUCCESS: "success",
    ErrorCode.CMD_EXEC_FAILED: "Command execution failed",
    ErrorCode.CMD_TIMEOUT: "Command execution timeout",
    ErrorCode.CMD_NOT_FOUND: "Command not found",
    ErrorCode.INVALID_PARAMS: "Invalid parameters",
    ErrorCode.JSON_PARSE_ERROR: "JSON parse error",
    ErrorCode.MISSING_REQUIRED: "Missing required parameter",
    ErrorCode.ENDPOINT_NOT_FOUND: "Endpoint not found",
    ErrorCode.FILE_NOT_FOUND: "File not found",
    ErrorCode.PERMISSION_DENIED: "Permission denied",
    ErrorCode.INTERNAL_ERROR: "Internal server error",
    ErrorCode.SERVICE_UNAVAILABLE: "Service unavailable",
    ErrorCode.CONNECTION_LOST: "Connection lost",
    ErrorCode.READ_TIMEOUT: "Read timeout",
    ErrorCode.WRITE_TIMEOUT: "Write timeout",
    ErrorCode.CONNECTION_REFUSED: "Connection refused",
}


class RPCError(Exception):
    """RPC错误异常类"""
    
    def __init__(self, code: ErrorCode, message: Optional[str] = None, 
                 data: Optional[Any] = None):
        self.code = code
        self.message = message or ERROR_MESSAGES.get(code, "Unknown error")
        self.data = data
        super().__init__(self.message)
    
    def to_dict(self) -> dict:
        """转换为字典格式"""
        result = {
            "code": self.code,
            "message": self.message,
        }
        if self.data is not None:
            result["data"] = self.data
        return result
    
    @property
    def http_status(self) -> int:
        """获取对应的HTTP状态码"""
        return ERROR_TO_HTTP_STATUS.get(self.code, 500)
