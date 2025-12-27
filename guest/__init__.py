# -*- coding: utf-8 -*-
"""
Guest module for virtio-serial RPC
Guest端（虚拟机内）RPC服务模块
"""

from .server import VirtioRPCServer
from .device import VirtioSerialDevice

__all__ = [
    'VirtioRPCServer',
    'VirtioSerialDevice',
]
