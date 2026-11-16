import asyncio
import json
import sqlite3
import pytest
from studynavigator.embeddings import validate_vector
from studynavigator.knowledge import KnowledgeStore, SCHEMA

@pytest.mark.parametrize('vector',[[1,2],[float('nan')]*768,[float('inf')]*768,[0]*768])
def test_invalid_vectors(vector):
    with pytest.raises(ValueError):
        validate_vector(vector)

def test_scope_and_lexical_fallback(tmp_path):
    path=tmp_path/'knowledge.sqlite3'
    with sqlite3.connect(path) as db:
        db.executescript(SCHEMA)
        db.execute('INSERT INTO sources VALUES (?,?)',('kb-study',json.dumps({'title':'Invented study','files':{},'scope':'project','kind':'requirement'})))
        db.execute('INSERT INTO chunks VALUES (?,?,?,?,?)',('kb-study-0','kb-study','Planning improves clarity','section 1','hash'))
        db.execute('INSERT INTO chunks_fts VALUES (?,?)',('kb-study-0','Planning improves clarity'))
    store=KnowledgeStore(path)
    result=asyncio.run(store.asearch('planning'))
    assert result['retrieval_status']=='lexical_only'
    assert result['sources'][0]['scope']=='shared_research'
    assert result['sources'][0]['kind']=='research'
    assert result['sources'][0]['due_date'] is None
    assert store.get_source('../study') is None
    assert store.resolve_file('kb-study') is None
    assert KnowledgeStore(tmp_path/'missing').search('planning')['sources']==[]

def test_wrong_model_vectors_not_used(tmp_path):
    path=tmp_path/'knowledge.sqlite3'
    class Fake:
        query_model='query';doc_model='doc';dimensions=768
        async def embed(self,*args,**kwargs):return [1]*768
    with sqlite3.connect(path) as db:
        db.executescript(SCHEMA)
        db.execute('INSERT INTO sources VALUES (?,?)',('kb-study','{}'))
        db.execute('INSERT INTO chunks VALUES (?,?,?,?,?)',('kb-study-0','kb-study','Planning','section','hash'))
        db.execute('INSERT INTO chunks_fts VALUES (?,?)',('kb-study-0','Planning'))
        db.execute('INSERT INTO embeddings VALUES (?,?,?,?)',('hash','different',768,json.dumps([1]*768)))
    result=asyncio.run(KnowledgeStore(path,Fake()).asearch('planning'))
    assert result['retrieval_status']=='lexical_only'
    assert result['degraded_reason']=='embedding_index_missing'

def test_index_manifest_invented_only(tmp_path):
    from tools.index_knowledge import prepare
    text=tmp_path/'invented.txt';text.write_text('Invented planning research, no real article.')
    item={'id':'sample','title':'Invented','authors':['Example'],'year':2026,'doi':None,
          'topic_ru':'Планирование','kind':'research','source_url':'https://example.org',
          'license':'CC0','files':{'text':str(text)}}
    (tmp_path/'manifest.json').write_text(json.dumps({'sources':[item]}))
    prepared=prepare(tmp_path)
    assert prepared[0][0]['id']=='kb-sample'
    assert prepared[0][1]==['Invented planning research, no real article.']
    item['id']='../escape'
    (tmp_path/'manifest.json').write_text(json.dumps([item]))
    with pytest.raises(ValueError):prepare(tmp_path)

def test_chunk_citations_are_exact_and_public(tmp_path):
    path=tmp_path/'knowledge.sqlite3'
    with sqlite3.connect(path) as db:
        db.executescript(SCHEMA)
        db.execute('INSERT INTO sources VALUES (?,?)',('kb-study',json.dumps({'title':'Invented','kind':'meta-analysis','files':{'pdf':'/secret/path.pdf'},'license':{'internal':'blob'}})))
        for n in range(2):
            identifier=f'kb-study-{n}'
            db.execute('INSERT INTO chunks VALUES (?,?,?,?,?)',(identifier,'kb-study',f'Planning result {n}',f'section {n}',str(n)))
            db.execute('INSERT INTO chunks_fts VALUES (?,?)',(identifier,f'Planning result {n}'))
    store=KnowledgeStore(path)
    sources=store.search('Planning')['sources']
    assert len({s['source_id'] for s in sources})==2
    for source in sources:
        exact=store.get_source(source['source_id'])
        assert exact['excerpt']==source['excerpt']
        assert exact['study_kind']=='meta-analysis'
        assert 'files' not in exact and 'license' not in exact
        assert '/secret' not in json.dumps(exact)


@pytest.mark.parametrize('cached', [None, ('old-model', 768, [1]*768),
                                     ('doc', 256, [1]*256), ('doc', 768, [0]*768),
                                     ('doc', 768, [1,2])])
def test_missing_stale_or_invalid_document_index_never_pays_for_query(tmp_path, cached):
    path = tmp_path/'knowledge.sqlite3'
    class NeverPaid:
        query_model='query'; doc_model='doc'; dimensions=768
        calls=0
        async def embed(self, *args, **kwargs):
            self.calls += 1
            raise AssertionError('No paid query without usable document index')
    with sqlite3.connect(path) as db:
        db.executescript(SCHEMA)
        db.execute('INSERT INTO sources VALUES (?,?)', ('kb-study', '{}'))
        db.execute('INSERT INTO chunks VALUES (?,?,?,?,?)', ('kb-study-0','kb-study','Planning','section','hash'))
        db.execute('INSERT INTO chunks_fts VALUES (?,?)', ('kb-study-0','Planning'))
        if cached:
            model, dimensions, vector = cached
            db.execute('INSERT INTO embeddings VALUES (?,?,?,?)', ('hash',model,dimensions,json.dumps(vector)))
    provider=NeverPaid()
    for _ in range(2):
        result=asyncio.run(KnowledgeStore(path,provider).asearch('planning'))
        assert result['degraded_reason']=='embedding_index_missing'
        assert result['sources'][0]['source_id']=='kb-study-0'
    assert provider.calls==0
