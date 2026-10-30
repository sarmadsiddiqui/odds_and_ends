#!/usr/bin/env python3
"""
copy_verify_md5.py

Copy files from a local folder to a remote folder over SSH.
- Skips files that already exist (same filename) on remote.
- For newly copied files: verifies md5sum matches local.
- If mismatch: deletes remote file, re-copies, verifies again.
- DOES NOT create the remote directory. If it doesn't exist, exits with an error.
- Shows scp progress for each file as it copies (Option A).


example syntax:

python3 copying_individual_files_to_remote.py --remote "user@remotehost" --input-folder "/foo/bar/root" --output-folder "/foo/bar/root/"

"""

from __future__ import annotations

import argparse
import hashlib
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Optional, Tuple

MD5_CHUNK_SIZE = 500 * 1024 * 1024  # 500 MB


def run_cmd(cmd: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
    """Run a command and return the completed process (captures stdout/stderr)."""
    return subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=check,
    )


def local_md5(path: Path, chunk_size: int = MD5_CHUNK_SIZE) -> str:
    """Compute MD5 of a local file using large chunks (default: 500 MB)."""
    h = hashlib.md5()
    with path.open("rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def ssh_cmd(remote: str, remote_command: str) -> subprocess.CompletedProcess:
    """
    Execute a command on remote via ssh.
    remote_command is passed to remote shell, so quote carefully.
    """
    return run_cmd(["ssh", remote, remote_command], check=False)


def remote_file_exists(remote: str, remote_path: str) -> bool:
    cmd = f"test -e {shlex.quote(remote_path)}"
    p = ssh_cmd(remote, cmd)
    return p.returncode == 0


def remote_dir_exists(remote: str, remote_dir: str) -> bool:
    cmd = f"test -d {shlex.quote(remote_dir)}"
    p = ssh_cmd(remote, cmd)
    return p.returncode == 0


def remote_md5(remote: str, remote_path: str) -> Optional[str]:
    """
    Return remote md5sum hex digest for remote_path, or None if it fails.
    Uses 'md5sum' which is standard on Linux.
    """
    cmd = f"md5sum {shlex.quote(remote_path)}"
    p = ssh_cmd(remote, cmd)
    if p.returncode != 0:
        return None
    parts = p.stdout.strip().split()
    if not parts:
        return None
    return parts[0].lower()


def remote_delete(remote: str, remote_path: str) -> bool:
    cmd = f"rm -f {shlex.quote(remote_path)}"
    p = ssh_cmd(remote, cmd)
    return p.returncode == 0


def scp_copy_with_progress(local_path: Path, remote: str, remote_dir: str) -> Tuple[bool, str]:
    """
    Copy local_path into remote_dir via scp, showing scp's native progress output.
    Assumes remote_dir already exists.
    """
    dest = f"{remote}:{remote_dir.rstrip('/')}/"
    try:
        subprocess.run(
            ["scp", "-p", str(local_path), dest],
            check=True,
        )
        return True, "Copied"
    except subprocess.CalledProcessError:
        return False, "SCP failed"


def verify_and_copy_one(
    remote: str,
    local_file: Path,
    remote_dir: str,
    retries: int = 1,
) -> None:
    remote_path = f"{remote_dir.rstrip('/')}/{local_file.name}"

    # 1) Skip if exists
    if remote_file_exists(remote, remote_path):
        print(f"[SKIP] {local_file.name} exists on remote at {remote_path}")
        return

    # 2) Copy + verify, retry if mismatch
    local_hash = local_md5(local_file)
    attempt = 0
    max_attempts = retries + 1

    while attempt < max_attempts:
        attempt += 1

        ok, msg = scp_copy_with_progress(local_file, remote, remote_dir)
        if not ok:
            print(f"[ERROR] {local_file.name}: {msg}")
            return

        r_hash = remote_md5(remote, remote_path)
        if r_hash is None:
            print(f"[ERROR] {local_file.name}: could not compute remote md5sum for {remote_path}")
            if attempt < max_attempts:
                print(f"       deleting remote file and retrying (attempt {attempt+1}/{max_attempts})")
                remote_delete(remote, remote_path)
                continue
            return

        if r_hash == local_hash:
            print(f"[OK]   {local_file.name}: md5 matches ({local_hash})")
            return

        print(f"[BAD]  {local_file.name}: md5 mismatch (local={local_hash}, remote={r_hash})")

        if attempt >= max_attempts:
            print(f"       giving up after {attempt} attempts")
            return

        deleted = remote_delete(remote, remote_path)
        if not deleted:
            print(f"[ERROR] {local_file.name}: failed to delete remote file {remote_path} before retry")
            return

        print(f"       deleted remote copy, re-copying (attempt {attempt+1}/{max_attempts})")


def iter_files(input_folder: Path) -> list[Path]:
    """Non-recursive: only files directly inside input_folder."""
    return sorted([p for p in input_folder.iterdir() if p.is_file()])


def main() -> int:
    parser = argparse.ArgumentParser(description="Copy files to remote folder with md5 verification.")
    parser.add_argument("--remote", required=True, help='Remote target like "user@host"')
    parser.add_argument("--input-folder", required=True, help="Local folder to copy from")
    parser.add_argument("--output-folder", required=True, help="Remote folder to copy into")
    parser.add_argument("--retries", type=int, default=1, help="Number of re-copy retries on md5 mismatch (default: 1)")
    args = parser.parse_args()

    remote = args.remote
    input_folder = Path(args.input_folder).expanduser().resolve()
    output_folder = args.output_folder
    retries = args.retries

    if not input_folder.exists() or not input_folder.is_dir():
        print(f"[FATAL] input_folder is not a directory: {input_folder}", file=sys.stderr)
        return 2

    # Fail fast if remote output directory does not exist
    if not remote_dir_exists(remote, output_folder):
        print(f"[FATAL] Remote directory does not exist: {remote}:{output_folder}", file=sys.stderr)
        return 3

    files = iter_files(input_folder)
    if not files:
        print(f"[INFO] No files found in {input_folder}")
        print("Job done.")
        return 0

    print(f"[INFO] Remote: {remote}")
    print(f"[INFO] From:   {input_folder}")
    print(f"[INFO] To:     {output_folder}")
    print(f"[INFO] Files:  {len(files)}")
    print("")

    for f in files:
        verify_and_copy_one(remote, f, output_folder, retries=retries)

    print("\nJob done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

