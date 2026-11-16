"""Local archive restoration, WAL snapshot and secret exclusion."""
import importlib.util
import json
from pathlib import Path
import sqlite3
import tarfile

import pytest

spec = importlib.util.spec_from_file_location('backup_helper', Path(__file__).parents[2] / 'deploy/backup.py')
backup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backup)


def test_backup_restores_committed_wal_upload_and_exact_reserved_ledger(tmp_path):
    database = tmp_path / 'main.db'
    current = sqlite3.connect(database)
    current.execute('PRAGMA journal_mode=WAL')
    current.execute('CREATE TABLE progress(value TEXT)')
    current.execute("INSERT INTO progress VALUES ('saved')")
    current.commit()
    uploads = tmp_path / 'uploads'
    uploads.mkdir()
    (uploads / 'document.pdf').write_bytes(b'pdf fixture')
    ledger = tmp_path / 'budget.json'
    raw = b'{"attempts":[{"status":"pending","reserved_rub":"1.23"}]}'
    ledger.write_bytes(raw)
    (tmp_path / '.env').write_text('PRIVATE_SECRET=never-archive')
    archive = backup.create_backup(database, uploads, ledger, output_dir=tmp_path)
    current.close()
    assert archive.stat().st_mode & 0o777 == 0o600
    restored = tmp_path / 'restored'
    restored.mkdir()
    with tarfile.open(archive) as package:
        package.extractall(restored, filter='data')
        assert all('.env' not in name for name in package.getnames())
    with sqlite3.connect(restored / 'study.db') as db:
        assert db.execute('SELECT value FROM progress').fetchone() == ('saved',)
    assert (restored / 'live-budget.json').read_bytes() == raw
    assert (restored / 'uploads/document.pdf').read_bytes() == b'pdf fixture'
    assert json.loads((restored / 'manifest.json').read_text())['requires_paused_project_writes']


def test_backup_rejects_upload_symlink(tmp_path):
    uploads = tmp_path / 'uploads'
    uploads.mkdir()
    (uploads / 'secret-link').symlink_to(tmp_path / '.env')
    with pytest.raises(ValueError, match='symlink'):
        backup.copy_tree(uploads, tmp_path / 'copy')


@pytest.mark.parametrize("separate_index", [False, True])
def test_optional_knowledge_index_and_corpus_restore_separately(tmp_path, separate_index):
    database = tmp_path / 'main.db'
    with sqlite3.connect(database) as db:
        db.execute('CREATE TABLE app(id INTEGER)')
    uploads = tmp_path / 'uploads'
    uploads.mkdir()
    ledger = tmp_path / 'budget.json'
    ledger.write_text('{"attempts":[]}')
    corpus = tmp_path / 'corpus'
    corpus.mkdir()
    (corpus / 'paper.pdf').write_bytes(b'research fixture')
    index = (tmp_path if separate_index else corpus) / 'index.sqlite3'
    with sqlite3.connect(index) as db:
        db.execute('CREATE TABLE chunks(text TEXT)')
        db.execute("INSERT INTO chunks VALUES ('evidence')")
    archive = backup.create_backup(database, uploads, ledger, knowledge_db=index,
                                   knowledge_root=corpus, output_dir=tmp_path)
    restored = tmp_path / 'restored'
    restored.mkdir()
    with tarfile.open(archive) as package:
        package.extractall(restored, filter='data')
    assert not (restored / 'knowledge/index.sqlite3').exists()
    assert (restored / 'knowledge/paper.pdf').read_bytes() == b'research fixture'
    with sqlite3.connect(restored / 'knowledge.db') as db:
        assert db.execute('SELECT text FROM chunks').fetchone() == ('evidence',)
