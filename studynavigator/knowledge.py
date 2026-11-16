"""Read-only shared research retrieval, independent from project requirements."""
import asyncio
from collections import OrderedDict
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
from .embeddings import validate_vector

DEFAULT_DB = '/opt/project-navigator/knowledge/index.sqlite3'
SAFE_ID = re.compile(r'^kb-[a-z0-9][a-z0-9_-]{0,100}$')
SCHEMA = '''CREATE TABLE IF NOT EXISTS sources(id TEXT PRIMARY KEY, metadata TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS chunks(id TEXT PRIMARY KEY, source_id TEXT NOT NULL, text TEXT NOT NULL, section TEXT, hash TEXT NOT NULL);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(id UNINDEXED, text);
CREATE TABLE IF NOT EXISTS embeddings(hash TEXT, model TEXT, dimensions INTEGER, vector TEXT, PRIMARY KEY(hash,model,dimensions));'''

class KnowledgeStore:
    def __init__(self, path=None, provider=None):
        self.path = Path(path).absolute() if path else Path('/__knowledge_disabled__/index.sqlite3')
        self.root = Path(os.environ.get('STUDYNAV_KNOWLEDGE_ROOT', str(self.path.parent))).resolve()
        self.provider = provider
        self._queries = OrderedDict()

    @classmethod
    def from_env(cls):
        provider = None
        required = ('EMBEDDING_BASE_URL','EMBEDDING_DOC_MODEL','EMBEDDING_QUERY_MODEL',
                    'LLM_API_KEY','STUDYNAV_LIVE_BUDGET_PATH','STUDYNAV_LIVE_BUDGET_RUB')
        if os.environ.get('STUDYNAV_MODE') == 'live' and all(os.environ.get(key) for key in required):
            from .embeddings import EmbeddingsProvider
            from .live_budget import BudgetTransport
            provider = EmbeddingsProvider(os.environ['EMBEDDING_BASE_URL'], os.environ['LLM_API_KEY'],
                os.environ['EMBEDDING_DOC_MODEL'], os.environ['EMBEDDING_QUERY_MODEL'],
                BudgetTransport(os.environ['STUDYNAV_LIVE_BUDGET_PATH'],
                    budget_rub=float(os.environ['STUDYNAV_LIVE_BUDGET_RUB'])),
                dimensions=int(os.environ.get('EMBEDDING_DIMENSIONS','768')))
        return cls(os.environ.get('STUDYNAV_KNOWLEDGE_DB_PATH'), provider)

    def _connect(self):
        return sqlite3.connect(self.path.as_uri() + '?mode=ro', uri=True)

    def _lookup(self, source_id):
        if not SAFE_ID.fullmatch(source_id or '') or not self.path.is_file():
            return None
        with self._connect() as db:
            chunk = db.execute('SELECT id,source_id,text,section FROM chunks WHERE id=?', (source_id,)).fetchone()
            if chunk is None:
                chunk = db.execute('SELECT id,source_id,text,section FROM chunks WHERE source_id=? ORDER BY id LIMIT 1', (source_id,)).fetchone()
            if chunk is None:
                return None
            row = db.execute('SELECT metadata FROM sources WHERE id=?', (chunk[1],)).fetchone()
        return (chunk,json.loads(row[0])) if row else None

    def get_source(self, source_id):
        found = self._lookup(source_id)
        if not found:
            return None
        chunk, data = found
        return {'id': chunk[0], 'source_id': chunk[0], 'article_id': chunk[1],
                'title': data.get('title'), 'authors': data.get('authors'), 'year': data.get('year'),
                'doi': data.get('doi'), 'scope': 'shared_research', 'kind': 'research',
                'study_kind': data.get('kind'), 'external_url': data.get('source_url'),
                'url': '/sources/' + chunk[0], 'file_url': '/research/' + chunk[1] + '.pdf',
                'document_url': '/research/' + chunk[1] + '.pdf', 'page': None,
                'excerpt': chunk[2], 'section': chunk[3], 'due_date': None}

    def resolve_file(self, source_id):
        found = self._lookup(source_id)
        if not found:
            return None
        filename = found[1].get('files', {}).get('pdf')
        if not filename:
            return None
        path = Path(filename).resolve()
        return path if path.is_relative_to(self.root) and path.is_file() else None

    def _rows(self):
        if not self.path.is_file():
            return []
        with self._connect() as db:
            return db.execute('SELECT id,source_id,text,section,hash FROM chunks').fetchall()

    def _result(self, rows, status='lexical_only', reason='embeddings_unavailable'):
        sources = []
        for row in rows:
            source = self.get_source(row[0])
            if source:
                sources.append({**source, 'chunk_id': row[0], 'excerpt': row[2], 'section': row[3]})
        return {'sources': sources, 'retrieval_status': status, 'degraded_reason': reason}

    def search(self, query, limit=5):
        if not isinstance(query, str) or len(query) > 4000:
            raise ValueError('knowledge_query_limit')
        limit = max(1, min(int(limit), 10))
        tokens = re.findall(r'[^\W_]+', query, re.UNICODE)[:32]
        if not tokens or not self.path.is_file():
            return self._result([])
        expression = ' OR '.join('"' + token.replace('"', '') + '"' for token in tokens)
        with self._connect() as db:
            rows = db.execute('SELECT c.id,c.source_id,c.text,c.section,c.hash FROM chunks_fts f JOIN chunks c ON c.id=f.id WHERE chunks_fts MATCH ? ORDER BY bm25(chunks_fts) LIMIT ?', (expression,limit)).fetchall()
        return self._result(rows)

    async def asearch(self, query, limit=5):
        lexical = self.search(query, limit)
        if self.provider is None or not self.path.is_file():
            return lexical
        try:
            async with asyncio.timeout(10):
                # Validate the configured document index before any paid query.
                # A missing/stale/corrupt index cannot benefit from an embedding.
                rows = self._rows()
                indexed = []
                with self._connect() as db:
                    for row in rows:
                        saved = db.execute('SELECT vector FROM embeddings WHERE hash=? AND model=? AND dimensions=?',
                            (row[4], self.provider.doc_model, self.provider.dimensions)).fetchone()
                        if saved:
                            try:
                                candidate = validate_vector(json.loads(saved[0]), self.provider.dimensions)
                            except (ValueError, TypeError):
                                continue
                            indexed.append((row, candidate))
                if not indexed:
                    return {**lexical, 'degraded_reason': 'embedding_index_missing'}
                key = (self.provider.query_model, self.provider.dimensions, hashlib.sha256(query.encode()).hexdigest())
                vector = self._queries.get(key)
                if vector is None:
                    vector = validate_vector(await self.provider.embed(query, query=True), self.provider.dimensions)
                    self._queries[key] = vector
                    if len(self._queries) > 128:
                        self._queries.popitem(last=False)
                ranked = [(sum(a*b for a,b in zip(vector,candidate)), row)
                          for row,candidate in indexed]
                # Reciprocal rank fusion preserves lexical evidence in a tiny corpus.
                scores, by_id = {}, {row[0]:row for row in rows}
                for rank, source in enumerate(lexical['sources']):
                    scores[source['chunk_id']] = 1/(60+rank)
                for rank, (_,row) in enumerate(sorted(ranked, key=lambda x:x[0],reverse=True)):
                    scores[row[0]] = scores.get(row[0],0)+1/(60+rank)
                selected = sorted(scores,key=scores.get,reverse=True)[:max(1,min(int(limit),10))]
                return self._result([by_id[key] for key in selected], 'hybrid', None)
        except (Exception, asyncio.TimeoutError):
            return {**lexical, 'degraded_reason':'embedding_request_failed'}
