"""Compact local-evidence attestation checks. Never loads/trains on history.

An authenticated submission attests a local validation run; hashes establish
identity, not predictive skill. Explicit approval and research gates still apply.
"""
import re
from pathlib import Path
from app.services import frost_analysis as analysis, frost_weather as weather, frost_vault as vault
from app.services.frost_xchange import PARSER_VERSION

VERSION='frost-local-verified-v2'
CODE_FILES=('app/services/frost_analysis.py','app/services/frost_weather.py',
    'app/services/frost_xchange.py','app/services/frost_model.py',
    'scripts/frost_local.py','scripts/frost_dataset.py')


def code_hash():
    root=Path(__file__).resolve().parents[2]
    return vault.digest(b'\n'.join((root/name).read_bytes().replace(b'\r\n',b'\n') for name in CODE_FILES))


def seal(value):
    body={k:v for k,v in value.items() if k!='fingerprint'}
    return dict(body,fingerprint=vault.digest(vault.canonical(body)))


def count(value):
    if type(value) is not int or value<0:raise ValueError('Invalid local manifest count.')
    return value


def checksum(value):
    if not isinstance(value,str) or not re.fullmatch('[0-9a-f]{64}',value):raise ValueError('Invalid local evidence SHA-256.')
    return value


def validate(manifest, *, sources=None, training=False, historical=False):
    if historical and training:raise ValueError('Read-only historical evidence cannot certify current training readiness.')
    if manifest.get('schema')!=VERSION or seal(manifest)['fingerprint']!=manifest.get('fingerprint'):
        raise ValueError('Local dataset manifest integrity mismatch.')
    legacy = historical and (manifest.get('version'),manifest.get('weather_version'),manifest.get('implementation_sha256')) in (
        ('frost-history-v2',weather.LEGACY_VERSION,'a533954b09bf8f4edc51ccac39471e484ccbee5604d27271b27f1fce131b8c23'),
        ('frost-history-v3',weather.VERSION,'c613a9e3d7744d919dcc01e47132283fd5bdff30cc6fb479481b11b997187998'),
        ('frost-history-v3',weather.VERSION,'56760304cc6757276c1ec65ffa3fd9b3c916a38f3dc2f2b68f234f0023bfd186'),
        # Phase 8 immutable corrected evidence: read-only replay, never a fresh
        # training/approval attestation under a later implementation.
        ('frost-history-v3',weather.VERSION,'383b9291f9e8845d76a830f1cb29fc1204ac952789eb23ef09c2c61ce8ce7386'))
    if (not legacy and (manifest.get('version')!=analysis.VERSION or manifest.get('weather_version')!=weather.VERSION)) or manifest.get('time_contract')!=analysis.TIME_CONTRACT or manifest.get('parser_version')!=PARSER_VERSION:
        raise ValueError('Local parser/weather/classification contract mismatch.')
    if not legacy and manifest.get('implementation_sha256')!=code_hash():raise ValueError('Local validation code changed; revalidate cached evidence locally.')
    source=manifest['source_catalog']
    if seal(source)['fingerprint']!=source.get('fingerprint') or not source['documents'] or len(source['documents'])>256:
        raise ValueError('Invalid source catalog proof.')
    if sources is not None and source!=sources:raise ValueError('Source catalog changed; refresh the local evidence manifest.')
    docs=source['documents'];ids=[d['id'] for d in docs]
    if ids!=sorted(set(ids)) or any(not d['verified'] or d['extraction_status']!='extracted' for d in docs):
        raise ValueError('Original evidence is incomplete or requires extraction review.')
    for document in docs:checksum(document['sha256']);count(document['source_rows'])
    catalog=manifest['catalog'];raw=sum(d['source_rows'] for d in docs)
    if count(catalog['source_rows'])!=raw or count(catalog['deduplicated_records'])+count(catalog['overlapping_rows'])+count(catalog['revisions'])!=raw:
        raise ValueError('Source and deduplication counts do not reconcile.')
    if catalog['revision_conflicts']:raise ValueError('Unresolved source revision conflicts block local readiness.')
    parts=manifest['partitions'];months=[p['month'] for p in parts]
    if months!=sorted(set(months)) or sum(count(p['records']) for p in parts)!=catalog['deduplicated_records']:
        raise ValueError('Local monthly evidence is incomplete.')
    for part in parts:checksum(part['sha256']);checksum(part['source_rows_sha256'])
    quality=manifest['quality'];n=catalog['deduplicated_records']
    if count(quality['weather_matched'])+count(quality['weather_unmatched'])!=n or sum(count(v) for v in quality['classifications'].values())!=n:
        raise ValueError('Weather/classification totals do not reconcile.')
    exclusions=quality['exclusions']
    if len(exclusions)!=len({(e['record_id'],e['reason']) for e in exclusions}) or any(not e['sources'] or e['reason'] not in ('weather_unmatched','incomplete_window','requires_review') for e in exclusions):
        raise ValueError('Quality exclusions require unique source-traceable dispositions.')
    documents={d['id']:d for d in docs}
    for exclusion in exclusions:
        for source in exclusion['sources']:
            document=documents.get(source['document_id'])
            if not document or source.get('document_sha256')!=document['sha256'] or type(source.get('source_row')) is not int or not 2<=source['source_row']<=document['source_rows']+1:
                raise ValueError('Quality exclusion provenance does not match the source catalog.')
    for key in ('weather_unmatched','incomplete_window','requires_review'):
        if sum(e['reason']==key for e in exclusions)!=quality[key]:raise ValueError('Undisposed quality exclusions block local readiness.')
    if manifest.get('source_origin')!='authenticated_frost_vault' or manifest.get('state')!='LOCAL DATA READY' or manifest.get('ready') is not True:
        raise ValueError('LOCAL DATA READY checks have not passed.')
    if training:
        eligible=manifest['eligibility'];classes=eligible['classes']
        if eligible['lead_hours'] not in (0,1,3,6) or sum(count(classes.get(k,0)) for k in ('0','1'))!=count(eligible['examples']) or not min(classes.get('0',0),classes.get('1',0)):
            raise ValueError('Local training class reconciliation failed.')
        checksum(eligible['examples_sha256'])
        if eligible['examples']>n-len({e['record_id'] for e in exclusions}) or classes.get('1',0)>quality['classifications'].get('LIKELY FROST PRESENT',0) or classes.get('0',0)>quality['classifications'].get('NON-FROST TREATMENT',0):
            raise ValueError('Training classes exceed eligible source evidence.')
    return manifest
