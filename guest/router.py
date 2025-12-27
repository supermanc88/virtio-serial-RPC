# -*- coding: utf-8 -*-
"""
请求路由模块
根据HTTP请求路径和方法路由到对应的处理器
"""

import re
import logging
from typing import Callable, Dict, List, Tuple, Optional, Any

logger = logging.getLogger(__name__)


class Route:
    """路由定义类"""
    
    def __init__(self, method: str, path: str, handler: Callable):
        """
        初始化路由
        
        Args:
            method: HTTP方法 (GET, POST等)
            path: URL路径模式（支持简单的参数匹配）
            handler: 处理函数
        """
        self.method = method.upper()
        self.path = path
        self.handler = handler
        
        # 将路径模式转换为正则表达式
        # 支持 {param} 格式的路径参数
        pattern = re.sub(r'\{(\w+)\}', r'(?P<\1>[^/]+)', path)
        self.pattern = re.compile(f"^{pattern}$")
    
    def match(self, method: str, path: str) -> Optional[Dict[str, str]]:
        """
        检查请求是否匹配此路由
        
        Args:
            method: HTTP方法
            path: 请求路径
        
        Returns:
            匹配的路径参数字典，不匹配返回None
        """
        if method.upper() != self.method:
            return None
        
        # 移除查询字符串
        if "?" in path:
            path = path.split("?")[0]
        
        match = self.pattern.match(path)
        if match:
            return match.groupdict()
        return None


class Router:
    """请求路由器"""
    
    def __init__(self):
        self.routes: List[Route] = []
    
    def add_route(self, method: str, path: str, handler: Callable):
        """
        添加路由
        
        Args:
            method: HTTP方法
            path: URL路径模式
            handler: 处理函数
        """
        route = Route(method, path, handler)
        self.routes.append(route)
        logger.debug(f"Added route: {method} {path}")
    
    def get(self, path: str):
        """GET路由装饰器"""
        def decorator(handler: Callable):
            self.add_route("GET", path, handler)
            return handler
        return decorator
    
    def post(self, path: str):
        """POST路由装饰器"""
        def decorator(handler: Callable):
            self.add_route("POST", path, handler)
            return handler
        return decorator
    
    def route(self, method: str, path: str):
        """通用路由装饰器"""
        def decorator(handler: Callable):
            self.add_route(method, path, handler)
            return handler
        return decorator
    
    def match(self, method: str, path: str) -> Tuple[Optional[Callable], Dict[str, str]]:
        """
        匹配路由
        
        Args:
            method: HTTP方法
            path: 请求路径
        
        Returns:
            (处理函数, 路径参数) 元组，未找到返回 (None, {})
        """
        for route in self.routes:
            params = route.match(method, path)
            if params is not None:
                return route.handler, params
        return None, {}
    
    def get_query_params(self, path: str) -> Dict[str, str]:
        """
        从路径中提取查询参数
        
        Args:
            path: 请求路径（可能包含查询字符串）
        
        Returns:
            查询参数字典
        """
        params = {}
        if "?" in path:
            query_string = path.split("?", 1)[1]
            for param in query_string.split("&"):
                if "=" in param:
                    key, value = param.split("=", 1)
                    # URL解码
                    from urllib.parse import unquote
                    params[unquote(key)] = unquote(value)
        return params
