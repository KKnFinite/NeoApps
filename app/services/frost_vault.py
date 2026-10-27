"""FROST namespace on NeoApps' existing private R2 transport. No public URLs."""
import gzip
import hashlib
import json
import re
import zlib
import gc
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from app.services import neoscorpion_learning_vault as provider

PREFIX='frost-vault/v1/'
CATEGORIES=('original-xchange','extracted-xchange','spray-history','weather',
            'frost-observations','evaluations','training-datasets','treatment-context','manifests','models')
MAX_OBJECT_BYTES=64*1024*1024
_transport_scope=ContextVar('frost_transport_scope',default=None)


@contextmanager
def transport_scope():
    """One existing-provider client per bounded job, closed even on failure.

    Botocore clients own connection pools and cyclic model graphs. Creating
    hundreds in a single historical batch needlessly grows the web container.
    Context-local scope cannot leak a client across threads or credential sets.
    """
    if _transport_scope.get() is not None:
        yield
        return
    # A paused/idle batch must not construct an SDK client at all.
    scope={'config':None,'client':None}
    token=_transport_scope.set(scope)
    try:
        yield
    finally:
        _transport_scope.reset(token)
        close=getattr(scope['client'],'close',None)
        if callable(close): close()


def _client(config):
    scope=_transport_scope.get()
    if scope is None:
        return provider._r2_client(config)
    if scope['client'] is None or scope['config']!=config:
        close=getattr(scope['client'],'close',None)
        if callable(close): close()
        scope['config'],scope['client']=config,provider._r2_client(config)
    return scope['client']


def _bounded_transport(function):
    """Downloads need the same client cleanup as historical worker batches.

    Nested reads/writes reuse their caller's scope and never collect mid-batch.
    Standalone requests close pools and reclaim cyclic SDK models on all exits.
    """
    @wraps(function)
    def bounded(*args, **kwargs):
        if _transport_scope.get() is not None:
            return function(*args, **kwargs)
        try:
            with transport_scope():
                return function(*args, **kwargs)
        finally:
            gc.collect()
    return bounded


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(payload):
    return json.dumps(payload,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False,default=str).encode()


def namespace(gateway_id,category):
    if category not in CATEGORIES or int(gateway_id)<=0: raise ValueError('Invalid FROST namespace.')
    return f'{PREFIX}gateway-{int(gateway_id)}/{category}/'


def _authorized_key(key,gateway_id,*,legacy=False):
    if not isinstance(key,str) or '..' in key or not re.fullmatch(r'[A-Za-z0-9_/.-]+',key):
        raise ValueError('Invalid FROST evidence key.')
    allowed=any(key.startswith(namespace(gateway_id,category)) for category in CATEGORIES)
    if legacy:
        allowed=allowed or any(key.startswith(f'{prefix}/v1/{int(gateway_id)}/')
            for prefix in ('subzero-spray','neofrost-treatment-context'))
    if not allowed: raise ValueError('Evidence is outside this gateway FROST namespace.')


@_bounded_transport
def read_bytes(key,gateway_id,*,legacy=False):
    _authorized_key(key,gateway_id,legacy=legacy)
    config=provider._require_r2_config()
    try:
        response=_client(config).get_object(Bucket=config['bucket'],Key=key)
        stream=response['Body']
        try: raw=stream.read(MAX_OBJECT_BYTES+1)
        finally: stream.close()
        if len(raw)>MAX_OBJECT_BYTES or response.get('Metadata',{}).get('checksum')!=digest(raw):
            raise ValueError('Stored FROST object failed integrity verification.')
        return raw
    except ValueError: raise
    except Exception:
        raise provider.LearningVaultUnavailable('FROST storage is unavailable; evidence has not been discarded.') from None


@_bounded_transport
def put_bytes(key,raw,gateway_id):
    _authorized_key(key,gateway_id)
    if not raw or len(raw)>MAX_OBJECT_BYTES: raise ValueError('FROST object size is invalid.')
    config=provider._require_r2_config();client=_client(config)
    expected=digest(raw)
    try:
        existing=client.head_object(Bucket=config['bucket'],Key=key)
        if existing.get('Metadata',{}).get('checksum')!=expected:
            raise ValueError('Existing FROST evidence differs; no object was overwritten.')
    except ValueError: raise
    except Exception as exc:
        if getattr(exc,'response',{}).get('ResponseMetadata',{}).get('HTTPStatusCode')!=404:
            raise provider.LearningVaultUnavailable('FROST storage check failed. Retry remains pending.') from None
        try:
            client.put_object(Bucket=config['bucket'],Key=key,Body=raw,
                ContentType='application/octet-stream',Metadata={'checksum':expected,'vault':'frost','schema-version':'frost-vault/v1'})
        except Exception:
            raise provider.LearningVaultUnavailable('FROST durable write failed. Retry remains pending.') from None
    if read_bytes(key,gateway_id)!=raw:
        raise ValueError('FROST read-back verification failed.')
    return key


@_bounded_transport
def put_json(category,gateway_id,identity,payload):
    if not re.fullmatch(r'[A-Za-z0-9_-]+',str(identity)): raise ValueError('Invalid FROST evidence identity.')
    body=canonical(payload)
    if len(body)>MAX_OBJECT_BYTES: raise ValueError('Extracted evidence exceeds the FROST size limit.')
    key=f'{namespace(gateway_id,category)}{identity}/{digest(body)}.json.gz'
    put_bytes(key,gzip.compress(body,mtime=0),gateway_id)
    return key


@_bounded_transport
def read_json(key,gateway_id,*,max_bytes=MAX_OBJECT_BYTES):
    _authorized_key(key,gateway_id,legacy=True)
    config=provider._require_r2_config()
    try:
        response=_client(config).get_object(Bucket=config['bucket'],Key=key)
        stream=response['Body']
        try: compressed=stream.read(max_bytes+1)
        finally: stream.close()
        if len(compressed)>max_bytes: raise ValueError('Archived object exceeds the size limit.')
        decoder=zlib.decompressobj(31)
        raw=decoder.decompress(compressed,max_bytes+1)
        if len(raw)>max_bytes or not decoder.eof or decoder.unused_data:
            raise ValueError('Archived evidence is malformed or oversized.')
        checksum=digest(raw)
        # Release 3 metadata hashes the uncompressed JSON; FROST transport hashes
        # compressed bytes. The checksum in the immutable object name checks both.
        if key.rsplit('/',1)[-1]!=checksum+'.json.gz': raise ValueError('Archive content hash mismatch.')
        return json.loads(raw,parse_constant=lambda value: (_ for _ in ()).throw(ValueError('Nonfinite archive value.')))
    except ValueError: raise
    except Exception:
        raise provider.LearningVaultUnavailable('Archived FROST evidence could not be read or verified.') from None
