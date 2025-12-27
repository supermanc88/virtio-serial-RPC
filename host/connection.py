# -*- coding: utf-8 -*-
"""
UNIX域套接字连接管理模块
处理Host端与virtio-serial套接字的连接
"""

import socket
import select
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class UDSConnection:
    """UNIX域套接字连接管理类"""
    
    def __init__(self, socket_path: str, connect_timeout: float = 5.0,
                 read_timeout: float = 30.0, write_timeout: float = 30.0):
        """
        初始化连接管理器
        
        Args:
            socket_path: UNIX域套接字路径
            connect_timeout: 连接超时时间（秒）
            read_timeout: 读取超时时间（秒）
            write_timeout: 写入超时时间（秒）
        """
        self.socket_path = socket_path
        self.connect_timeout = connect_timeout
        self.read_timeout = read_timeout
        self.write_timeout = write_timeout
        self._socket: Optional[socket.socket] = None
    
    def connect(self) -> bool:
        """
        连接到UNIX域套接字
        
        Returns:
            是否成功连接
        """
        try:
            self._socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self._socket.settimeout(self.connect_timeout)
            self._socket.connect(self.socket_path)
            self._socket.setblocking(False)
            logger.info(f"Connected to {self.socket_path}")
            return True
        except socket.error as e:
            logger.error(f"Failed to connect to {self.socket_path}: {e}")
            self._socket = None
            return False
    
    def disconnect(self):
        """断开连接"""
        if self._socket:
            try:
                self._socket.close()
                logger.info(f"Disconnected from {self.socket_path}")
            except socket.error as e:
                logger.error(f"Error closing socket: {e}")
            finally:
                self._socket = None
    
    def send(self, data: bytes) -> bool:
        """
        发送数据
        
        Args:
            data: 要发送的数据
        
        Returns:
            是否成功发送
        """
        if not self._socket:
            logger.error("Socket not connected")
            return False
        
        try:
            total_sent = 0
            while total_sent < len(data):
                # 等待套接字可写
                _, writable, _ = select.select([], [self._socket], [], self.write_timeout)
                if not writable:
                    logger.error("Write timeout")
                    return False
                
                sent = self._socket.send(data[total_sent:])
                if sent == 0:
                    logger.error("Socket connection broken")
                    return False
                total_sent += sent
            
            logger.debug(f"Sent {total_sent} bytes")
            return True
            
        except socket.error as e:
            logger.error(f"Error sending data: {e}")
            return False
    
    def receive(self, timeout: Optional[float] = None) -> Optional[bytes]:
        """
        接收数据
        
        Args:
            timeout: 读取超时时间（秒），None使用默认值
        
        Returns:
            接收到的数据，超时或错误返回None
        """
        if not self._socket:
            logger.error("Socket not connected")
            return None
        
        timeout = timeout if timeout is not None else self.read_timeout
        
        try:
            data = b""
            content_length = 0
            headers_end = -1
            
            import time
            start_time = time.time()
            
            while True:
                # 检查超时
                elapsed = time.time() - start_time
                if elapsed >= timeout:
                    logger.debug("Read timeout")
                    return data if data else None
                
                remaining = timeout - elapsed
                
                # 等待数据可读
                readable, _, _ = select.select([self._socket], [], [], min(remaining, 1.0))
                if not readable:
                    # 如果已经接收到完整的HTTP响应，返回
                    if data and headers_end > 0:
                        body_start = headers_end + 4
                        if len(data) >= body_start + content_length:
                            break
                    continue
                
                chunk = self._socket.recv(65536)
                if not chunk:
                    # 连接关闭
                    break
                
                data += chunk
                
                # 查找headers结束位置
                if headers_end < 0 and b"\r\n\r\n" in data:
                    headers_end = data.find(b"\r\n\r\n")
                    
                    # 解析Content-Length
                    header_part = data[:headers_end].decode('utf-8', errors='ignore')
                    for line in header_part.split("\r\n"):
                        if line.lower().startswith("content-length:"):
                            try:
                                content_length = int(line.split(":", 1)[1].strip())
                            except ValueError:
                                pass
                            break
                
                # 检查是否收到完整响应
                if headers_end > 0:
                    body_start = headers_end + 4
                    if len(data) >= body_start + content_length:
                        break
            
            if data:
                logger.debug(f"Received {len(data)} bytes")
            return data if data else None
            
        except socket.error as e:
            logger.error(f"Error receiving data: {e}")
            return None
    
    def send_and_receive(self, data: bytes, timeout: Optional[float] = None) -> Optional[bytes]:
        """
        发送数据并等待接收响应
        
        Args:
            data: 要发送的数据
            timeout: 接收超时时间
        
        Returns:
            接收到的响应数据
        """
        if not self.send(data):
            return None
        return self.receive(timeout)
    
    @property
    def is_connected(self) -> bool:
        """检查是否已连接"""
        return self._socket is not None
    
    def __enter__(self):
        self.connect()
        return self
    
    def __exit__(self, *args):
        self.disconnect()
