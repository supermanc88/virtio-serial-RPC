# -*- coding: utf-8 -*-
"""
HTTP协议处理模块
实现HTTP请求和响应的构建与解析
"""

import json
import re
from dataclasses import dataclass, field
from typing import Optional, Dict, Any
from .errors import ErrorCode, RPCError, ERROR_TO_HTTP_STATUS
from .utils import generate_request_id, get_timestamp


# HTTP状态码对应的状态文本
HTTP_STATUS_TEXT = {
    200: "OK",
    400: "Bad Request",
    403: "Forbidden",
    404: "Not Found",
    500: "Internal Server Error",
    503: "Service Unavailable",
}


@dataclass
class HTTPRequest:
    """HTTP请求数据类"""
    method: str
    path: str
    headers: Dict[str, str] = field(default_factory=dict)
    body: Optional[Dict[str, Any]] = None
    
    def to_bytes(self) -> bytes:
        """将请求转换为HTTP报文字节"""
        # 构建请求行
        request_line = f"{self.method} {self.path} HTTP/1.1\r\n"
        
        # 设置默认headers
        if "Host" not in self.headers:
            self.headers["Host"] = "virtio-rpc"
        if "Content-Type" not in self.headers:
            self.headers["Content-Type"] = "application/json"
        if "X-Request-ID" not in self.headers:
            self.headers["X-Request-ID"] = generate_request_id()
        if "X-Timestamp" not in self.headers:
            self.headers["X-Timestamp"] = str(get_timestamp())
        
        # 构建body
        body_bytes = b""
        if self.body is not None:
            body_bytes = json.dumps(self.body, ensure_ascii=False).encode('utf-8')
            self.headers["Content-Length"] = str(len(body_bytes))
        else:
            self.headers["Content-Length"] = "0"
        
        # 构建headers部分
        headers_str = "".join(f"{k}: {v}\r\n" for k, v in self.headers.items())
        
        # 组合完整请求
        request_str = request_line + headers_str + "\r\n"
        return request_str.encode('utf-8') + body_bytes


@dataclass
class HTTPResponse:
    """HTTP响应数据类"""
    status_code: int
    status_text: str
    headers: Dict[str, str] = field(default_factory=dict)
    body: Optional[Dict[str, Any]] = None
    
    @classmethod
    def from_bytes(cls, data: bytes) -> 'HTTPResponse':
        """从HTTP报文字节解析响应"""
        try:
            # 分离header和body
            if b"\r\n\r\n" in data:
                header_part, body_part = data.split(b"\r\n\r\n", 1)
            else:
                header_part = data
                body_part = b""
            
            header_str = header_part.decode('utf-8')
            lines = header_str.split("\r\n")
            
            # 解析状态行
            status_line = lines[0]
            match = re.match(r'HTTP/1\.[01] (\d+) (.+)', status_line)
            if not match:
                raise RPCError(ErrorCode.JSON_PARSE_ERROR, "Invalid HTTP response")
            
            status_code = int(match.group(1))
            status_text = match.group(2)
            
            # 解析headers
            headers = {}
            for line in lines[1:]:
                if ": " in line:
                    key, value = line.split(": ", 1)
                    headers[key] = value
            
            # 解析body
            body = None
            if body_part:
                try:
                    body = json.loads(body_part.decode('utf-8'))
                except json.JSONDecodeError:
                    body = {"raw": body_part.decode('utf-8', errors='replace')}
            
            return cls(
                status_code=status_code,
                status_text=status_text,
                headers=headers,
                body=body
            )
        except RPCError:
            raise
        except Exception as e:
            raise RPCError(ErrorCode.JSON_PARSE_ERROR, f"Failed to parse response: {e}")
    
    def to_bytes(self) -> bytes:
        """将响应转换为HTTP报文字节"""
        # 构建状态行
        status_text = self.status_text or HTTP_STATUS_TEXT.get(self.status_code, "Unknown")
        status_line = f"HTTP/1.1 {self.status_code} {status_text}\r\n"
        
        # 设置默认headers
        if "Content-Type" not in self.headers:
            self.headers["Content-Type"] = "application/json"
        
        # 构建body
        body_bytes = b""
        if self.body is not None:
            body_bytes = json.dumps(self.body, ensure_ascii=False).encode('utf-8')
        self.headers["Content-Length"] = str(len(body_bytes))
        
        # 构建headers部分
        headers_str = "".join(f"{k}: {v}\r\n" for k, v in self.headers.items())
        
        # 组合完整响应
        response_str = status_line + headers_str + "\r\n"
        return response_str.encode('utf-8') + body_bytes


