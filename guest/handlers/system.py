# -*- coding: utf-8 -*-
"""
系统信息处理器
处理系统信息和状态查询相关的API
"""

import platform
import time
import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)


def handle_ping(context: Dict[str, Any]) -> Dict[str, Any]:
    """
    处理心跳请求
    
    Args:
        context: 请求上下文
    
    Returns:
        响应数据
    """
    return {
        "timestamp": int(time.time()),
        "uptime": context.get("uptime", 0),
        "message": "pong"
    }


def handle_system_info(context: Dict[str, Any]) -> Dict[str, Any]:
    """
    获取系统信息
    
    Args:
        context: 请求上下文
    
    Returns:
        系统信息字典
    """
    import socket
    
    try:
        # 尝试导入psutil获取更多信息
        import psutil
        memory = psutil.virtual_memory()
        memory_total = memory.total
        memory_available = memory.available
        cpu_count = psutil.cpu_count()
    except ImportError:
        import os
        memory_total = 0
        memory_available = 0
        cpu_count = os.cpu_count() or 1
        
        # 尝试从/proc/meminfo获取内存信息
        try:
            with open('/proc/meminfo', 'r') as f:
                for line in f:
                    if line.startswith('MemTotal:'):
                        memory_total = int(line.split()[1]) * 1024
                    elif line.startswith('MemAvailable:'):
                        memory_available = int(line.split()[1]) * 1024
        except:
            pass
    
    uname = platform.uname()
    
    # 获取系统版本信息
    try:
        with open('/etc/os-release', 'r') as f:
            os_release = {}
            for line in f:
                if '=' in line:
                    key, value = line.strip().split('=', 1)
                    os_release[key] = value.strip('"')
        os_version = os_release.get('PRETTY_NAME', f"{uname.system} {uname.release}")
    except:
        os_version = f"{uname.system} {uname.release}"
    
    return {
        "hostname": socket.gethostname(),
        "os": uname.system,
        "os_version": os_version,
        "kernel": uname.release,
        "arch": uname.machine,
        "cpu_count": cpu_count,
        "memory_total": memory_total,
        "memory_available": memory_available,
        "python_version": platform.python_version(),
    }


def handle_system_status(context: Dict[str, Any]) -> Dict[str, Any]:
    """
    获取系统状态
    
    Args:
        context: 请求上下文
    
    Returns:
        系统状态字典
    """
    result = {
        "cpu_usage": 0.0,
        "memory_usage": 0.0,
        "disk_usage": {},
        "load_average": [0.0, 0.0, 0.0],
        "process_count": 0,
    }
    
    try:
        import psutil
        
        # CPU使用率
        result["cpu_usage"] = psutil.cpu_percent(interval=0.1)
        
        # 内存使用率
        memory = psutil.virtual_memory()
        result["memory_usage"] = memory.percent
        
        # 磁盘使用率
        disk_usage = {}
        for partition in psutil.disk_partitions():
            try:
                usage = psutil.disk_usage(partition.mountpoint)
                disk_usage[partition.mountpoint] = round(usage.percent, 1)
            except:
                pass
        result["disk_usage"] = disk_usage
        
        # 进程数
        result["process_count"] = len(psutil.pids())
        
    except ImportError:
        logger.warning("psutil not available, using fallback methods")
        
        # 从/proc获取信息
        try:
            # Load average
            with open('/proc/loadavg', 'r') as f:
                parts = f.read().split()
                result["load_average"] = [float(parts[0]), float(parts[1]), float(parts[2])]
        except:
            pass
        
        try:
            # 进程数
            import os
            result["process_count"] = len([d for d in os.listdir('/proc') if d.isdigit()])
        except:
            pass
        
        try:
            # CPU使用率（简单计算）
            with open('/proc/stat', 'r') as f:
                line = f.readline()
                parts = line.split()
                if parts[0] == 'cpu':
                    total = sum(int(x) for x in parts[1:])
                    idle = int(parts[4])
                    result["cpu_usage"] = round((1 - idle / total) * 100, 1) if total > 0 else 0
        except:
            pass
        
        try:
            # 内存使用率
            with open('/proc/meminfo', 'r') as f:
                mem_info = {}
                for line in f:
                    if ':' in line:
                        key, value = line.split(':')
                        mem_info[key.strip()] = int(value.split()[0])
                
                total = mem_info.get('MemTotal', 0)
                available = mem_info.get('MemAvailable', mem_info.get('MemFree', 0))
                if total > 0:
                    result["memory_usage"] = round((1 - available / total) * 100, 1)
        except:
            pass
        
        try:
            # 磁盘使用率
            import os
            stat = os.statvfs('/')
            total = stat.f_blocks * stat.f_frsize
            free = stat.f_bfree * stat.f_frsize
            if total > 0:
                result["disk_usage"]["/"] = round((1 - free / total) * 100, 1)
        except:
            pass
    
    # Load average (通用方法)
    try:
        import os
        result["load_average"] = list(os.getloadavg())
    except:
        pass
    
    return result
