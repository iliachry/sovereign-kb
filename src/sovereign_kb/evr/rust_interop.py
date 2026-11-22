"""Zero-dependency Rust wire verifier (`rustc -D warnings`) for `.evr` v1.0."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

RUST_EVR_KERNEL_SRC = r"""
//! Native Rust Reference Engine for the `.evr` ("Everything" v1.0) File Format.
//! Enforces checked arithmetic, 64-byte SIMD/mmap alignment, Superblock/Footer CRC32,
//! version/codec validation, and stored-payload CRC32 verification across RAW and ZLIB chunks.

use std::env;
use std::fs;

const EVR_MAGIC_START: [u8; 4] = *b"EVR\x01";
const EVR_MAGIC_RESERVED: [u8; 4] = *b"EVR!";
const EVR_MAGIC_END: [u8; 4] = *b"1RVE";
const EVR_ALIGNMENT: u64 = 64;

pub fn crc32_ieee(data: &[u8]) -> u32 {
    let mut crc: u32 = 0xFFFF_FFFF;
    for &byte in data {
        crc ^= byte as u32;
        for _ in 0..8 {
            if (crc & 1) != 0 {
                crc = (crc >> 1) ^ 0xEDB8_8320;
            } else {
                crc >>= 1;
            }
        }
    }
    !crc
}

fn read_u16_le(buf: &[u8], off: usize) -> Option<u16> {
    let s = buf.get(off..off.checked_add(2)?)?;
    Some(u16::from_le_bytes([s[0], s[1]]))
}

fn read_u32_le(buf: &[u8], off: usize) -> Option<u32> {
    let s = buf.get(off..off.checked_add(4)?)?;
    Some(u32::from_le_bytes([s[0], s[1], s[2], s[3]]))
}

fn read_u64_le(buf: &[u8], off: usize) -> Option<u64> {
    let s = buf.get(off..off.checked_add(8)?)?;
    Some(u64::from_le_bytes([s[0], s[1], s[2], s[3], s[4], s[5], s[6], s[7]]))
}

#[derive(Debug, Clone)]
pub struct EvrRustSummary {
    pub version_major: u16,
    pub version_minor: u16,
    pub flags: u32,
    pub alignment: u32,
    pub superblock_offset: u64,
    pub toc_offset: u64,
    pub manifest_offset: u64,
    pub manifest_len: u64,
    pub chunk_count: u64,
    pub all_aligned_64: bool,
}

