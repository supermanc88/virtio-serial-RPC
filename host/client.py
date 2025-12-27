# -*- coding: utf-8 -*-
"""
Host端RPC客户端主模块
实现与Guest端通信的客户端
"""

import logging
import os
import sys
from typing import Optional, Dict, Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common.protocol import build_request, HTTPResponse
from common.errors import ErrorCode, RPCError
from common.utils import encode_base64, decode_base64, calculate_file_md5
from host.connection import UDSConnection

logger = logging.getLogger(__name__)


class VirtioRPCClient:
    """Host端RPC客户端"""
    
    def __init__(self, socket_path: str, config: Optional[Dict] = None):
        """
        初始化客户端
        
        Args:
            socket_path: virtio-serial UNIX域套接字路径
            config: 配置字典
        """
        self.socket_path = socket_path
        self.config = config or {}
        
        # 连接参数
        connection_config = self.config.get('connection', {})
        self.connection = UDSConnection(
            socket_path,
            connect_timeout=connection_config.get('connect_timeout', 5.0),
            read_timeout=connection_config.get('read_timeout', 30.0),
            write_timeout=connection_config.get('write_timeout', 30.0),
        )
        
        # 重试配置
        retry_config = self.config.get('retry', {})
        self.max_retries = retry_config.get('max_retries', 3)
        self.retry_interval = retry_config.get('retry_interval', 1.0)
        self.backoff_factor = retry_config.get('backoff_factor', 2.0)
    
    def connect(self) -> bool:
        """
        连接到virtio-serial套接字
        
        Returns:
            是否成功连接
        """
        return self.connection.connect()
    
    def disconnect(self):
        """断开连接"""
        self.connection.disconnect()
    
    def send_request(self, method: str, endpoint: str,
                     body: Optional[Dict] = None, 
                     timeout: Optional[float] = None) -> Dict[str, Any]:
        """
        发送HTTP请求并获取响应
        
        Args:
            method: HTTP方法
            endpoint: API端点
            body: 请求体
            timeout: 超时时间
        
        Returns:
            响应数据字典
        
        Raises:
            RPCError: 请求失败时抛出
        """
        if not self.connection.is_connected:
            raise RPCError(ErrorCode.CONNECTION_LOST, "Not connected to server")
        
        # 构建请求
        request = build_request(method, endpoint, body)
        request_bytes = request.to_bytes()
        
        logger.debug(f"Sending request: {method} {endpoint}")
        
        # 发送请求并接收响应
        response_bytes = self.connection.send_and_receive(request_bytes, timeout)
        
        if not response_bytes:
            raise RPCError(ErrorCode.READ_TIMEOUT, "No response from server")
        
        # 解析响应
        try:
            response = HTTPResponse.from_bytes(response_bytes)
        except Exception as e:
            raise RPCError(ErrorCode.JSON_PARSE_ERROR, f"Failed to parse response: {e}")
        
        # 检查响应状态
        if response.body:
            code = response.body.get('code', 0)
            if code != 0:
                raise RPCError(
                    ErrorCode(code) if code in ErrorCode._value2member_map_ else ErrorCode.INTERNAL_ERROR,
                    response.body.get('message', 'Unknown error'),
                    response.body.get('data')
                )
            return response.body
        
        return {"code": 0, "message": "success"}
    
    # ==================== 便捷方法 ====================
    
    def ping(self) -> Dict[str, Any]:
        """
        心跳检测
        
        Returns:
            心跳响应数据
        """
        return self.send_request("GET", "/api/v1/ping")
    
    def get_system_info(self) -> Dict[str, Any]:
        """
        获取系统信息
        
        Returns:
            系统信息
        """
        return self.send_request("GET", "/api/v1/system/info")
    
    def get_system_status(self) -> Dict[str, Any]:
        """
        获取系统状态
        
        Returns:
            系统状态
        """
        return self.send_request("GET", "/api/v1/system/status")
    
    def exec_command(self, command: str, timeout: int = 30,
                     working_dir: Optional[str] = None,
                     env: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        """
        执行Shell命令
        
        Args:
            command: 要执行的命令
            timeout: 命令执行超时时间
            working_dir: 工作目录
            env: 环境变量
        
        Returns:
            命令执行结果
        """
        body = {
            "command": command,
            "timeout": timeout,
        }
        if working_dir:
            body["working_dir"] = working_dir
        if env:
            body["env"] = env
        
        return self.send_request("POST", "/api/v1/shell/exec", body, timeout=timeout + 5)
    
    def upload_file(self, local_path: str, remote_path: str,
                    mode: str = "0644", overwrite: bool = True) -> Dict[str, Any]:
        """
        上传文件到Guest
        
        Args:
            local_path: 本地文件路径
            remote_path: Guest上的目标路径
            mode: 文件权限
            overwrite: 是否覆盖已存在的文件
        
        Returns:
            上传结果
        """
        if not os.path.exists(local_path):
            raise RPCError(ErrorCode.FILE_NOT_FOUND, f"Local file not found: {local_path}")
        
        # 读取文件内容
        with open(local_path, 'rb') as f:
            content = f.read()
        
        body = {
            "path": remote_path,
            "content": encode_base64(content),
            "mode": mode,
            "overwrite": overwrite,
        }
        
        return self.send_request("POST", "/api/v1/file/upload", body)
    
    def download_file(self, remote_path: str, local_path: str,
                      chunk_size: int = 2 * 1024 * 1024) -> Dict[str, Any]:
        """
        从Guest下载文件（自动选择普通/分块下载）
        
        Args:
            remote_path: Guest上的文件路径
            local_path: 本地保存路径
            chunk_size: 分块大小（默认2MB）
        
        Returns:
            下载结果
        """
        # 先获取文件信息
        info_result = self.get_file_info(remote_path)
        if not info_result.get('data', {}).get('exists', False):
            raise RPCError(ErrorCode.FILE_NOT_FOUND, f"Remote file not found: {remote_path}")
        
        total_size = info_result.get('data', {}).get('size', 0)
        
        # 对于小文件（<2MB），直接下载
        if total_size <= chunk_size:
            body = {"path": remote_path}
            result = self.send_request("POST", "/api/v1/file/download", body)
            
            content_b64 = result.get('data', {}).get('content', '')
            content = decode_base64(content_b64)
            
            # 确保目录存在
            dir_path = os.path.dirname(local_path)
            if dir_path and not os.path.exists(dir_path):
                os.makedirs(dir_path)
            
            with open(local_path, 'wb') as f:
                f.write(content)
            
            return result
        
        # 对于大文件，使用分块下载
        return self.chunked_download_file(remote_path, local_path, chunk_size)
    
    def chunked_download_file(self, remote_path: str, local_path: str,
                               chunk_size: int = 2 * 1024 * 1024,
                               progress_callback=None) -> Dict[str, Any]:
        """
        分块下载大文件
        
        Args:
            remote_path: Guest上的文件路径
            local_path: 本地保存路径
            chunk_size: 分块大小（默认2MB）
            progress_callback: 进度回调函数 callback(downloaded, total)
        
        Returns:
            下载结果
        """
        import hashlib
        
        # 确保目录存在
        dir_path = os.path.dirname(local_path)
        if dir_path and not os.path.exists(dir_path):
            os.makedirs(dir_path)
        
        offset = 0
        total_size = None
        md5_hash = hashlib.md5()
        
        with open(local_path, 'wb') as f:
            while True:
                body = {
                    "path": remote_path,
                    "offset": offset,
                    "size": chunk_size,
                }
                result = self.send_request("POST", "/api/v1/file/chunked/download", body)
                data = result.get('data', {})
                
                if total_size is None:
                    total_size = data.get('total_size', 0)
                
                chunk_b64 = data.get('content', '')
                chunk = decode_base64(chunk_b64)
                
                f.write(chunk)
                md5_hash.update(chunk)
                offset += len(chunk)
                
                if progress_callback:
                    progress_callback(offset, total_size)
                
                logger.debug(f"Downloaded {offset}/{total_size} bytes ({offset*100//total_size}%)")
                
                if not data.get('has_more', False):
                    break
        
        return {
            "code": 0,
            "message": "success",
            "data": {
                "path": local_path,
                "size": offset,
                "md5": md5_hash.hexdigest(),
            }
        }
    
    def chunked_upload_file(self, local_path: str, remote_path: str,
                            mode: str = "0644", overwrite: bool = True,
                            chunk_size: int = 2 * 1024 * 1024,
                            progress_callback=None) -> Dict[str, Any]:
        """
        分块上传大文件
        
        Args:
            local_path: 本地文件路径
            remote_path: Guest上的目标路径
            mode: 文件权限
            overwrite: 是否覆盖已存在的文件
            chunk_size: 分块大小（默认2MB）
            progress_callback: 进度回调函数 callback(uploaded, total)
        
        Returns:
            上传结果
        """
        import hashlib
        
        if not os.path.exists(local_path):
            raise RPCError(ErrorCode.FILE_NOT_FOUND, f"Local file not found: {local_path}")
        
        total_size = os.path.getsize(local_path)
        
        # 1. 初始化上传会话
        init_body = {
            "path": remote_path,
            "size": total_size,
            "mode": mode,
            "overwrite": overwrite,
        }
        init_result = self.send_request("POST", "/api/v1/file/chunked/upload/init", init_body)
        session_id = init_result.get('data', {}).get('session_id')
        
        if not session_id:
            raise RPCError(ErrorCode.INTERNAL_ERROR, "Failed to initialize upload session")
        
        logger.info(f"Chunked upload session started: {session_id}")
        
        # 2. 分块上传
        md5_hash = hashlib.md5()
        uploaded = 0
        chunk_index = 0
        
        try:
            with open(local_path, 'rb') as f:
                while True:
                    chunk = f.read(chunk_size)
                    if not chunk:
                        break
                    
                    md5_hash.update(chunk)
                    
                    chunk_body = {
                        "session_id": session_id,
                        "chunk_index": chunk_index,
                        "content": encode_base64(chunk),
                    }
                    self.send_request("POST", "/api/v1/file/chunked/upload/chunk", chunk_body)
                    
                    uploaded += len(chunk)
                    chunk_index += 1
                    
                    if progress_callback:
                        progress_callback(uploaded, total_size)
                    
                    logger.debug(f"Uploaded chunk {chunk_index}, {uploaded}/{total_size} bytes ({uploaded*100//total_size}%)")
            
            # 3. 完成上传
            finish_body = {
                "session_id": session_id,
                "md5": md5_hash.hexdigest(),
            }
            result = self.send_request("POST", "/api/v1/file/chunked/upload/finish", finish_body)
            
            logger.info(f"Chunked upload completed: {remote_path}")
            return result
        
        except Exception as e:
            # 上传失败，尝试取消会话
            try:
                abort_body = {"session_id": session_id}
                self.send_request("POST", "/api/v1/file/chunked/upload/abort", abort_body)
            except:
                pass
            raise
    
    def get_file_info(self, path: str) -> Dict[str, Any]:
        """
        查询文件信息
        
        Args:
            path: 文件路径
        
        Returns:
            文件信息
        """
        from urllib.parse import quote
        endpoint = f"/api/v1/file/info?path={quote(path)}"
        return self.send_request("GET", endpoint)
    
    def control_service(self, name: str, action: str) -> Dict[str, Any]:
        """
        控制服务
        
        Args:
            name: 服务名称
            action: 操作类型 (start, stop, restart, status, enable, disable)
        
        Returns:
            操作结果
        """
        body = {
            "name": name,
            "action": action,
        }
        return self.send_request("POST", "/api/v1/service/control", body)
    
    # ==================== 上下文管理 ====================
    
    def __enter__(self):
        self.connect()
        return self
    
    def __exit__(self, *args):
        self.disconnect()
