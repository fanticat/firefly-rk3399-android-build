#!/usr/bin/env python3
"""
Assemble RKFW firmware for RK3399 without using img_maker.

img_maker from rk2918_tools hardcodes chip=0x50 (RK29xx) and corrupts
the RKFW header for RK3399, causing "Prepare IDB failed" on flash.

This script preserves the original RKFW header + loader bytes exactly,
only replacing the RKAF update.img payload, then appending a correct MD5.
"""

import struct
import os
import sys
import hashlib
import subprocess

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.join(SCRIPT_DIR, "tools")
FIRMWARE = os.path.join(SCRIPT_DIR, "firmware")
BOOT = os.path.join(SCRIPT_DIR, "boot")
UBOOT = os.path.join(SCRIPT_DIR, "uboot")

BASE_IMG = os.path.join(FIRMWARE, "Vicharak_Vaaman_EMMC_android12_v0.1.0_09262023.img")
CUSTOM_BOOT = os.path.join(BOOT, "firefly-boot-nt35596.img")
CUSTOM_UBOOT = os.path.join(UBOOT, "uboot.img")
CUSTOM_TRUST = os.path.join(UBOOT, "trust.img")
CUSTOM_IDBLOADER = os.path.join(UBOOT, "idbloader.img")
OUTPUT = os.path.join(FIRMWARE, "firefly-rk3399-android12-nt35596.img")

WORK_DIR = os.path.join(FIRMWARE, "_work")


def run(cmd, **kwargs):
    print(f"  + {cmd}")
    subprocess.run(cmd, shell=True, check=True, **kwargs)