pub fn inspect_evr_bytes(buf: &[u8]) -> Result<EvrRustSummary, String> {
    let file_len = buf.len() as u64;
    if file_len < 128 || file_len % EVR_ALIGNMENT != 0 {
        return Err(format!("invalid file length {}", file_len));
    }
    let f_off = buf.len() - 64;
    let footer = buf.get(f_off..).ok_or_else(|| "missing footer".to_string())?;
    if footer.get(0..4) != Some(&EVR_MAGIC_START) || footer.get(60..64) != Some(&EVR_MAGIC_END) {
        return Err("invalid footer magic".to_string());
    }
    let expected_fcrc = crc32_ieee(&footer[0..56]);
    let actual_fcrc = read_u32_le(footer, 56).ok_or("footer crc read failed")?;
    if expected_fcrc != actual_fcrc {
        return Err(format!("footer crc32 mismatch: {:x} != {:x}", expected_fcrc, actual_fcrc));
    }
    let footer_flags = read_u32_le(footer, 4).ok_or("footer flags read failed")?;
    let sb_offset = read_u64_le(footer, 8).ok_or("sb_offset read failed")?;
    let toc_offset = read_u64_le(footer, 16).ok_or("toc_offset read failed")?;
    let manifest_offset = read_u64_le(footer, 24).ok_or("manifest_offset read failed")?;
    let manifest_len = read_u64_le(footer, 32).ok_or("manifest_len read failed")?;

    if sb_offset % EVR_ALIGNMENT != 0
        || toc_offset % EVR_ALIGNMENT != 0
        || manifest_offset % EVR_ALIGNMENT != 0
    {
        return Err("superblock/toc/manifest offsets violate 64B alignment".to_string());
    }
    let sb_end = sb_offset.checked_add(64).ok_or("sb_offset overflow")?;
    let manifest_end = manifest_offset.checked_add(manifest_len).ok_or("manifest_len overflow")?;
    if sb_end > file_len - 64 || toc_offset < sb_end || manifest_offset < toc_offset || manifest_end > file_len - 64 {
        return Err("section offsets out of file bounds".to_string());
    }

    let sb_usize = usize::try_from(sb_offset).map_err(|_| "sb_offset usize overflow")?;
    let sb = buf.get(sb_usize..sb_usize + 64).ok_or("superblock slice out of bounds")?;
    if sb.get(0..4) != Some(&EVR_MAGIC_START) || sb.get(60..64) != Some(&EVR_MAGIC_RESERVED) {
        return Err("invalid superblock magic".to_string());
    }
    let expected_sbcrc = crc32_ieee(&sb[0..56]);
    let actual_sbcrc = read_u32_le(sb, 56).ok_or("sb crc read failed")?;
    if expected_sbcrc != actual_sbcrc {
        return Err("superblock crc32 mismatch".to_string());
    }
    let ver_major = read_u16_le(sb, 4).ok_or("ver_major read failed")?;
    let ver_minor = read_u16_le(sb, 6).ok_or("ver_minor read failed")?;
    let flags = read_u32_le(sb, 8).ok_or("flags read failed")?;
    let alignment = read_u32_le(sb, 12).ok_or("alignment read failed")?;
    let chunk_count = read_u64_le(sb, 24).ok_or("chunk_count read failed")?;
    let sb_toc_offset = read_u64_le(sb, 32).ok_or("sb_toc_offset read failed")?;

    if ver_major != 1 || ver_minor != 0 {
        return Err(format!("unsupported version {}.{}", ver_major, ver_minor));
    }
    if alignment != 64 {
        return Err(format!("unsupported alignment {}", alignment));
    }
    if flags != footer_flags || sb_toc_offset != toc_offset {
        return Err("superblock/footer mismatch".to_string());
    }
    let toc_bytes = chunk_count.checked_mul(128).ok_or("chunk_count overflow")?;
    if toc_offset.checked_add(toc_bytes) != Some(manifest_offset) {
        return Err("binary TOC size does not match manifest_offset".to_string());
    }

    for i in 0..chunk_count {
        let e_off_u64 = toc_offset.checked_add(i * 128).ok_or("entry offset overflow")?;
        let e_off = usize::try_from(e_off_u64).map_err(|_| "entry offset usize overflow")?;
        let entry = buf.get(e_off..e_off + 128).ok_or("entry slice out of bounds")?;
        let kind = read_u32_le(entry, 0).ok_or("kind read failed")?;
        let codec = read_u32_le(entry, 4).ok_or("codec read failed")?;
        let d_off = read_u64_le(entry, 8).ok_or("d_off read failed")?;
        let s_len = read_u64_le(entry, 16).ok_or("s_len read failed")?;
        let r_len = read_u64_le(entry, 24).ok_or("r_len read failed")?;
        let packed_flags = read_u64_le(entry, 32).ok_or("flags read failed")?;
        let expected_raw_crc = read_u32_le(entry, 72).ok_or("crc read failed")?;
        let expected_stored_crc = ((packed_flags >> 32) & 0xFFFF_FFFF) as u32;

        if kind == 0 || kind > 12 {
            return Err(format!("chunk {} invalid kind {}", i, kind));
        }
        if codec > 1 {
            return Err(format!("chunk {} unsupported codec {}", i, codec));
        }
        if d_off % EVR_ALIGNMENT != 0 {
            return Err(format!("chunk {} data_offset {} not 64B aligned", i, d_off));
        }
        let d_end = d_off.checked_add(s_len).ok_or("chunk d_off + s_len overflow")?;
        if d_off < sb_end || d_end > toc_offset {
            return Err(format!("chunk {} range out of payload section bounds", i));
        }
        let d_start_usize = usize::try_from(d_off).map_err(|_| "d_off usize")?;
        let d_end_usize = usize::try_from(d_end).map_err(|_| "d_end usize")?;
        let slice = buf.get(d_start_usize..d_end_usize).ok_or("chunk slice OOB")?;
        let actual_stored_crc = crc32_ieee(slice);
        if actual_stored_crc != expected_stored_crc {
            return Err(format!("chunk {} stored payload crc32 mismatch", i));
        }
        if codec == 0 {
            if s_len != r_len {
                return Err(format!("chunk {} RAW s_len != r_len", i));
            }
            if actual_stored_crc != expected_raw_crc {
                return Err(format!("chunk {} RAW crc32 mismatch", i));
            }
        }
    }

    Ok(EvrRustSummary {
        version_major: ver_major,
        version_minor: ver_minor,
        flags,
        alignment,
        superblock_offset: sb_offset,
        toc_offset,
        manifest_offset,
        manifest_len,
        chunk_count,
        all_aligned_64: true,
    })
}

