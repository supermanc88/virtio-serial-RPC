# -*- coding: utf-8 -*-
"""
virtio-serial字符设备读写模块
处理Guest端字符设备的读写操作
"""

import os
import select
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class VirtioSerialDevice:
    """virtio-serial字符设备处理类"""
    
    def __init__(self, device_path: str, buffer_size: int = 65536):
        """
        初始化设备处理器
        
        Args:
            device_path: 字符设备路径，如 /dev/virtio-ports/test.vserial.0
            buffer_size: 读取缓冲区大小
        """
        self.device_path = device_path
        self.buffer_size = buffer_size
        self._fd: Optional[int] = None
        self._file = None
    
    def open(self) -> bool:
        """
        打开设备
        
        Returns:
            是否成功打开
        """
        try:
            if not os.path.exists(self.device_path):
                logger.error(f"Device not found: {self.device_path}")
                return False
            
            # 以读写模式打开设备（非阻塞）
            self._fd = os.open(self.device_path, os.O_RDWR | os.O_NONBLOCK)
            logger.info(f"Opened device: {self.device_path}")
            return True
        except OSError as e:
            logger.error(f"Failed to open device {self.device_path}: {e}")
            return False
    
    def close(self):
        """关闭设备"""
        if self._fd is not None:
            try:
                os.close(self._fd)
                logger.info(f"Closed device: {self.device_path}")
            except OSError as e:
                logger.error(f"Error closing device: {e}")
            finally:
                self._fd = None
    
    def read(self, timeout: float = 30.0) -> Optional[bytes]:
        """
        从设备读取数据
        
        Args:
            timeout: 读取超时时间（秒）
        
        Returns:
            读取到的数据，超时返回None
        """
        if self._fd is None:
            logger.error("Device not opened")
            return None
        
        try:
            # 使用select等待数据可读
            readable, _, _ = select.select([self._fd], [], [], timeout)
            if not readable:
                logger.debug("Read timeout")
                return None
            
            # 读取数据
            data = b""
            while True:
                try:
                    chunk = os.read(self._fd, self.buffer_size)
                    if not chunk:
                        break
                    data += chunk
                    
                    # 检查是否还有更多数据
                    readable, _, _ = select.select([self._fd], [], [], 0.1)
                    if not readable:
                        break
                except BlockingIOError:
                    break
            
            if data:
                logger.debug(f"Read {len(data)} bytes from device")
            return data if data else None
            
        except OSError as e:
            logger.error(f"Error reading from device: {e}")
            return None
    
    def write(self, data: bytes) -> bool:
        """
        向设备写入数据
        
        Args:
            data: 要写入的数据
        
        Returns:
            是否成功写入
        """
        if self._fd is None:
            logger.error("Device not opened")
            return False
        
        try:
            total_written = 0
            while total_written < len(data):
                # 等待设备可写
                _, writable, _ = select.select([], [self._fd], [], 5.0)
                if not writable:
                    logger.error("Write timeout")
                    return False
                
                written = os.write(self._fd, data[total_written:])
                if written <= 0:
                    logger.error("Failed to write data")
                    return False
                total_written += written
            
            logger.debug(f"Wrote {total_written} bytes to device")
            return True
            
        except OSError as e:
            logger.error(f"Error writing to device: {e}")
            return False
    
    def read_request(self, timeout: float = 30.0) -> Optional[bytes]:
        """
        读取完整的HTTP请求
        会持续读取直到收到完整的HTTP请求（通过Content-Length判断）
        
        Args:
            timeout: 读取超时时间（秒）
        
        Returns:
            完整的HTTP请求数据
        """
        if self._fd is None:
            logger.error("Device not opened")
            return None
        
        data = b""
        content_length = 0
        headers_end = -1
        
        import time
        start_time = time.time()
        
        while True:
            # 检查超时
            if time.time() - start_time > timeout:
                logger.debug("Request read timeout")
                return None
            
            # 等待数据
            try:
                readable, _, _ = select.select([self._fd], [], [], 1.0)
                if not readable:
                    # 如果已经有数据并且超过短暂等待，可能请求已完整
                    if data and headers_end > 0:
                        body_start = headers_end + 4
                        if len(data) >= body_start + content_length:
                            break
                    continue
                
                chunk = os.read(self._fd, self.buffer_size)
                if chunk:
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
                    
                    # 检查是否收到完整请求
                    if headers_end > 0:
                        body_start = headers_end + 4
                        if len(data) >= body_start + content_length:
                            break
                            
            except BlockingIOError:
                continue
            except OSError as e:
                logger.error(f"Error reading request: {e}")
                return None
        
        return data if data else None
    
    @property
    def is_open(self) -> bool:
        """检查设备是否已打开"""
        return self._fd is not None
    
    def __enter__(self):
        self.open()
        return self
    
    def __exit__(self, *args):
        self.close()
