"""Historical RFD observations using IEM's documented ASOS archive.

Separate from today's forecast cache, sharing FROST's existing durable vault.
Routine+SPECI METAR, UTC, original provider bytes preserved by the worker.
"""
import csv
import io
import math
import re
import threading
import time
from datetime import datetime, timezone
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

URL = 'https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py'
STATION = dict(icao='KRFD', provider_id='RFD', network='IL_ASOS', latitude=42.19151,
    longitude=-89.08760, timezone='America/Chicago', archive_begin='1951-03-01',
    documentation='https://mesonet.agron.iastate.edu/request/download.phtml',
    license='IEM public-domain, as-is observations with limited QC')
LEGACY_VERSION = 'iem-rfd-routine-special-v1'
VERSION = 'iem-rfd-routine-special-v2'
_lock = threading.Lock()
_last = 0.0


class WeatherUnavailable(RuntimeError):
    def __init__(self, message, retry_after=60):
        super().__init__(message)
        self.retry_after = retry_after


def fetch(start, end):
    global _last
    params = [('station','RFD'), ('network','IL_ASOS'), ('data','all'), ('sts',start.isoformat()),
        ('ets',end.isoformat()), ('tz','UTC'), ('format','onlycomma'), ('latlon','yes'),
        ('missing','M'), ('trace','T'), ('report_type','3'), ('report_type','4')]
    url = URL+'?'+urlencode(params)
    try:
        # At most one public-provider request/second/process; durable task lease
        # serializes a gateway across workers. 429 is always retried, not success.
        with _lock:
            delay = 1.1-(time.monotonic()-_last)
            if delay > 0: time.sleep(delay)
            _last = time.monotonic()
            with urlopen(Request(url, headers={'User-Agent':'NeoApps-FROST historical research/1.0'}), timeout=40) as response:
                raw = response.read(16*1024*1024+1)
                if len(raw) > 16*1024*1024: raise WeatherUnavailable('Weather response exceeds bounded monthly limit.')
        return raw, url
    except HTTPError as exc:
        retry = str(exc.headers.get('Retry-After','60'))
        raise WeatherUnavailable(f'IEM HTTP {exc.code}; automatic retry pending.', min(86400,max(60,int(retry))) if retry.isdigit() else 300) from None
    except (OSError, TimeoutError):
        raise WeatherUnavailable('Historical weather connection failed; automatic retry pending.') from None


def parse(raw, start, end, *, version=VERSION):
    if version not in (VERSION, LEGACY_VERSION):
        raise ValueError('Unsupported historical observation parser.')
    def number(value):
        try:
            value = float(value)
            return value if math.isfinite(value) else None
        except (ValueError, TypeError): return None
    try:
        reader = csv.DictReader(io.StringIO(raw.decode('utf-8-sig')))
        if not {'station','valid','tmpf','dwpf','metar'} <= set(reader.fieldnames or []):
            raise ValueError('Unrecognized historical weather response.')
        rows, seen = [], set()
        for raw_row in reader:
            if len(rows) >= 10000: raise ValueError('Monthly observation limit exceeded.')
            if raw_row['station'] != 'RFD': raise ValueError('Historical station mismatch.')
            for key, expected in (('lat',STATION['latitude']),('lon',STATION['longitude'])):
                if number(raw_row.get(key)) is None or abs(number(raw_row[key])-expected) > .001:
                    raise ValueError('Historical station coordinates mismatch.')
            at = datetime.strptime(raw_row['valid'], '%Y-%m-%d %H:%M').replace(tzinfo=timezone.utc)
            if not start <= at < end: raise ValueError('Observation outside requested UTC period.')
            identity = (at, raw_row['metar'])
            if identity in seen: continue
            seen.add(identity)
            t, d = number(raw_row.get('tmpf')), number(raw_row.get('dwpf'))
            t = (t-32)*5/9 if t is not None else None
            d = (d-32)*5/9 if d is not None else None
            coverage = [raw_row.get('skyc'+str(i)) for i in range(1,5)]
            coverage = [item for item in coverage if item not in ('M','',None)]
            ceilings = [number(raw_row.get('skyl'+str(i))) for i in range(1,5) if raw_row.get('skyc'+str(i)) in ('BKN','OVC','VV')]
            ceilings = [item for item in ceilings if item is not None]
            if version == VERSION:
                # IEM skyl* is already feet, but skyc can contain padded VV.
                # METAR VVhhh is hundreds of feet AGL (FAA AIM 7-1).
                # Preserve the raw report, and never decode a remark as sky.
                codes = [(raw_row.get('skyc'+str(i)) or '').strip().upper() for i in range(1,5)]
                coverage = [code for code in codes if code not in ('M','')]
                ceilings = [number(raw_row.get('skyl'+str(i))) for i,code in enumerate(codes,1) if code in ('BKN','OVC','VV')]
                body = raw_row['metar'].split(' RMK',1)[0]
                vv = re.search(r'(?<!\S)VV\s*(\d{3}|///)(?!\S)',body)
                if vv and 'VV' in coverage:
                    ceilings = [number(raw_row.get('skyl'+str(i))) for i,code in enumerate(codes,1) if code in ('BKN','OVC')]
                    if vv[1] != '///':ceilings.append(int(vv[1])*100)
                ceilings = [height for height in ceilings if height is not None and height >= 0]
            rh = number(raw_row.get('relh'))
            if rh is None and t is not None and d is not None and t > -243.04 and d > -243.04:
                rh = 100*math.exp(17.625*d/(243.04+d)-17.625*t/(243.04+t))
            rows.append(dict(utc=at.isoformat(), source_row=reader.line_num, temperature_c=t, dewpoint_c=d,
                spread_c=t-d if t is not None and d is not None else None, relative_humidity=rh,
                wind_kt=number(raw_row.get('sknt')), wind_direction_degrees=number(raw_row.get('drct')) if number(raw_row.get('sknt')) not in (None,0) else None,
                gust_kt=number(raw_row.get('gust')), visibility_sm=number(raw_row.get('vsby')),
                cloud_layers=coverage, clear_sky=all(item in ('CLR','SKC','NSC','NCD') for item in coverage) if coverage else None,
                ceiling_ft=min(ceilings) if ceilings else None,
                weather_codes=raw_row.get('wxcodes') if raw_row.get('wxcodes') not in ('M','') else None,
                precipitation_since_hourly_reset_in=number(raw_row.get('p01i')),
                precipitation_trace=raw_row.get('p01i')=='T', raw_metar=raw_row['metar'],
                missing_fields=[key for key,value in raw_row.items() if value in ('M','',None)]))
        # Same-minute corrected/conflicting METARs have no trustworthy receipt
        # ordering in this export. Preserve both raw reports, but exclude an
        # ambiguous measurement rather than choosing lexicographically.
        by_time = {}
        for row in rows:
            measurement = {key:value for key,value in row.items() if key not in ('source_row','raw_metar','missing_fields')}
            by_time.setdefault(row['utc'],set()).add(str(sorted(measurement.items())))
        for row in rows:
            row['conflicting_same_time'] = len(by_time[row['utc']]) > 1
        return sorted(rows, key=lambda row: (row['utc'],row['raw_metar']))
    except (UnicodeError,csv.Error,KeyError) as exc:
        raise ValueError('Malformed historical weather evidence.') from exc