def build_request(method: str, endpoint: str, body: Optional[Dict] = None,
                  request_id: Optional[str] = None) -> HTTPRequest:
    """
    构建HTTP请求
    
    Args:
        method: HTTP方法 (GET, POST等)
        endpoint: API端点路径
        body: 请求体
        request_id: 请求ID（可选）
    
    Returns:
        HTTPRequest对象
    """
    headers = {}
    if request_id:
        headers["X-Request-ID"] = request_id
    
    return HTTPRequest(
        method=method.upper(),
        path=endpoint,
        headers=headers,
        body=body
    )


def build_response(code: ErrorCode, message: Optional[str] = None,
                   data: Optional[Any] = None, 
                   request_id: Optional[str] = None) -> HTTPResponse:
    """
    构建HTTP响应
    
    Args:
        code: 错误码
        message: 消息
        data: 响应数据
        request_id: 请求ID
    
    Returns:
        HTTPResponse对象
    """
    from .errors import ERROR_MESSAGES
    
    http_status = ERROR_TO_HTTP_STATUS.get(code, 500)
    status_text = HTTP_STATUS_TEXT.get(http_status, "Unknown")
    
    headers = {}
    if request_id:
        headers["X-Request-ID"] = request_id
    
    body = {
        "code": int(code),
        "message": message or ERROR_MESSAGES.get(code, "Unknown"),
        "timestamp": get_timestamp()
    }
    if data is not None:
        body["data"] = data
    
    return HTTPResponse(
        status_code=http_status,
        status_text=status_text,
        headers=headers,
        body=body
    )


def parse_request(data: bytes) -> HTTPRequest:
    """
    从HTTP报文字节解析请求
    
    Args:
        data: HTTP请求报文字节
    
    Returns:
        HTTPRequest对象
    """
    try:
        # 分离header和body
        if b"\r\n\r\n" in data:
            header_part, body_part = data.split(b"\r\n\r\n", 1)
        else:
            header_part = data
            body_part = b""
        
        header_str = header_part.decode('utf-8')
        lines = header_str.split("\r\n")
        
        # 解析请求行
        request_line = lines[0]
        parts = request_line.split(" ")
        if len(parts) < 2:
            raise RPCError(ErrorCode.JSON_PARSE_ERROR, "Invalid HTTP request line")
        
        method = parts[0]
        path = parts[1]
        
        # 解析headers
        headers = {}
        for line in lines[1:]:
            if ": " in line:
                key, value = line.split(": ", 1)
                headers[key] = value
        
        # 解析body
        body = None
        if body_part:
            content_length = int(headers.get("Content-Length", 0))
            if content_length > 0:
                try:
                    body = json.loads(body_part[:content_length].decode('utf-8'))
                except json.JSONDecodeError as e:
                    raise RPCError(ErrorCode.JSON_PARSE_ERROR, f"Invalid JSON body: {e}")
        
        return HTTPRequest(
            method=method,
            path=path,
            headers=headers,
            body=body
        )
    except RPCError:
        raise
    except Exception as e:
        raise RPCError(ErrorCode.JSON_PARSE_ERROR, f"Failed to parse request: {e}")


def parse_response(data: bytes) -> Dict[str, Any]:
    """
    解析HTTP响应并返回body
    
    Args:
        data: HTTP响应报文字节
    
    Returns:
        响应body字典
    """
    response = HTTPResponse.from_bytes(data)
    return response.body or {}
