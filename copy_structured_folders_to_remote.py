#!/usr/bin/env python3
"""
Append-only recursive copy local -> remote using rsync (Option A), with explicit tagging.

Implements:
- input_folder is local
- output_folder is remote (user_host like "foo@bar")
- output_folder MUST already exist on remote (error + exit if not)
- Copy every top-level folder under input_folder into output_folder, preserving structure:
    input_folder/name/...  ->  output_folder/name/...
- NEVER overwrite existing remote files:
    rsync --ignore-existing
  (even if checksums differ, existing files are left untouched)
- Use rsync checksum logic + safe resume (no manual md5 pass):
    --checksum, --append-verify, --partial
- Show per-file progress:
    --progress
- Explicit tagging of rsync itemize output:
    [COPIED] for transferred files
    [SKIPPED] for files that were considered but not transferred (often because they exist)
    [DIR] for directories
- Dry-run mode:
    --dry-run (no remote changes; still shows what WOULD copy/skip)

Requirements on both machines:
- ssh
- rsync

Example syntax:

python3 copy_structured_folders_to_remote.py --user-host "user@remotehost" --input-folder "/foo/bar/root/" --output-folder "/fu/bur/root/"

"""

from __future__ import annotations

import argparse
import shlex
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Tuple


def eprint(*args, **kwargs):
    print(*args, file=sys.stderr, **kwargs)


def run_ssh(user_host: str, remote_cmd: str) -> subprocess.CompletedProcess:
    cmd = ["ssh", "-o", "BatchMode=yes", user_host, remote_cmd]
    return subprocess.run(cmd, text=True, capture_output=True)


def parse_rsync_outformat_line(line: str) -> Optional[Tuple[str, str]]:
    """
    We set --out-format='RSYNC:%i:%n' so we can parse reliably.
    Returns (itemize_code, name) or None.

    Example:
      RSYNC:>f+++++++++:name/name - Treason 0/file.bin
      RSYNC:cd+++++++++:name/name - Treason 0/
      RSYNC:.f........:name/name - Treason 0/file.bin
    """
    if not line.startswith("RSYNC:"):
        return None
    try:
        _, rest = line.split("RSYNC:", 1)
        item, name = rest.split(":", 1)
        return item.strip(), name.strip()
    except ValueError:
        return None


def classify_item(itemize_code: str) -> str:
    """
    Classify rsync itemize changes codes into human-friendly tags.
    We keep this intentionally simple and conservative.

    Common prefixes:
    - >f...  transferred file (sent to receiver)
    - .f...  file but not transferred (unchanged or skipped due to --ignore-existing)
    - cd...  directory (created/updated metadata)
    - .d...  directory not changed

    We'll tag:
    - [COPIED] for >f...
    - [SKIPPED] for .f... (not transferred)
    - [DIR] for directories (cd..., .d...)
    - [OTHER] fallback
    """
    if itemize_code.startswith(">f"):
        return "COPIED"
    if itemize_code.startswith(".f") or itemize_code.startswith("..f") or itemize_code.startswith("hf"):
        return "SKIPPED"
    if itemize_code.startswith("cd") or itemize_code.startswith(".d") or itemize_code.startswith("cD") or itemize_code.startswith(">d"):
        return "DIR"
    return "OTHER"


