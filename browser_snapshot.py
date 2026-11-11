"""Lightweight browser snapshots; saved notebook assets stay unchanged."""
import base64
from urllib.parse import urlencode


def browser_snapshot(snapshot, notebook, revision):
    store = dict(snapshot['store'])
    for key, record in store.items():
        src = record.get('props', {}).get('src', '')
        if record.get('typeName') == 'asset' and isinstance(src, str) and src.partition(',')[0] in ('data:image/png;base64', 'data:image/jpeg;base64', 'data:image/webp;base64'):
            query = urlencode({'notebook': notebook, 'asset': key, 'v': revision})
            store[key] = {**record, 'props': {**record['props'], 'src': '/api/board?' + query}}
    return {**snapshot, 'store': store}


def image_bytes(snapshot, asset_id):
    record = snapshot.get('store', {}).get(asset_id, {})
    src = record.get('props', {}).get('src', '')
    if record.get('typeName') != 'asset' or not isinstance(src, str):
        return None
    header, separator, encoded = src.partition(',')
    if not separator or header not in ('data:image/png;base64', 'data:image/jpeg;base64', 'data:image/webp;base64'):
        return None
    return base64.b64decode(encoded, validate=True), header[5:].split(';')[0]
