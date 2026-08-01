"""D2 数据备份：把 .data 关键目录快照到备份位置，保留最近 N 份。

用法（apps/api 下）：
    python scripts/backup_data.py --target E:\\backups\\sag --keep 3 --dry-run

默认：备份到 <data_dir 上级>\\backups\\<时间戳>，保留最近 3 份。
跳过可重建的缓存（lancedb 向量索引可由审计脚本重建）。
"""

from __future__ import annotations

import argparse
import datetime
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# 需要备份的 .data 子目录/文件（相对 data_dir 的父目录）
INCLUDE: list[str] = ["sag.db", "sag.db-wal", "sag.db-shm", "engine", "uploads", "reports"]
# 跳过的大体积可重建内容
EXCLUDE_DIRS = {"lancedb", "__pycache__"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", type=str, default=None, help="备份根目录（默认 <data 父目录>/backups）")
    parser.add_argument("--keep", type=int, default=3)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    from sag_api.core.config import settings

    data_parent = Path(settings.data_dir).resolve().parent  # 例如 E:\sag\.data
    target_root = Path(args.target or data_parent / "backups").resolve()
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = target_root / f"sag-backup-{stamp}"

    print(f"data 根: {data_parent}")
    print(f"备份到: {dest}")
    if args.dry_run:
        for name in INCLUDE:
            src = data_parent / name
            print(f"  [dry] {name} -> {dest / name} ({'存在' if src.exists() else '缺失'})")
        return 0

    dest.mkdir(parents=True, exist_ok=True)
    copied = 0
    skipped = 0
    for name in INCLUDE:
        src = data_parent / name
        if not src.exists():
            skipped += 1
            continue
        dst = dest / name
        if src.is_dir():
            shutil.copytree(
                src, dst,
                ignore=shutil.ignore_patterns(*[f"{d}/*" for d in EXCLUDE_DIRS], *EXCLUDE_DIRS),
            )
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        copied += 1
    print(f"完成：复制 {copied} 项，跳过缺失 {skipped} 项")

    # 保留策略：删除超出 keep 的最旧备份
    backups = sorted([p for p in target_root.glob("sag-backup-*") if p.is_dir()])
    for old in backups[:-args.keep] if args.keep > 0 else []:
        print(f"清理旧备份: {old.name}")
        shutil.rmtree(old, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