fn main() {
    let args: Vec<String> = env::args().collect();
    if args.len() < 2 {
        eprintln!("Usage: evr-rs <file.evr>");
        std::process::exit(1);
    }
    let data = match fs::read(&args[1]) {
        Ok(d) => d,
        Err(e) => {
            eprintln!("{{\"valid\":false,\"error\":\"{}\"}}", e);
            std::process::exit(2);
        }
    };
    match inspect_evr_bytes(&data) {
        Ok(s) => {
            println!(
                "{{\"valid\":true,\"version\":\"{}.{}\",\"alignment\":{},\"superblock_offset\":{},\"toc_offset\":{},\"chunk_count\":{},\"all_aligned_64\":{}}}",
                s.version_major,
                s.version_minor,
                s.alignment,
                s.superblock_offset,
                s.toc_offset,
                s.chunk_count,
                s.all_aligned_64
            );
        }
        Err(e) => {
            eprintln!("{{\"valid\":false,\"error\":\"{}\"}}", e);
            std::process::exit(2);
        }
    }
}
"""


def verify_rust_evr_interop(evr_file: str | Path) -> dict[str, Any]:
    """Compile the zero-dependency Rust `.evr` kernel (`rustc -D warnings`) and verify `evr_file`."""
    rustc = shutil.which("rustc") or str(Path.home() / ".cargo" / "bin" / "rustc")
    if not Path(rustc).is_file():
        return {"rust_available": False, "valid": False, "skipped": True}
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        rs_path = tmp / "evr_kernel.rs"
        bin_path = tmp / "evr-rs"
        rs_path.write_text(RUST_EVR_KERNEL_SRC, encoding="utf-8")
        comp_env = dict(os.environ)
        comp = subprocess.run(
            [rustc, "--edition", "2021", "-O", "-D", "warnings", str(rs_path), "-o", str(bin_path)],
            capture_output=True,
            text=True,
            check=False,
            env=comp_env,
        )
        if comp.returncode != 0 and "tapi error" in (comp.stderr or ""):
            sdk_root_dir = Path("/Library/Developer/CommandLineTools/SDKs")
            for cand_sdk in sorted(sdk_root_dir.glob("MacOSX26*.sdk"), reverse=True):
                if cand_sdk.is_dir():
                    comp_env["SDKROOT"] = str(cand_sdk)
                    comp = subprocess.run(
                        [rustc, "--edition", "2021", "-O", "-D", "warnings", str(rs_path), "-o", str(bin_path)],
                        capture_output=True,
                        text=True,
                        check=False,
                        env=comp_env,
                    )
                    if comp.returncode == 0:
                        break
        if comp.returncode != 0:
            return {"rust_available": True, "valid": False, "compile_error": comp.stderr}
        run = subprocess.run(
            [str(bin_path), str(evr_file)],
            capture_output=True,
            text=True,
            check=False,
        )
        if run.returncode != 0:
            return {"rust_available": True, "valid": False, "returncode": run.returncode, "runtime_error": run.stderr}
        parsed = json.loads(run.stdout.strip())
        parsed["rust_available"] = True
        return parsed
