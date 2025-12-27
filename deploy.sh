#!/bin/bash
# virtio-serial RPC 一键部署脚本
# 用于将代码部署到远端Host和Guest设备

set -e

# 配置
PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
HOST_IP="${HOST_IP:-10.210.20.17}"
GUEST_IP="${GUEST_IP:-10.210.23.3}"
REMOTE_DIR="/opt/virtio-rpc"

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# 检查连通性
check_connectivity() {
    log_info "检查远端连通性..."
    
    if ! ssh -o ConnectTimeout=5 root@${HOST_IP} "echo 'Host连接成功'" > /dev/null 2>&1; then
        log_error "无法连接到Host: ${HOST_IP}"
        exit 1
    fi
    log_info "Host (${HOST_IP}) 连接成功"
    
    if ! ssh -o ConnectTimeout=5 root@${GUEST_IP} "echo 'Guest连接成功'" > /dev/null 2>&1; then
        log_error "无法连接到Guest: ${GUEST_IP}"
        exit 1
    fi
    log_info "Guest (${GUEST_IP}) 连接成功"
}

# 创建远端目录
create_remote_dirs() {
    log_info "创建远端目录..."
    
    ssh root@${HOST_IP} "mkdir -p ${REMOTE_DIR}/{host,common,logs}"
    ssh root@${GUEST_IP} "mkdir -p ${REMOTE_DIR}/{guest,common,logs}"
    
    log_info "远端目录创建完成"
}

# 同步代码
sync_code() {
    log_info "同步代码到远端..."
    
    # 同步common模块到两端
    log_info "同步common模块..."
    rsync -avz --delete \
        --exclude='__pycache__' \
        --exclude='*.pyc' \
        "${PROJECT_DIR}/common/" "root@${HOST_IP}:${REMOTE_DIR}/common/"
    
    rsync -avz --delete \
        --exclude='__pycache__' \
        --exclude='*.pyc' \
        "${PROJECT_DIR}/common/" "root@${GUEST_IP}:${REMOTE_DIR}/common/"
    
    # 同步host模块
    log_info "同步host模块..."
    rsync -avz --delete \
        --exclude='__pycache__' \
        --exclude='*.pyc' \
        "${PROJECT_DIR}/host/" "root@${HOST_IP}:${REMOTE_DIR}/host/"
    
    # 同步guest模块
    log_info "同步guest模块..."
    rsync -avz --delete \
        --exclude='__pycache__' \
        --exclude='*.pyc' \
        "${PROJECT_DIR}/guest/" "root@${GUEST_IP}:${REMOTE_DIR}/guest/"
    
    # 同步requirements.txt
    scp "${PROJECT_DIR}/requirements.txt" "root@${HOST_IP}:${REMOTE_DIR}/"
    scp "${PROJECT_DIR}/requirements.txt" "root@${GUEST_IP}:${REMOTE_DIR}/"
    
    log_info "代码同步完成"
}

# 安装依赖
install_deps() {
    log_info "安装依赖..."
    
    # 尝试在Host上安装
    log_info "在Host上安装依赖..."
    ssh root@${HOST_IP} "cd ${REMOTE_DIR} && pip3 install -r requirements.txt" || {
        log_warn "Host依赖安装失败，可能需要手动安装或配置代理"
    }
    
    # 尝试在Guest上安装
    log_info "在Guest上安装依赖..."
    ssh root@${GUEST_IP} "cd ${REMOTE_DIR} && pip3 install -r requirements.txt" || {
        log_warn "Guest依赖安装失败，可能需要手动安装或配置代理"
    }
}

# 创建systemd服务
create_systemd_service() {
    log_info "创建systemd服务..."
    
    ssh root@${GUEST_IP} "cat > /etc/systemd/system/virtio-rpc.service << 'EOF'
[Unit]
Description=Virtio Serial RPC Server
After=network.target

[Service]
Type=simple
WorkingDirectory=${REMOTE_DIR}
ExecStart=/usr/bin/python3 -m guest.server --config ${REMOTE_DIR}/guest/config.yaml
Restart=always
RestartSec=5
StandardOutput=append:${REMOTE_DIR}/logs/server.log
StandardError=append:${REMOTE_DIR}/logs/server.log

[Install]
WantedBy=multi-user.target
EOF"

    ssh root@${GUEST_IP} "systemctl daemon-reload"
    log_info "systemd服务创建完成"
}

# 启动服务
start_service() {
    log_info "启动Guest服务..."
    ssh root@${GUEST_IP} "systemctl restart virtio-rpc" || {
        log_warn "systemd启动失败，尝试直接启动..."
        ssh root@${GUEST_IP} "cd ${REMOTE_DIR} && nohup python3 -m guest.server > logs/server.log 2>&1 &"
    }
    
    sleep 2
    log_info "检查服务状态..."
    ssh root@${GUEST_IP} "systemctl status virtio-rpc --no-pager" || \
        ssh root@${GUEST_IP} "ps aux | grep 'guest.server' | grep -v grep" || \
        log_warn "服务可能未正常启动，请检查日志"
}

# 测试连接
test_connection() {
    log_info "测试连接..."
    
    VIRTIO_SOCKET="/var/lib/libvirt/qemu/channel/target/domain-26-UCypher-newtest1/test.vserial.0"
    
    ssh root@${HOST_IP} "cd ${REMOTE_DIR} && python3 -m host.cli --socket ${VIRTIO_SOCKET} ping" && {
        log_info "连接测试成功！"
    } || {
        log_warn "连接测试失败，请检查："
        log_warn "1. Guest服务是否已启动"
        log_warn "2. virtio-serial设备是否存在"
        log_warn "3. UNIX域套接字路径是否正确"
    }
}

# 显示使用说明
show_usage() {
    echo ""
    log_info "部署完成！使用说明："
    echo ""
    echo "在Host上测试："
    echo "  export VIRTIO_SOCKET=/var/lib/libvirt/qemu/channel/target/domain-26-UCypher-newtest1/test.vserial.0"
    echo "  cd ${REMOTE_DIR}"
    echo "  python3 -m host.cli --socket \$VIRTIO_SOCKET ping"
    echo "  python3 -m host.cli --socket \$VIRTIO_SOCKET info"
    echo "  python3 -m host.cli --socket \$VIRTIO_SOCKET exec 'uname -a'"
    echo ""
    echo "管理Guest服务："
    echo "  ssh root@${GUEST_IP} 'systemctl status virtio-rpc'"
    echo "  ssh root@${GUEST_IP} 'systemctl restart virtio-rpc'"
    echo "  ssh root@${GUEST_IP} 'tail -f ${REMOTE_DIR}/logs/server.log'"
    echo ""
}

# 主流程
main() {
    echo "========================================"
    echo "  virtio-serial RPC 部署脚本"
    echo "========================================"
    echo ""
    echo "Host: ${HOST_IP}"
    echo "Guest: ${GUEST_IP}"
    echo "远端目录: ${REMOTE_DIR}"
    echo ""
    
    check_connectivity
    create_remote_dirs
    sync_code
    install_deps
    create_systemd_service
    start_service
    test_connection
    show_usage
}

# 支持单独执行某个步骤
case "${1:-}" in
    check)
        check_connectivity
        ;;
    sync)
        sync_code
        ;;
    deps)
        install_deps
        ;;
    start)
        start_service
        ;;
    test)
        test_connection
        ;;
    *)
        main
        ;;
esac
