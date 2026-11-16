import json
import sqlite3
from fastapi.testclient import TestClient
from studynavigator.config import Settings
from studynavigator.knowledge import SCHEMA
from studynavigator.main import create_app


def test_shared_research_routes_show_exact_chunk_and_safe_public_metadata(tmp_path, monkeypatch):
    root = tmp_path / 'knowledge'
    root.mkdir()
    pdf = root / 'original.pdf'
    pdf.write_bytes(b'%PDF-1.4\nsource fixture')
    dbpath = tmp_path / 'knowledge.sqlite3'
    with sqlite3.connect(dbpath) as db:
        db.executescript(SCHEMA)
        db.execute('INSERT INTO sources VALUES (?, ?)', ('kb-study', json.dumps({
            'title': 'Study <script>bad()</script>', 'authors': 'Test Author', 'year': 2022,
            'doi': '10.1234/test', 'kind': 'meta_analysis', 'source_url': 'https://example.org/study',
            'files': {'pdf': str(pdf)}, 'license_statement': 'Private raw metadata'})))
        db.execute('INSERT INTO chunks VALUES (?, ?, ?, ?, ?)', ('kb-study-0','kb-study','Exact first fragment','Abstract','hash1'))
        db.execute('INSERT INTO chunks VALUES (?, ?, ?, ?, ?)', ('kb-study-1','kb-study','Exact second fragment','Limitations','hash2'))
    monkeypatch.setenv('STUDYNAV_KNOWLEDGE_DB_PATH', str(dbpath))
    monkeypatch.setenv('STUDYNAV_KNOWLEDGE_ROOT', str(root))
    monkeypatch.setenv('STUDYNAV_MODE', 'offline')
    with TestClient(create_app(Settings(database_path=tmp_path/'app.db'))) as client:
        source = client.get('/api/sources/kb-study-1')
        assert source.status_code == 200
        assert source.json()['excerpt'] == 'Exact second fragment'
        assert source.json()['scope'] == 'shared_research'
        assert 'files' not in source.json()
        assert str(tmp_path) not in source.text
        page = client.get('/sources/kb-study-1')
        assert page.status_code == 200
        assert 'Exact second fragment' in page.text
        assert 'Exact first fragment' not in page.text
        assert '<script>bad()' not in page.text
        assert 'href="/research/kb-study.pdf"' in page.text
        assert client.get('/research/kb-study.pdf').content == pdf.read_bytes()
        assert client.get('/research/kb-missing.pdf').status_code == 404
        assert client.get('/api/sources/kb-missing').status_code == 404
