# -*- coding: utf-8 -*-
"""
Host端命令行工具
提供命令行接口与Guest端进行通信
"""

import argparse
import json
import sys
import os
import logging

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from host.client import VirtioRPCClient
from common.errors import RPCError


def setup_logging(debug: bool = False):
    """配置日志"""
    level = logging.DEBUG if debug else logging.WARNING
    logging.basicConfig(
        level=level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )


def print_result(result: dict, raw: bool = False):
    """打印结果"""
    if raw:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        # 友好格式输出
        code = result.get('code', 0)
        if code == 0:
            data = result.get('data', {})
            if isinstance(data, dict):
                # 特殊处理stdout输出
                if 'stdout' in data:
                    print(data['stdout'], end='')
                    if data.get('stderr'):
                        print(f"\n[stderr]: {data['stderr']}", file=sys.stderr)
                else:
                    print(json.dumps(data, ensure_ascii=False, indent=2))
            else:
                print(data)
        else:
            print(f"Error [{code}]: {result.get('message', 'Unknown error')}", file=sys.stderr)
            if result.get('data'):
                print(json.dumps(result['data'], ensure_ascii=False, indent=2), file=sys.stderr)


def cmd_ping(client: VirtioRPCClient, args):
    """心跳检测"""
    result = client.ping()
    print_result(result, args.raw)


def cmd_info(client: VirtioRPCClient, args):
    """获取系统信息"""
    result = client.get_system_info()
    print_result(result, args.raw)


def cmd_status(client: VirtioRPCClient, args):
    """获取系统状态"""
    result = client.get_system_status()
    print_result(result, args.raw)


def cmd_exec(client: VirtioRPCClient, args):
    """执行命令"""
    result = client.exec_command(
        args.command,
        timeout=args.timeout,
        working_dir=args.workdir,
    )
    print_result(result, args.raw)


def cmd_upload(client: VirtioRPCClient, args):
    """上传文件"""
    import os
    
    local_path = args.local
    if not os.path.exists(local_path):
        print(f"Error: Local file not found: {local_path}", file=sys.stderr)
        sys.exit(1)
    
    file_size = os.path.getsize(local_path)
    chunk_size = getattr(args, 'chunk_size', 2 * 1024 * 1024)
    
    # 进度显示回调
    def progress_callback(uploaded, total):
        percent = uploaded * 100 // total if total > 0 else 100
        bar_len = 30
        filled = int(bar_len * uploaded / total) if total > 0 else bar_len
        bar = '=' * filled + '-' * (bar_len - filled)
        print(f"\r[{bar}] {percent}% ({uploaded}/{total} bytes)", end='', flush=True)
    
    # 小文件直接上传，大文件分块上传
    if file_size <= chunk_size:
        result = client.upload_file(
            args.local,
            args.remote,
            mode=args.mode,
            overwrite=not args.no_overwrite,
        )
    else:
        print(f"Large file detected ({file_size} bytes), using chunked upload...")
        result = client.chunked_upload_file(
            args.local,
            args.remote,
            mode=args.mode,
            overwrite=not args.no_overwrite,
            chunk_size=chunk_size,
            progress_callback=progress_callback if not args.raw else None,
        )
        if not args.raw:
            print()  # 换行
    
    if not args.raw:
        data = result.get('data', {})
        print(f"Upload successful: {data.get('path')} ({data.get('size')} bytes)")
        if data.get('md5'):
            print(f"MD5: {data.get('md5')}")
    else:
        print_result(result, args.raw)


def cmd_download(client: VirtioRPCClient, args):
    """下载文件"""
    # 先获取文件信息确定大小
    info_result = client.get_file_info(args.remote)
    file_info = info_result.get('data', {})
    
    if not file_info.get('exists', False):
        print(f"Error: Remote file not found: {args.remote}", file=sys.stderr)
        sys.exit(1)
    
    file_size = file_info.get('size', 0)
    chunk_size = getattr(args, 'chunk_size', 2 * 1024 * 1024)
    
    # 进度显示回调
    def progress_callback(downloaded, total):
        percent = downloaded * 100 // total if total > 0 else 100
        bar_len = 30
        filled = int(bar_len * downloaded / total) if total > 0 else bar_len
        bar = '=' * filled + '-' * (bar_len - filled)
        print(f"\r[{bar}] {percent}% ({downloaded}/{total} bytes)", end='', flush=True)
    
    # 小文件直接下载，大文件分块下载
    if file_size <= chunk_size:
        result = client.download_file(args.remote, args.local)
    else:
        print(f"Large file detected ({file_size} bytes), using chunked download...")
        result = client.chunked_download_file(
            args.remote,
            args.local,
            chunk_size=chunk_size,
            progress_callback=progress_callback if not args.raw else None,
        )
        if not args.raw:
            print()  # 换行
    
    if not args.raw:
        data = result.get('data', {})
        print(f"Download successful: {args.local} ({data.get('size')} bytes)")
        if data.get('md5'):
            print(f"MD5: {data.get('md5')}")
    else:
        print_result(result, args.raw)


