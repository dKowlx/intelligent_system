#!/usr/bin/env python3
"""Explicit research indexing. Preview is default; --apply performs local writes.

Paid embeddings additionally require --embed and a shared budget ledger.
"""
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import xml.etree.ElementTree as ET
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from studynavigator.knowledge import SCHEMA, SAFE_ID
from studynavigator.embeddings import EmbeddingsProvider
from studynavigator.live_budget import BudgetTransport


def prepare(root):
    manifest = json.loads((root/'manifest.json').read_text())
    entries = manifest if isinstance(manifest,list) else manifest.get('sources',manifest.get('articles',[]))
    if not entries or len(entries) > 20:
        raise ValueError('invalid manifest count')
    prepared = []
    seen = set()
    for item in entries:
        item = dict(item)
        identifier = item['id']
        identifier = identifier if identifier.startswith('kb-') else 'kb-' + identifier
        if not SAFE_ID.fullmatch(identifier) or identifier in seen:
            raise ValueError('invalid source id')
        seen.add(identifier)
        for required in ('title','authors','year','doi','topic_ru','kind','source_url','license','files'):
            if required not in item:
                raise ValueError('missing metadata: '+required)
        files = {}
        for kind, filename in item['files'].items():
            path = Path(filename)
            if not path.is_file() or not path.resolve().is_relative_to(root):
                candidates = [root/Path(filename).name, root/item['id']/Path(filename).name,
                              root/identifier/Path(filename).name]
                path = next((p for p in candidates if p.is_file()), candidates[0])
            path = path.resolve()
            if not path.is_relative_to(root):
                raise ValueError('source path outside root')
            files[kind] = str(path)
        item['files'] = files
        item['id'] = identifier
        path = Path(files.get('text') or files.get('xml') or '')
        if not path.is_file() or path.stat().st_size > 10_000_000:
            raise ValueError('bounded XML/text required')
        if path.suffix.lower() == '.xml':
            text = ' '.join(ET.fromstring(path.read_bytes()).itertext())
        else:
            text = path.read_text()
        text = ' '.join(text.split())
        chunks = [text[offset:offset+2800] for offset in range(0,len(text),2400)]
        if not chunks:
            raise ValueError('empty source')
        prepared.append((item,chunks))
    return prepared


async def run(args):
    root = Path(args.root).resolve()
    prepared = prepare(root)
    chunks = [(item,n,text) for item,parts in prepared for n,text in enumerate(parts)]
    input_bound = sum(len(text.encode())+512 for _,_,text in chunks)
    if len(chunks)>args.max_chunks or input_bound>args.max_input_bound:
        raise ValueError('index cap exceeded')
    print(json.dumps({'sources':len(prepared),'chunks':len(chunks),'input_token_upper_bound':input_bound,
                      'estimated_upper_rub':round(input_bound*0.0000101,6),'apply':args.apply,'embeddings':args.embed}))
    if not args.apply:
        return
    provider = None
    if args.embed:
        ledger = os.environ.get('STUDYNAV_LIVE_BUDGET_PATH')
        if not ledger:
            raise ValueError('explicit shared budget ledger required')
        provider = EmbeddingsProvider(os.environ['EMBEDDING_BASE_URL'],os.environ['LLM_API_KEY'],
            os.environ['EMBEDDING_DOC_MODEL'],os.environ['EMBEDDING_QUERY_MODEL'],
            BudgetTransport(ledger,budget_rub=float(os.environ['STUDYNAV_LIVE_BUDGET_RUB'])))
    target = Path(args.db)
    target.parent.mkdir(parents=True,exist_ok=True)
    with sqlite3.connect(target) as db:
        db.executescript(SCHEMA)
        # Cache each paid response immediately, even if a later chunk fails.
        for _,_,text in chunks:
            if provider:
                digest=hashlib.sha256(text.encode()).hexdigest()
                cached=db.execute('SELECT 1 FROM embeddings WHERE hash=? AND model=? AND dimensions=?',(digest,provider.doc_model,provider.dimensions)).fetchone()
                if not cached:
                    vector=await provider.embed(text)
                    db.execute('INSERT INTO embeddings VALUES (?,?,?,?)',(digest,provider.doc_model,provider.dimensions,json.dumps(vector)))
                    db.commit()
        # Replace corpus metadata/chunks atomically; cached vectors survive reindexing.
        db.execute('DELETE FROM sources'); db.execute('DELETE FROM chunks'); db.execute('DELETE FROM chunks_fts')
        for item,_ in prepared:
            db.execute('INSERT INTO sources VALUES (?,?)',(item['id'],json.dumps(item,ensure_ascii=False)))
        for item,n,text in chunks:
            digest=hashlib.sha256(text.encode()).hexdigest()
            identifier=f"{item['id']}-{n}"
            db.execute('INSERT INTO chunks VALUES (?,?,?,?,?)',(identifier,item['id'],text,f'fragment {n+1}',digest))
            db.execute('INSERT INTO chunks_fts VALUES (?,?)',(identifier,text))

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',required=True); parser.add_argument('--db',required=True)
    parser.add_argument('--apply',action='store_true'); parser.add_argument('--embed',action='store_true')
    parser.add_argument('--max-chunks',type=int,default=500); parser.add_argument('--max-input-bound',type=int,default=2_000_000)
    try:
        asyncio.run(run(parser.parse_args()))
    except Exception as exc:
        print('Indexing failed: '+type(exc).__name__,file=sys.stderr)
        sys.exit(1)
