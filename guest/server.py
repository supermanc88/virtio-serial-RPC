# -*- coding: utf-8 -*-
"""
Guest RPC服务端主模块
实现virtio-serial RPC服务端
"""

import signal
import logging
import sys
import os
import time
from typing import Optional, Dict, Any

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common.protocol import parse_request, build_response, HTTPRequest, HTTPResponse
from common.errors import ErrorCode, RPCError
from common.utils import Timer
from guest.device import VirtioSerialDevice
from guest.router import Router

logger = logging.getLogger(__name__)


class VirtioRPCServer:
    """Guest端RPC服务端"""
    
    def __init__(self, device_path: str, config: Optional[Dict] = None):
        """
        初始化服务端
        
        Args:
            device_path: virtio-serial字符设备路径
            config: 配置字典
        """
        self.device_path = device_path
        self.config = config or {}
        self.device = VirtioSerialDevice(
            device_path, 
            buffer_size=self.config.get('buffer_size', 65536)
        )
        self.router = Router()
        self.running = False
        self._start_time = 0
        
        # 注册默认处理器
        self._register_default_handlers()
    
    def _register_default_handlers(self):
        """注册默认的API处理器"""
        from guest.handlers import system, shell, file, service
        
        # 系统相关
        self.router.add_route("GET", "/api/v1/ping", system.handle_ping)
        self.router.add_route("GET", "/api/v1/system/info", system.handle_system_info)
        self.router.add_route("GET", "/api/v1/system/status", system.handle_system_status)
        
        # Shell命令执行
        self.router.add_route("POST", "/api/v1/shell/exec", shell.handle_shell_exec)
        
        # 文件操作
        self.router.add_route("POST", "/api/v1/file/upload", file.handle_file_upload)
        self.router.add_route("POST", "/api/v1/file/download", file.handle_file_download)
        self.router.add_route("GET", "/api/v1/file/info", file.handle_file_info)
        
        # 分块上传
        self.router.add_route("POST", "/api/v1/file/chunked/upload/init", file.handle_chunked_upload_init)
        self.router.add_route("POST", "/api/v1/file/chunked/upload/chunk", file.handle_chunked_upload_chunk)
        self.router.add_route("POST", "/api/v1/file/chunked/upload/finish", file.handle_chunked_upload_finish)
        self.router.add_route("POST", "/api/v1/file/chunked/upload/abort", file.handle_chunked_upload_abort)
        
        # 分块下载
        self.router.add_route("POST", "/api/v1/file/chunked/download", file.handle_chunked_download)
        self.router.add_route("GET", "/api/v1/file/chunked/download", file.handle_chunked_download)
        
        # 服务管理
        self.router.add_route("POST", "/api/v1/service/control", service.handle_service_control)
        
        logger.info("Registered default API handlers")
    
    def register_handler(self, method: str, path: str, handler):
        """
        注册自定义请求处理器
        
        Args:
            method: HTTP方法
            path: URL路径
            handler: 处理函数
        """
        self.router.add_route(method, path, handler)
    
    def _handle_request(self, raw_request: bytes) -> bytes:
        """
        处理请求
        
        Args:
            raw_request: 原始HTTP请求数据
        
        Returns:
            HTTP响应数据
        """
        timer = Timer()
        timer.start()
        request_id = None
        
        try:
            # 解析请求
            request = parse_request(raw_request)
            request_id = request.headers.get("X-Request-ID")
            
            logger.info(f"Received request: {request.method} {request.path}")
            logger.debug(f"Request body: {request.body}")
            
            # 路由匹配
            handler, path_params = self.router.match(request.method, request.path)
            
            if handler is None:
                raise RPCError(ErrorCode.ENDPOINT_NOT_FOUND, 
                             f"Endpoint not found: {request.method} {request.path}")
            
            # 获取查询参数
            query_params = self.router.get_query_params(request.path)
            
            # 构建上下文
            context = {
                "request": request,
                "path_params": path_params,
                "query_params": query_params,
                "body": request.body or {},
                "config": self.config,
                "uptime": int(time.time() - self._start_time) if self._start_time else 0,
            }
            
            # 调用处理器
            result = handler(context)
            
            # 构建响应
            timer.stop()
            response = build_response(
                code=ErrorCode.SUCCESS,
                data=result,
                request_id=request_id
            )
            response.headers["X-Response-Time"] = str(timer.elapsed_ms)
            
            logger.info(f"Request completed in {timer.elapsed_ms}ms")
            return response.to_bytes()
            
        except RPCError as e:
            timer.stop()
            logger.warning(f"RPC error: {e.code} - {e.message}")
            response = build_response(
                code=e.code,
                message=e.message,
                data=e.data,
                request_id=request_id
            )
            response.headers["X-Response-Time"] = str(timer.elapsed_ms)
            return response.to_bytes()
            
        except Exception as e:
            timer.stop()
            logger.exception(f"Internal error: {e}")
            response = build_response(
                code=ErrorCode.INTERNAL_ERROR,
                message=str(e),
                request_id=request_id
            )
            response.headers["X-Response-Time"] = str(timer.elapsed_ms)
            return response.to_bytes()
    
    def start(self):
        """启动服务"""
        logger.info(f"Starting VirtioRPC Server on {self.device_path}")
        
        # 打开设备
        if not self.device.open():
            logger.error(f"Failed to open device: {self.device_path}")
            return
        
        self.running = True
        self._start_time = time.time()
        
        # 设置信号处理
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
        
        logger.info(f"VirtioRPC Server started on {self.device_path}")
        
        # 主循环
        while self.running:
            try:
                # 读取请求
                request_data = self.device.read_request(timeout=5.0)
                
                if request_data:
                    # 处理请求
                    response_data = self._handle_request(request_data)
                    
                    # 发送响应
                    if not self.device.write(response_data):
                        logger.error("Failed to send response")
                        
            except Exception as e:
                logger.exception(f"Error in main loop: {e}")
                time.sleep(1)  # 避免错误时快速循环
        
        # 清理
        self.device.close()
        logger.info("VirtioRPC Server stopped")
    
    def stop(self):
        """停止服务"""
        logger.info("Stopping VirtioRPC Server...")
        self.running = False
    
    def _signal_handler(self, signum, frame):
        """信号处理器"""
        logger.info(f"Received signal {signum}")
        self.stop()


def main():
    """主入口函数"""
    import argparse
    import yaml
    
    # 配置日志
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    # 解析命令行参数
    parser = argparse.ArgumentParser(description='VirtioRPC Guest Server')
    parser.add_argument('--device', '-d', 
                       default='/dev/virtio-ports/test.vserial.0',
                       help='virtio-serial device path')
    parser.add_argument('--config', '-c',
                       help='Configuration file path')
    parser.add_argument('--debug', action='store_true',
                       help='Enable debug logging')
    
    args = parser.parse_args()
    
    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)
    
    # 加载配置
    config = {}
    if args.config and os.path.exists(args.config):
        with open(args.config, 'r') as f:
            config = yaml.safe_load(f) or {}
    
    # 从配置中获取设备路径（命令行参数优先）
    device_path = args.device
    if device_path == '/dev/virtio-ports/test.vserial.0' and 'device' in config:
        device_path = config['device'].get('path', device_path)
    
    # 创建并启动服务
    server = VirtioRPCServer(device_path, config)
    server.start()


if __name__ == '__main__':
    main()