def cmd_file_info(client: VirtioRPCClient, args):
    """查询文件信息"""
    result = client.get_file_info(args.path)
    print_result(result, args.raw)


def cmd_service(client: VirtioRPCClient, args):
    """服务管理"""
    result = client.control_service(args.name, args.action)
    print_result(result, args.raw)


def main():
    """主入口"""
    parser = argparse.ArgumentParser(
        description='VirtioRPC Host CLI - 与Guest虚拟机通信的命令行工具',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  %(prog)s --socket /path/to/socket ping
  %(prog)s --socket /path/to/socket info
  %(prog)s --socket /path/to/socket exec "ls -la /tmp"
  %(prog)s --socket /path/to/socket upload local.txt /tmp/remote.txt
  %(prog)s --socket /path/to/socket download /tmp/remote.txt local.txt
  %(prog)s --socket /path/to/socket service nginx status

环境变量:
  VIRTIO_SOCKET  可代替 --socket 参数
        """
    )
    
    # socket 路径支持环境变量
    default_socket = os.environ.get('VIRTIO_SOCKET')
    parser.add_argument('--socket', '-s', required=(default_socket is None),
                       default=default_socket,
                       help='virtio-serial UNIX域套接字路径 (环境变量: VIRTIO_SOCKET)')
    parser.add_argument('--config', '-c',
                       help='配置文件路径')
    parser.add_argument('--debug', '-d', action='store_true',
                       help='启用调试输出')
    parser.add_argument('--raw', '-r', action='store_true',
                       help='输出原始JSON格式')
    parser.add_argument('--timeout', '-t', type=int, default=30,
                       help='默认超时时间（秒）')
    
    subparsers = parser.add_subparsers(dest='command', help='可用命令')
    
    # ping命令
    parser_ping = subparsers.add_parser('ping', help='心跳检测')
    parser_ping.set_defaults(func=cmd_ping)
    
    # info命令
    parser_info = subparsers.add_parser('info', help='获取系统信息')
    parser_info.set_defaults(func=cmd_info)
    
    # status命令
    parser_status = subparsers.add_parser('status', help='获取系统状态')
    parser_status.set_defaults(func=cmd_status)
    
    # exec命令
    parser_exec = subparsers.add_parser('exec', help='执行Shell命令')
    parser_exec.add_argument('command', help='要执行的命令')
    parser_exec.add_argument('--timeout', type=int, default=30,
                            help='命令执行超时时间（秒）')
    parser_exec.add_argument('--workdir', '-w',
                            help='工作目录')
    parser_exec.set_defaults(func=cmd_exec)
    
    # upload命令
    parser_upload = subparsers.add_parser('upload', help='上传文件到Guest')
    parser_upload.add_argument('local', help='本地文件路径')
    parser_upload.add_argument('remote', help='Guest上的目标路径')
    parser_upload.add_argument('--mode', '-m', default='0644',
                              help='文件权限（默认0644）')
    parser_upload.add_argument('--no-overwrite', action='store_true',
                              help='不覆盖已存在的文件')
    parser_upload.add_argument('--chunk-size', type=int, default=2*1024*1024,
                              help='分块大小（默认2MB）')
    parser_upload.set_defaults(func=cmd_upload)
    
    # download命令
    parser_download = subparsers.add_parser('download', help='从Guest下载文件')
    parser_download.add_argument('remote', help='Guest上的文件路径')
    parser_download.add_argument('local', help='本地保存路径')
    parser_download.add_argument('--chunk-size', type=int, default=2*1024*1024,
                              help='分块大小（默认2MB）')
    parser_download.set_defaults(func=cmd_download)
    
    # file-info命令
    parser_file_info = subparsers.add_parser('file-info', help='查询文件信息')
    parser_file_info.add_argument('path', help='文件路径')
    parser_file_info.set_defaults(func=cmd_file_info)
    
    # service命令
    parser_service = subparsers.add_parser('service', help='服务管理')
    parser_service.add_argument('name', help='服务名称')
    parser_service.add_argument('action', 
                               choices=['start', 'stop', 'restart', 'status', 'enable', 'disable'],
                               help='操作类型')
    parser_service.set_defaults(func=cmd_service)
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        sys.exit(1)
    
    # 配置日志
    setup_logging(args.debug)
    
    # 加载配置
    config = {}
    if args.config and os.path.exists(args.config):
        import yaml
        with open(args.config, 'r') as f:
            config = yaml.safe_load(f) or {}
    
    # 创建客户端
    client = VirtioRPCClient(args.socket, config)
    
    try:
        # 连接
        if not client.connect():
            print(f"Error: Failed to connect to {args.socket}", file=sys.stderr)
            sys.exit(1)
        
        # 执行命令
        args.func(client, args)
        
    except RPCError as e:
        print(f"Error [{e.code}]: {e.message}", file=sys.stderr)
        if e.data:
            print(json.dumps(e.data, ensure_ascii=False, indent=2), file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nInterrupted", file=sys.stderr)
        sys.exit(130)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        if args.debug:
            import traceback
            traceback.print_exc()
        sys.exit(1)
    finally:
        client.disconnect()


if __name__ == '__main__':
    main()
