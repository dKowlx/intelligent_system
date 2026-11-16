"""Snapshot explicit data paths; never package environment or application secrets.

Run with writes to projects/uploads paused for a coherent multi-file snapshot.
SQLite databases use the backup API (including committed WAL data). The budget
ledger is copied under its normal exclusive lock; in-flight reservations remain
reserved, so restoration cannot reset their spend protection.
"""
from contextlib import closing, contextmanager
from datetime import datetime, timezone
import argparse
import fcntl
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import tarfile
import tempfile


@contextmanager
def ledger_lock(path):
    lock_path = path.with_name(path.name + '.lock')
    with lock_path.open('a+b') as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def snapshot_database(source, destination):
    source = Path(source).resolve(strict=True)
    with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)) as current:
        with closing(sqlite3.connect(destination)) as backup:
            current.backup(backup)
            backup.execute('PRAGMA journal_mode=DELETE')
            if backup.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                raise ValueError('database_integrity_check_failed')


def copy_tree(source, destination):
    source = Path(source).resolve(strict=True)
    if not source.is_dir():
        raise ValueError('backup_directory_required')
    # Do not follow links outside the explicitly selected data tree.
    for child in source.rglob('*'):
        if child.is_symlink():
            raise ValueError('backup_symlink_not_allowed')
    shutil.copytree(source, destination)


def create_backup(db, uploads, ledger, *, knowledge_db=None, knowledge_root=None, output_dir=None):
    if bool(knowledge_db) != bool(knowledge_root):
        raise ValueError('knowledge_db_and_root_required_together')
    output = None
    if output_dir:
        directory = Path(output_dir).resolve(strict=True)
        if not directory.is_dir():
            raise ValueError('output_directory_required')
        output = directory / ('navigator-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.tar.gz')
    with tempfile.TemporaryDirectory(prefix='navigator-backup-') as temporary:
        stage = Path(temporary)
        os.chmod(stage, 0o700)
        with ledger_lock(Path(ledger)):
            # Preserve ledger bytes exactly; unknown/pending costs stay reserved.
            raw = Path(ledger).read_bytes()
            if not isinstance(json.loads(raw), dict):
                raise ValueError('invalid_ledger')
            (stage / 'live-budget.json').write_bytes(raw)
            (stage / 'live-budget.json.lock').touch(mode=0o600)
        snapshot_database(db, stage / 'study.db')
        copy_tree(uploads, stage / 'uploads')
        if knowledge_db:
            # Index is snapshotted separately; exclude SQLite sidecars from files.
            root = Path(knowledge_root).resolve(strict=True)
            index = Path(knowledge_db).resolve(strict=True)
            snapshot_database(index, stage / 'knowledge.db')
            copy_tree(root, stage / 'knowledge')
            relative = index.relative_to(root) if index.is_relative_to(root) else None
            for suffix in ('', '-wal', '-shm', '-journal'):
                if relative is None:
                    continue
                copied = stage / 'knowledge' / (str(relative) + suffix)
                if copied.is_file():
                    copied.unlink()
        (stage / 'manifest.json').write_text(json.dumps({
            'version': 1, 'created_at': datetime.now(timezone.utc).isoformat(),
            'database': 'study.db', 'uploads': 'uploads', 'ledger': 'live-budget.json',
            'knowledge_database': 'knowledge.db' if knowledge_db else None,
            'knowledge_files': 'knowledge' if knowledge_db else None,
            'requires_paused_project_writes': True,
        }, indent=2))
        stream = None
        try:
            if output:
                descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                stream = os.fdopen(descriptor, 'wb')
            with tarfile.open(fileobj=stream or sys.stdout.buffer, mode='w|gz') as archive:
                for child in sorted(stage.iterdir()):
                    archive.add(child, arcname=child.name)
        except BaseException:
            if output:
                output.unlink(missing_ok=True)
            raise
        finally:
            if stream:
                stream.close()
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', required=True, type=Path)
    parser.add_argument('--uploads', required=True, type=Path)
    parser.add_argument('--ledger', required=True, type=Path)
    parser.add_argument('--knowledge-db', type=Path)
    parser.add_argument('--knowledge-root', type=Path)
    parser.add_argument('--output-dir', type=Path)
    args = parser.parse_args()
    result = create_backup(args.db, args.uploads, args.ledger,
        knowledge_db=args.knowledge_db, knowledge_root=args.knowledge_root, output_dir=args.output_dir)
    if result:
        print(result)


if __name__ == '__main__':
    main()
