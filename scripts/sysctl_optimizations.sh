#!/bin/bash
# sysctl_optimizations.sh
# Phase 2 - Task 88: System kernel tuning for low-latency trading
#
# Educational purpose only - paper trading simulation
# Run with: sudo ./sysctl_optimizations.sh
#
# WARNING: These settings may affect system stability.
# Only use on dedicated trading systems.

set -e

echo "=== Polym Trading System Optimization ==="
echo "Educational purpose only - paper trading simulation"
echo ""

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    echo "Please run as root: sudo $0"
    exit 1
fi

# Backup current settings
BACKUP_FILE="/tmp/sysctl_backup_$(date +%Y%m%d_%H%M%S).conf"
sysctl -a > "$BACKUP_FILE" 2>/dev/null || true
echo "Backed up current settings to: $BACKUP_FILE"

echo ""
echo "Applying network optimizations..."

# TCP/IP Stack Optimizations
# --------------------------

# Increase socket buffer sizes
sysctl -w net.core.rmem_max=16777216 || true
sysctl -w net.core.wmem_max=16777216 || true
sysctl -w net.core.rmem_default=1048576 || true
sysctl -w net.core.wmem_default=1048576 || true

# TCP buffer sizes
sysctl -w net.ipv4.tcp_rmem="4096 1048576 16777216" || true
sysctl -w net.ipv4.tcp_wmem="4096 1048576 16777216" || true

# Enable TCP window scaling
sysctl -w net.ipv4.tcp_window_scaling=1 || true

# Reduce TCP TIME_WAIT
sysctl -w net.ipv4.tcp_tw_reuse=1 || true
sysctl -w net.ipv4.tcp_fin_timeout=15 || true

# Disable TCP slow start after idle
sysctl -w net.ipv4.tcp_slow_start_after_idle=0 || true

# Enable TCP timestamps for better RTT estimation
sysctl -w net.ipv4.tcp_timestamps=1 || true

# Increase pending connections queue
sysctl -w net.core.somaxconn=4096 || true
sysctl -w net.ipv4.tcp_max_syn_backlog=4096 || true

# Optimize TCP keepalive (detect dead connections faster)
sysctl -w net.ipv4.tcp_keepalive_time=60 || true
sysctl -w net.ipv4.tcp_keepalive_intvl=10 || true
sysctl -w net.ipv4.tcp_keepalive_probes=6 || true

echo ""
echo "Applying memory optimizations..."

# Memory Optimizations
# -------------------

# Reduce swappiness for better latency
sysctl -w vm.swappiness=10 || true

# Increase dirty ratio for better I/O batching
sysctl -w vm.dirty_ratio=60 || true
sysctl -w vm.dirty_background_ratio=2 || true

# Disable transparent huge pages (can cause latency spikes)
if [ -f /sys/kernel/mm/transparent_hugepage/enabled ]; then
    echo never > /sys/kernel/mm/transparent_hugepage/enabled || true
fi

echo ""
echo "Applying scheduler optimizations..."

# Scheduler Optimizations
# ----------------------

# Reduce scheduler migration cost (better CPU affinity)
sysctl -w kernel.sched_migration_cost_ns=5000000 || true

# Reduce scheduler latency
sysctl -w kernel.sched_min_granularity_ns=1000000 || true
sysctl -w kernel.sched_wakeup_granularity_ns=500000 || true

# Increase file descriptor limits
sysctl -w fs.file-max=2097152 || true

echo ""
echo "Applying interrupt optimizations..."

# Interrupt Coalescing (if supported)
# ----------------------------------

# Find network interfaces and optimize them
for iface in $(ls /sys/class/net/ | grep -v lo); do
    # Try to disable interrupt coalescing
    ethtool -C "$iface" rx-usecs 0 tx-usecs 0 2>/dev/null || true
    
    # Enable GRO (Generic Receive Offload)
    ethtool -K "$iface" gro on 2>/dev/null || true
done

echo ""
echo "=== Optimization Summary ==="
echo ""
echo "Network:"
echo "  - Increased socket buffers to 16MB"
echo "  - Enabled TCP window scaling"
echo "  - Reduced TCP TIME_WAIT timeout"
echo "  - Disabled TCP slow start after idle"
echo ""
echo "Memory:"
echo "  - Reduced swappiness to 10"
echo "  - Disabled transparent huge pages"
echo ""
echo "Scheduler:"
echo "  - Optimized migration costs"
echo "  - Increased file descriptor limits"
echo ""
echo "To make these changes permanent, add them to /etc/sysctl.conf"
echo "Backup saved to: $BACKUP_FILE"
echo ""
echo "=== Done ==="