def run_rsync_directory(
    src_dir: Path,
    user_host: str,
    remote_output_folder: str,
    dry_run: bool,
    show_other: bool,
) -> Tuple[int, int, int]:
    """
    Rsync a top-level directory into remote_output_folder.
    Returns (copied_files, skipped_files, dir_events) based on parsed rsync lines.
    """
    src = str(src_dir)  # no trailing slash => includes top-level folder name
    dest = f"{user_host}:{remote_output_folder.rstrip('/')}/"

    rsync_cmd: List[str] = [
        "rsync",
        "-a",
        "--ignore-existing",          # append-only: do not overwrite existing files
        "--checksum",                 # compare by content when deciding transfers
        "--partial",                  # keep partial transfers
        "--append-verify",            # safe resume with verification
        "--progress",                 # per-file progress
        "--itemize-changes",
        "--out-format=RSYNC:%i:%n",   # parseable
    ]

    if dry_run:
        rsync_cmd.append("--dry-run")

    rsync_cmd.extend([src, dest])

    print("\n=== RSYNC DIR ===")
    print(" ".join(shlex.quote(x) for x in rsync_cmd))
    print("=================\n")

    copied_files = 0
    skipped_files = 0
    dir_events = 0

    proc = subprocess.Popen(
        rsync_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        universal_newlines=True,
    )

    assert proc.stdout is not None
    for raw_line in proc.stdout:
        line = raw_line.rstrip("\n")

        parsed = parse_rsync_outformat_line(line)
        if parsed:
            itemize_code, name = parsed
            tag = classify_item(itemize_code)

            if tag == "COPIED":
                copied_files += 1
                prefix = "[WOULD COPY]" if dry_run else "[COPIED]"
                print(f"{prefix} {name}")
            elif tag == "SKIPPED":
                skipped_files += 1
                # With --ignore-existing, most skipped files are because they already exist.
                # We label it explicitly as "exists/unchanged".
                print(f"[SKIPPED - EXISTS/UNCHANGED] {name}")
            elif tag == "DIR":
                dir_events += 1
                # Directories may be created on remote (inside output_folder) as needed.
                # That's fine; we are not creating output_folder itself.
                print(f"[DIR] {name}")
            else:
                if show_other:
                    print(f"[OTHER {itemize_code}] {name}")

            continue

        # Non-out-format lines: rsync headers, progress bars, summaries, etc.
        # Keep them as-is so you still see per-file progress.
        print(line)

    rc = proc.wait()
    if rc != 0:
        raise RuntimeError(f"rsync failed for {src_dir} with exit code {rc}")

    return copied_files, skipped_files, dir_events


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Append-only local->remote sync using rsync (--ignore-existing) with explicit tagging and optional dry-run."
    )
    ap.add_argument("--user-host", required=True, help='Remote in the form "user@host" (e.g., foo@bar)')
    ap.add_argument("--input-folder", required=True, help="Local folder to copy from")
    ap.add_argument("--output-folder", required=True, help="Remote folder to copy into (must already exist)")
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview what would be copied (no changes made on remote).",
    )
    ap.add_argument(
        "--show-other",
        action="store_true",
        help="Also show [OTHER] tagged rsync itemize lines (usually not needed).",
    )
    args = ap.parse_args()

    user_host = args.user_host
    input_folder = Path(args.input_folder).expanduser().resolve()
    output_folder = args.output_folder
    dry_run = args.dry_run
    show_other = args.show_other

    if not input_folder.is_dir():
        eprint(f"ERROR: input_folder does not exist or is not a directory: {input_folder}")
        return 2

    # Ensure remote output_folder exists; do NOT create it.
    remote_check_cmd = f"test -d {shlex.quote(output_folder)}"
    cp = run_ssh(user_host, remote_check_cmd)
    if cp.returncode != 0:
        eprint(f"ERROR: remote output_folder does not exist (won't create it): {user_host}:{output_folder}")
        if cp.stderr.strip():
            eprint(cp.stderr.strip())
        return 3

    top_dirs = [p for p in sorted(input_folder.iterdir()) if p.is_dir()]
    if not top_dirs:
        print(f"No directories found under input_folder: {input_folder}")
        return 0

    print(f"Local input_folder : {input_folder}")
    print(f"Remote output_folder: {user_host}:{output_folder}")
    print(f"Mode: {'DRY-RUN (no changes)' if dry_run else 'LIVE (will copy)'}")
    print("Append-only guarantee: existing remote files are NOT overwritten (--ignore-existing).")
    print(f"Top-level folders to process: {len(top_dirs)}")

    total_copied = 0
    total_skipped = 0
    total_dirs = 0

    for d in top_dirs:
        print(f"\n>>> Processing top-level folder: {d.name}")
        copied, skipped, dirs = run_rsync_directory(
            src_dir=d,
            user_host=user_host,
            remote_output_folder=output_folder,
            dry_run=dry_run,
            show_other=show_other,
        )
        total_copied += copied
        total_skipped += skipped
        total_dirs += dirs

        if dry_run:
            print(f"[DRY-RUN SUMMARY] {d.name}: would copy={copied}, skipped(exists/unchanged)={skipped}, dir-events={dirs}")
        else:
            print(f"[SUMMARY] {d.name}: copied={copied}, skipped(exists/unchanged)={skipped}, dir-events={dirs}")

    print("\n=== OVERALL SUMMARY ===")
    if dry_run:
        print(f"Would copy files: {total_copied}")
    else:
        print(f"Copied files: {total_copied}")
    print(f"Skipped files (exists/unchanged): {total_skipped}")
    print(f"Directory events: {total_dirs}")
    print("=======================\n")

    print("JOB DONE ✅")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        eprint("\nInterrupted.")
        raise SystemExit(130)
    except Exception as ex:
        eprint(f"\nERROR: {ex}")
        raise SystemExit(1)
