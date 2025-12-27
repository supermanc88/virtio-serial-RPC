# -*- coding: utf-8 -*-
"""
服务管理处理器
处理systemd服务管理相关的API
"""

import subprocess
import logging
from typing import Dict, Any, Optional

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from common.errors import ErrorCode, RPCError

logger = logging.getLogger(__name__)

# 允许的服务操作
ALLOWED_ACTIONS = {'start', 'stop', 'restart', 'status', 'enable', 'disable', 'reload'}


def run_systemctl(action: str, service_name: str, timeout: int = 30) -> Dict[str, Any]:
    """
    执行systemctl命令
    
    Args:
        action: 操作类型
        service_name: 服务名称
        timeout: 超时时间
    
    Returns:
        执行结果字典
    """
    cmd = ['systemctl', action, service_name]
    
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            timeout=timeout,
        )
        
        return {
            "exit_code": result.returncode,
            "stdout": result.stdout.decode('utf-8', errors='replace'),
            "stderr": result.stderr.decode('utf-8', errors='replace'),
        }
    except subprocess.TimeoutExpired:
        raise RPCError(ErrorCode.CMD_TIMEOUT, f"systemctl command timed out")
    except FileNotFoundError:
        raise RPCError(ErrorCode.CMD_NOT_FOUND, "systemctl command not found")
    except Exception as e:
        raise RPCError(ErrorCode.CMD_EXEC_FAILED, f"Failed to execute systemctl: {e}")


def get_service_status(service_name: str) -> Dict[str, Any]:
    """
    获取服务状态详情
    
    Args:
        service_name: 服务名称
    
    Returns:
        服务状态字典
    """
    status = {
        "name": service_name,
        "status": "unknown",
        "active": False,
        "enabled": False,
        "pid": None,
        "description": "",
    }
    
    try:
        # 检查服务是否active
        result = subprocess.run(
            ['systemctl', 'is-active', service_name],
            capture_output=True,
            timeout=10,
        )
        active_status = result.stdout.decode('utf-8').strip()
        status["status"] = active_status
        status["active"] = (active_status == "active")
        
        # 检查服务是否enabled
        result = subprocess.run(
            ['systemctl', 'is-enabled', service_name],
            capture_output=True,
            timeout=10,
        )
        enabled_status = result.stdout.decode('utf-8').strip()
        status["enabled"] = (enabled_status == "enabled")
        
        # 获取详细状态（包括PID）
        result = subprocess.run(
            ['systemctl', 'show', service_name, 
             '--property=MainPID,Description,ActiveState,SubState'],
            capture_output=True,
            timeout=10,
        )
        
        for line in result.stdout.decode('utf-8').split('\n'):
            if '=' in line:
                key, value = line.split('=', 1)
                if key == 'MainPID':
                    pid = int(value) if value.isdigit() else None
                    status["pid"] = pid if pid and pid > 0 else None
                elif key == 'Description':
                    status["description"] = value
                elif key == 'SubState':
                    status["sub_state"] = value
        
    except subprocess.TimeoutExpired:
        logger.warning(f"Timeout getting status for service: {service_name}")
    except FileNotFoundError:
        logger.warning("systemctl not found")
    except Exception as e:
        logger.warning(f"Error getting service status: {e}")
    
    return status


def handle_service_control(context: Dict[str, Any]) -> Dict[str, Any]:
    """
    处理服务管理请求
    
    Args:
        context: 请求上下文，body中应包含:
            - name: 服务名称
            - action: 操作类型 (start, stop, restart, status, enable, disable, reload)
    
    Returns:
        操作结果字典
    """
    body = context.get("body", {})
    
    # 获取参数
    service_name = body.get("name")
    action = body.get("action", "status")
    
    if not service_name:
        raise RPCError(ErrorCode.MISSING_REQUIRED, "Missing required parameter: name")
    
    # 验证操作类型
    action = action.lower()
    if action not in ALLOWED_ACTIONS:
        raise RPCError(
            ErrorCode.INVALID_PARAMS,
            f"Invalid action: {action}. Allowed: {', '.join(ALLOWED_ACTIONS)}"
        )
    
    # 简单的服务名称验证（防止命令注入）
    if not service_name.replace('-', '').replace('_', '').replace('.', '').isalnum():
        raise RPCError(ErrorCode.INVALID_PARAMS, f"Invalid service name: {service_name}")
    
    # 执行操作
    if action == "status":
        # 状态查询不需要执行命令，直接获取状态
        status = get_service_status(service_name)
        return status
    
    # 执行systemctl命令
    result = run_systemctl(action, service_name)
    
    # 获取操作后的状态
    status = get_service_status(service_name)
    
    # 判断操作是否成功
    success = (result["exit_code"] == 0)
    
    if not success:
        raise RPCError(
            ErrorCode.CMD_EXEC_FAILED,
            f"Failed to {action} service {service_name}",
            data={
                "exit_code": result["exit_code"],
                "stderr": result["stderr"],
                "status": status,
            }
        )
    
    return {
        "name": service_name,
        "action": action,
        "success": success,
        "status": status["status"],
        "active": status["active"],
        "enabled": status["enabled"],
        "pid": status["pid"],
    }
