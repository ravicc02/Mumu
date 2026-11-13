from __future__ import annotations

import argparse
import hashlib
import json
import sys
import zipfile


def main():
    if hasattr(sys.stdout, "reconfigure"): sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Read and verify a local Mumu diagnostic bundle")
    parser.add_argument("bundle")
    args = parser.parse_args()
    with zipfile.ZipFile(args.bundle) as archive:
        if sum(item.file_size for item in archive.infolist()) > 45 * 1024 * 1024: raise ValueError("Bundle exceeds the supported size")
        manifest = json.loads(archive.read("manifest.json"))
        if manifest.get("schema_version") != 1: raise ValueError("Unsupported diagnostic schema version")
        for item in manifest["files"]:
            value = archive.read(item["path"])
            if len(value) != item["bytes"] or hashlib.sha256(value).hexdigest() != item["sha256"]: raise ValueError("Checksum mismatch: " + item["path"])
        print(archive.read("REPORT.md").decode("utf-8"))
        print("\nVerified files:", len(manifest["files"]))


if __name__ == "__main__":
    try: main()
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as error:
        print("Invalid diagnostic bundle:", error, file=sys.stderr)
        raise SystemExit(2)
