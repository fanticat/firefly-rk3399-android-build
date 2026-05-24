#!/bin/bash
# Build and repack Firefly-RK3399 Android 12 firmware
# Based on Firefly wiki: img_unpack -> afptool -> replace boot.img -> afptool -pack -> img_maker
#
# Tools: afptool, img_unpack, img_maker (from rk2918_tools)
# Prerequisites: gh CLI authenticated, proxy at 192.168.1.25:10808

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TOOLS="$SCRIPT_DIR/tools"
FIRMWARE="$SCRIPT_DIR/firmware"
BOOT="$SCRIPT_DIR/boot"
KERNEL="$SCRIPT_DIR/kernel"
UBOOT="$SCRIPT_DIR/uboot"
BUILD_REPO="fanticat/firefly-rk3399-android-build"
PROXY="http://192.168.1.25:10808"

# Step 1: Download kernel from GitHub Actions
download_kernel() {
    echo "==> Finding latest successful kernel build..."
    RUN_ID=$(https_proxy=$PROXY gh run list \
        --repo $BUILD_REPO \
        --status completed \
        --limit 10 \
        --json databaseId,conclusion,name \
        | python3 -c "
import json,sys
runs = json.load(sys.stdin)
for r in runs:
    if r['conclusion'] == 'success' and 'kernel' in r.get('name','').lower():
        print(r['databaseId'])
        break
")

    if [ -z "$RUN_ID" ]; then
        echo "ERROR: No successful kernel build found"
        exit 1
    fi

    echo "==> Downloading kernel artifacts from run $RUN_ID..."
    mkdir -p "$KERNEL"
    https_proxy=$PROXY gh run download $RUN_ID \
        --repo $BUILD_REPO \
        --name firefly-rk3399-kernel-nt35596 \
        --dir /tmp/kernel-download

    cp /tmp/kernel-download/Image "$KERNEL/"
    cp /tmp/kernel-download/rk3399-firefly-nt35596.dtb "$KERNEL/"
    rm -rf /tmp/kernel-download

    echo "==> Kernel downloaded:"
    ls -lh "$KERNEL/Image" "$KERNEL/rk3399-firefly-nt35596.dtb"
}

# Step 1b: Download U-Boot from GitHub Actions
download_uboot() {
    echo "==> Finding latest successful U-Boot build..."
    RUN_ID=$(https_proxy=$PROXY gh run list \
        --repo $BUILD_REPO \
        --status completed \
        --limit 10 \
        --json databaseId,conclusion,name \
        | python3 -c "
import json,sys
runs = json.load(sys.stdin)
for r in runs:
    if r['conclusion'] == 'success' and 'u-boot' in r.get('name','').lower():
        print(r['databaseId'])
        break
")

    if [ -z "$RUN_ID" ]; then
        echo "ERROR: No successful U-Boot build found"
        exit 1
    fi

    echo "==> Downloading U-Boot artifacts from run $RUN_ID..."
    mkdir -p "$UBOOT"
    https_proxy=$PROXY gh run download $RUN_ID \
        --repo $BUILD_REPO \
        --name firefly-rk3399-uboot \
        --dir "$UBOOT"

    echo "==> U-Boot downloaded:"
    ls -lh "$UBOOT/"
}

# Step 2: Repack boot.img (new kernel + original ramdisk + extra cmdline for dynamic partitions)
make_boot() {
    echo "==> Repacking boot.img..."
    python3 "$TOOLS/mkbootimg-rk.py" \
        "$BOOT/vaaman-boot-original.img" \
        "$KERNEL/Image" \
        "$BOOT/firefly-boot-nt35596.img" \
        "" \
        "androidboot.super_partition=/dev/block/by-name/super"
}

# Step 3: Build complete RKFW update.img
# Flow: assemble-rkfw.py (preserves original header+loader, replaces RKAF payload)
# NOTE: img_maker from rk2918_tools breaks RK3399 IDB (hardcodes chip=0x50 for RK29xx)
make_firmware() {
    if [ ! -f "$BOOT/firefly-boot-nt35596.img" ]; then
        echo "ERROR: Custom boot.img not found. Run '$0 boot' first."
        exit 1
    fi

    python3 "$SCRIPT_DIR/assemble-rkfw.py"
}

# Trigger new GitHub Actions build
trigger_build() {
    local target="${2:-kernel}"
    if [ "$target" = "uboot" ]; then
        echo "==> Triggering U-Boot build..."
        https_proxy=$PROXY gh workflow run build-uboot.yml --repo $BUILD_REPO
    else
        echo "==> Triggering kernel build..."
        https_proxy=$PROXY gh workflow run build-kernel.yml --repo $BUILD_REPO
    fi
    echo "==> Monitor at: https://github.com/$BUILD_REPO/actions"
}

case "${1:-}" in
    download)
        download_kernel
        ;;
    download-uboot)
        download_uboot
        ;;
    boot)
        make_boot
        ;;
    firmware|img)
        make_firmware
        ;;
    build)
        trigger_build "$@"
        ;;
    all)
        download_kernel
        make_boot
        make_firmware
        ;;
    *)
        echo "Usage: $0 {download|download-uboot|boot|firmware|build|all}"
        echo ""
        echo "  download       - Download kernel from GitHub Actions"
        echo "  download-uboot - Download U-Boot from GitHub Actions"
        echo "  boot           - Repack boot.img with new kernel"
        echo "  firmware       - Build complete RKFW update.img"
        echo "  build [uboot]  - Trigger GitHub Actions build (kernel or uboot)"
        echo "  all            - Download + boot + firmware"
        ;;
esac
