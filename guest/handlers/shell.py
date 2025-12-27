# -*- coding: utf-8 -*-
"""
Shell命令执行处理器
处理Shell命令执行相关的API
"""

import subprocess
import shlex
import os
import logging
from typing import Dict, Any, Optional

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from common.errors import ErrorCode, RPCError
from common.utils import Timer

logger = logging.getLogger(__name__)

# 默认命令白名单
DEFAULT_ALLOWED_COMMANDS = {
    'ls', 'cat', 'head', 'tail', 'grep', 'find', 'wc',
    'df', 'free', 'top', 'ps', 'netstat', 'ss', 'ip',
    'systemctl', 'service', 'journalctl',
    'date', 'uptime', 'hostname', 'uname', 'whoami',
    'pwd', 'echo', 'env', 'printenv',
    'which', 'type', 'file', 'stat',
    'id', 'groups', 'last', 'who', 'w',
    'dmidecode', 'lscpu', 'lsmem', 'lsblk', 'lspci', 'lsusb',
    'mount', 'fdisk', 'blkid',
    'iptables', 'firewall-cmd',
    'docker', 'podman', 'crictl',
}

# 危险字符
DANGEROUS_CHARS = [';', '&&', '||', '`', '$(', '${', '\n', '\r']


def is_command_safe(command: str, allowed_commands: Optional[set] = None) -> bool:
    """
    检查命令是否安全
    
    Args:
        command: 要检查的命令
        allowed_commands: 允许的命令集合
    
    Returns:
        命令是否安全
    """
    if allowed_commands is None:
        allowed_commands = DEFAULT_ALLOWED_COMMANDS
    
    # 空白名单表示允许所有命令
    if not allowed_commands:
        return True
    
    # 检查危险字符
    for char in DANGEROUS_CHARS:
        if char in command:
            # 允许管道符（但会被shell处理）
            if char not in ['|']:
                logger.warning(f"Dangerous character found in command: {char}")
                return False
    
    # 获取命令的基础命令名
    try:
        parts = shlex.split(command)
        if not parts:
            return False
        base_cmd = os.path.basename(parts[0])
    except ValueError:
        return False
    
    # 检查是否在白名单中
    if base_cmd not in allowed_commands:
        logger.warning(f"Command not in allowed list: {base_cmd}")
        return False
    
    return True


def handle_shell_exec(context: Dict[str, Any]) -> Dict[str, Any]:
    """
    执行Shell命令
    
    Args:
        context: 请求上下文，body中应包含:
            - command: 要执行的命令
            - timeout: 超时时间（秒），默认30
            - working_dir: 工作目录（可选）
            - env: 环境变量（可选）
    
    Returns:
        执行结果字典
    """
    body = context.get("body", {})
    config = context.get("config", {})
    
    # 获取命令
    command = body.get("command")
    if not command:
        raise RPCError(ErrorCode.MISSING_REQUIRED, "Missing required parameter: command")
    
    # 获取参数
    timeout = body.get("timeout", 30)
    working_dir = body.get("working_dir")
    env_vars = body.get("env", {})
    
    # 安全检查
    security_config = config.get("security", {})
    allowed_commands = security_config.get("allowed_commands")
    if allowed_commands is not None:
        allowed_set = set(allowed_commands) if allowed_commands else None
    else:
        allowed_set = DEFAULT_ALLOWED_COMMANDS
    
    # 如果配置为空列表，使用默认白名单；如果配置为None，也使用默认白名单
    # 只有显式配置了命令列表时才使用配置的白名单
    if not is_command_safe(command, allowed_set):
        raise RPCError(
            ErrorCode.PERMISSION_DENIED,
            f"Command not allowed: {command.split()[0] if command else ''}"
        )
    
    # 验证工作目录
    if working_dir and not os.path.isdir(working_dir):
        raise RPCError(ErrorCode.FILE_NOT_FOUND, f"Working directory not found: {working_dir}")
    
    # 准备环境变量
    env = os.environ.copy()
    env.update(env_vars)
    
    # 执行命令
    timer = Timer()
    timer.start()
    
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            timeout=timeout,
            cwd=working_dir,
            env=env,
        )
        timer.stop()
        
        return {
            "exit_code": result.returncode,
            "stdout": result.stdout.decode('utf-8', errors='replace'),
            "stderr": result.stderr.decode('utf-8', errors='replace'),
            "duration_ms": timer.elapsed_ms,
        }
        
    except subprocess.TimeoutExpired:
        timer.stop()
        raise RPCError(
            ErrorCode.CMD_TIMEOUT,
            f"Command timed out after {timeout} seconds",
            data={"timeout": timeout, "duration_ms": timer.elapsed_ms}
        )
    except FileNotFoundError:
        timer.stop()
        raise RPCError(
            ErrorCode.CMD_NOT_FOUND,
            f"Command not found: {command.split()[0] if command else ''}"
        )
    except Exception as e:
        timer.stop()
        raise RPCError(
            ErrorCode.CMD_EXEC_FAILED,
            f"Command execution failed: {str(e)}",
            data={"duration_ms": timer.elapsed_ms}
        )

if __name__ == "__main__":
    # 简单测试
    test_context = {
        "body": {
            "command": "ls -l /",
            "timeout": 10,
        },
        "config": {
            "security": {
                "allowed_commands": ["ls", "cat", "echo"]
            }
        }
    }
    
    try:
        result = handle_shell_exec(test_context)
        print("Command executed successfully:")
        print(result)
    except RPCError as e:
        print(f"RPC Error {e.code}: {e.message}")