def main():
    if not os.path.exists(BASE_IMG):
        print(f"ERROR: Base firmware not found: {BASE_IMG}")
        sys.exit(1)
    if not os.path.exists(CUSTOM_BOOT):
        print(f"ERROR: Custom boot.img not found: {CUSTOM_BOOT}")
        sys.exit(1)

    # Read original RKFW header
    with open(BASE_IMG, "rb") as f:
        header = bytearray(f.read(0x66))

    magic = header[0:4]
    if magic != b"RKFW":
        print(f"ERROR: Invalid RKFW magic: {magic}")
        sys.exit(1)

    loader_offset = struct.unpack_from("<I", header, 0x19)[0]
    loader_length = struct.unpack_from("<I", header, 0x1D)[0]
    image_offset = struct.unpack_from("<I", header, 0x21)[0]
    image_length = struct.unpack_from("<I", header, 0x25)[0]
    chip = struct.unpack_from("<I", header, 0x15)[0]

    print(f"Original firmware:")
    print(f"  chip:      0x{chip:08x}")
    print(f"  loader:    offset=0x{loader_offset:x}, length={loader_length}")
    print(f"  image:     offset=0x{image_offset:x}, length={image_length}")

    # Read original loader
    with open(BASE_IMG, "rb") as f:
        f.seek(loader_offset)
        loader_data = f.read(loader_length)

    # Read original RKAF update.img
    with open(BASE_IMG, "rb") as f:
        f.seek(image_offset)
        rkaf_data = f.read(image_length)

    # Unpack RKAF
    print("\n==> Unpacking original RKAF update.img...")
    if os.path.exists(WORK_DIR):
        run(f"rm -rf {WORK_DIR}")
    os.makedirs(os.path.join(WORK_DIR, "update", "Image"), exist_ok=True)

    rkaf_path = os.path.join(WORK_DIR, "update.img")
    with open(rkaf_path, "wb") as f:
        f.write(rkaf_data)

    run(f"cd {WORK_DIR}/update && {TOOLS}/afptool -unpack {rkaf_path} .")

    # Replace boot.img
    print("\n==> Replacing boot.img...")
    boot_dst = os.path.join(WORK_DIR, "update", "Image", "boot.img")
    run(f"cp {CUSTOM_BOOT} {boot_dst}")

    # Replace uboot.img and trust.img if custom U-Boot is available
    use_custom_uboot = os.path.exists(CUSTOM_UBOOT) and os.path.exists(CUSTOM_TRUST)
    if use_custom_uboot:
        print("\n==> Replacing uboot.img and trust.img with custom U-Boot...")
        uboot_dst = os.path.join(WORK_DIR, "update", "Image", "uboot.img")
        trust_dst = os.path.join(WORK_DIR, "update", "Image", "trust.img")
        run(f"cp {CUSTOM_UBOOT} {uboot_dst}")
        run(f"cp {CUSTOM_TRUST} {trust_dst}")
        print("  Custom U-Boot applied!")
    else:
        print("\n  (No custom U-Boot found, keeping original)")

    # Replace misc.img with zeros to clear recovery/bootloader flag
    misc_dst = os.path.join(WORK_DIR, "update", "Image", "misc.img")
    with open(misc_dst, "wb") as f:
        f.write(b"\x00" * (128 * 1024))  # 128KB zeroed misc
    print("  Cleared misc.img (zeroed to force normal boot)")

    # Disable AVB verification by patching vbmeta.img flags
    vbmeta_dst = os.path.join(WORK_DIR, "update", "Image", "vbmeta.img")
    if os.path.exists(vbmeta_dst):
        with open(vbmeta_dst, "rb") as f:
            vbmeta = bytearray(f.read())
        if vbmeta[:4] == b"AVB0":
            # AVB VBMeta Image Header flags at offset 123 (big-endian uint32)
            # Bit 0: VERIFICATION_DISABLED, Bit 1: HASHTREE_DISABLED
            import struct as st
            flags_off = 123
            old_flags = st.unpack_from(">I", vbmeta, flags_off)[0]
            new_flags = old_flags | 3  # VERIFICATION_DISABLED | HASHTREE_DISABLED
            st.pack_into(">I", vbmeta, flags_off, new_flags)
            with open(vbmeta_dst, "wb") as f:
                f.write(vbmeta)
            print(f"  Patched vbmeta.img: flags 0x{old_flags:x} -> 0x{new_flags:x} (AVB disabled)")
        else:
            print("  (vbmeta.img not AVB0 format, skipping patch)")

    # Keep original dtbo.img - it contains overlay for bootargs_ext and reboot_mode
    # that Android init needs for proper boot configuration
    dtbo_dst = os.path.join(WORK_DIR, "update", "Image", "dtbo.img")
    if os.path.exists(dtbo_dst):
        print("  Keeping original dtbo.img (contains boot overlay)")

    # afptool -pack expects ./parameter in the CWD (the update directory)
    param_src = os.path.join(WORK_DIR, "update", "Image", "parameter.txt")
    param_dst = os.path.join(WORK_DIR, "update", "parameter")
    if os.path.exists(param_src):
        run(f"cp {param_src} {param_dst}")

    # Repack RKAF
    print("\n==> Repacking RKAF update.img...")
    update_new_path = os.path.join(WORK_DIR, "update_new.img")
    run(f"cd {WORK_DIR}/update && {TOOLS}/afptool -pack . {update_new_path}")

    update_new_size = os.path.getsize(update_new_path)
    print(f"  New update.img size: {update_new_size}")

    # Assemble final RKFW
    print("\n==> Assembling RKFW firmware...")

    # Update header: only change image_length
    new_image_offset = loader_offset + loader_length
    struct.pack_into("<I", header, 0x21, new_image_offset)
    struct.pack_into("<I", header, 0x25, update_new_size)

    # Build MD5 over header + loader + update.img
    md5 = hashlib.md5()
    md5.update(header)
    md5.update(loader_data)

    # Write output: header + loader + update.img + MD5 hex
    with open(OUTPUT, "wb") as out:
        out.write(header)
        out.write(loader_data)

        with open(update_new_path, "rb") as upd:
            while True:
                chunk = upd.read(1024 * 1024)
                if not chunk:
                    break
                out.write(chunk)
                md5.update(chunk)

    md5_hex = md5.hexdigest()

    # Append MD5 as 32-byte ASCII hex string
    with open(OUTPUT, "ab") as out:
        out.write(md5_hex.encode("ascii"))

    final_size = os.path.getsize(OUTPUT)
    print(f"\n==> Done!")
    print(f"  Output:  {OUTPUT}")
    print(f"  Size:    {final_size} bytes ({final_size / (1024*1024):.1f} MB)")
    print(f"  MD5:     {md5_hex}")

    # Verify: read back and check structure
    print("\n==> Verifying output...")
    with open(OUTPUT, "rb") as f:
        v_header = f.read(0x66)
        v_magic = v_header[0:4]
        v_chip = struct.unpack_from("<I", v_header, 0x15)[0]
        v_loader_off = struct.unpack_from("<I", v_header, 0x19)[0]
        v_loader_len = struct.unpack_from("<I", v_header, 0x1D)[0]
        v_image_off = struct.unpack_from("<I", v_header, 0x21)[0]
        v_image_len = struct.unpack_from("<I", v_header, 0x25)[0]
        f.seek(-32, 2)
        v_md5 = f.read(32).decode("ascii")

    print(f"  Magic:   {v_magic}")
    print(f"  Chip:    0x{v_chip:08x}")
    print(f"  Loader:  offset=0x{v_loader_off:x}, length={v_loader_len}")
    print(f"  Image:   offset=0x{v_image_off:x}, length={v_image_len}")
    print(f"  MD5:     {v_md5}")

    if v_magic != b"RKFW":
        print("  ERROR: Invalid magic!")
        sys.exit(1)
    if v_chip != 0x33333043:
        print("  ERROR: Wrong chip type!")
        sys.exit(1)
    if v_md5 != md5_hex:
        print("  ERROR: MD5 mismatch!")
        sys.exit(1)

    print("  All checks passed!")

    # Cleanup
    run(f"rm -rf {WORK_DIR}")

    print(f"\n  Flash with: sudo rkdeveloptool wl 0 {OUTPUT}")


if __name__ == "__main__":
    main()
