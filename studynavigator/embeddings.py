"""Bounded embeddings adapter. Paid calls require the shared budget transport."""
import asyncio
import math
import httpx


def validate_vector(value, dimensions=768):
    if not isinstance(value, list) or len(value) != dimensions:
        raise ValueError('embedding_dimension')
    if any(type(x) not in (int, float) or not math.isfinite(x) for x in value):
        raise ValueError('embedding_nonfinite')
    norm = math.sqrt(sum(x*x for x in value))
    if not math.isfinite(norm) or norm == 0:
        raise ValueError('embedding_zero')
    return [x / norm for x in value]


class EmbeddingsProvider:
    def __init__(self, base_url, api_key, doc_model, query_model, transport, dimensions=768, timeout=8):
        if not base_url.startswith('https://') or transport is None:
            raise ValueError('embeddings require HTTPS and budget transport')
        self.base_url, self.api_key = base_url.rstrip('/'), api_key
        self.doc_model, self.query_model = doc_model, query_model
        self.dimensions, self.timeout, self.transport = dimensions, min(timeout, 15), transport

    async def embed(self, text, *, query=False):
        if not isinstance(text, str) or not text.strip() or len(text.encode()) > 24000:
            raise ValueError('embedding_input_limit')
        model = self.query_model if query else self.doc_model
        headers = {'Authorization': 'Bearer ' + self.api_key}
        if model.startswith('emb://'):
            headers = {'Authorization': 'Api-Key ' + self.api_key, 'OpenAI-Project': model.split('/')[2]}
        async with asyncio.timeout(self.timeout):
            async with httpx.AsyncClient(transport=self.transport, timeout=self.timeout) as client:
                response = await client.post(self.base_url + '/embeddings', headers=headers,
                    json={'model': model, 'input': text, 'dimensions': self.dimensions, 'encoding_format': 'float'})
                response.raise_for_status()
                data = response.json()
                if data.get('model', model) != model:
                    raise ValueError('embedding_model_mismatch')
                return validate_vector(data['data'][0]['embedding'], self.dimensions)
