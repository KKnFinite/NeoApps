"""Bounded extraction of the actual supplied Xchange deicing-log CSV contract.

Clock values and ApplicationDate are preserved separately. The export does not
establish whether the application date is a calendar day or operational night.
Created/modified UTC fields are NOT used as spray timestamps.
"""
import csv
import io
import hashlib
from datetime import datetime
from decimal import Decimal, InvalidOperation

PARSER_VERSION = 'xchange-deicing-log/v1'
MAX_DOCUMENT_BYTES = 10 * 1024 * 1024
MAX_ROWS = 25000
REQUIRED = frozenset({'RecordID','ApplicationNumber','GatewayCode','ApplicationDate',
    'TailNumber','TruckNumber','Reason','Precipitation','AmbientTemp','DewPoint',
    'LiquidUOM','TempUOM','Type1Used','Type1SKU','Type1Concentration','StartTime1','EndTime1',
    'Type4Used','Type4SKU','Type4AConcentration','StartTime4','EndTime4','Driver','Operator','Notes'})


def extract(raw, *, gateway_code):
    if not raw or len(raw) > MAX_DOCUMENT_BYTES:
        raise ValueError('Choose a nonempty CSV file no larger than 10 MiB.')
    try:
        text = raw.decode('utf-8-sig')
        if '\x00' in text: raise ValueError('CSV contains binary data.')
        reader = csv.reader(io.StringIO(text, newline=''), strict=True)
        headers = next(reader)
        if not headers or len(headers) != len(set(headers)) or len(headers)>128:
            raise ValueError('CSV headers are empty, duplicated, or exceed the format limit.')
        rows=[]
        for values in reader:
            if not values or not any(values): continue
            if len(values)!=len(headers): raise ValueError('CSV row does not match its headers.')
            if len(rows)>=MAX_ROWS: raise ValueError('CSV exceeds the 25,000-record import limit.')
            rows.append(dict(zip(headers,values)))
    except (UnicodeError,csv.Error,StopIteration) as exc:
        raise ValueError('Malformed CSV. Use the original UTF-8 Xchange export.') from exc
    digest=hashlib.sha256(raw).hexdigest()
    result=dict(schema_version=PARSER_VERSION,source_sha256=digest,source_bytes=len(raw),
        headers=headers,rows=[],issues=[],training_eligible=False,
        source_row_basis='Logical CSV record number; header is row 1, not physical text line number',
        timestamp_basis='ApplicationDate and local clocks retained separately; calendar/operational-date convention unverified',
        missing_contract_fields=sorted(REQUIRED-set(headers)))
    if result['missing_contract_fields']:
        result.update(status='unsupported',source_row_count=len(rows),contribution_count=0)
        return result
    ids=set(); dates=[]; contributions=0
    def number(value):
        if not value.strip(): return None
        try: parsed=Decimal(value)
        except InvalidOperation: raise ValueError('Invalid numeric value.') from None
        if not parsed.is_finite(): raise ValueError('Nonfinite numeric value.')
        return str(parsed)
    for index, raw_row in enumerate(rows,2):
        item=dict(source_row=index,source_record_id=raw_row['RecordID'],raw=raw_row,
            tail=raw_row['TailNumber'] or None,truck=raw_row['TruckNumber'] or None,
            application_number=raw_row['ApplicationNumber'] or None,
            application_date=None,reason_code=raw_row['Reason'] or None,
            precipitation=raw_row['Precipitation'] or None,
            temperature=raw_row['AmbientTemp'] or None,dew_point=raw_row['DewPoint'] or None,
            temperature_unit=raw_row['TempUOM'] or None,driver_source=raw_row['Driver'] or None,
            operator_source=raw_row['Operator'] or None,fluids=[],
            # A treatment reason is source evidence, not a validated frost label.
            frost_observed=None,training_eligible=False)
        try:
            if raw_row['GatewayCode'].strip().upper()!=gateway_code.upper():
                raise ValueError('Source gateway does not match the importing gateway.')
            if not raw_row['RecordID'] or raw_row['RecordID'] in ids:
                raise ValueError('Missing or repeated source RecordID; review before use.')
            ids.add(raw_row['RecordID'])
            day=datetime.strptime(raw_row['ApplicationDate'],'%m/%d/%Y').date()
            item['application_date']=day.isoformat();dates.append(day)
            for phase,fluid in (('1','TYPE I'),('4','TYPE IV')):
                quantity=number(raw_row['Type'+phase+'Used'])
                if quantity is None: continue
                if Decimal(quantity)<0: raise ValueError('Negative applied-fluid quantity.')
                if Decimal(quantity)==0: continue  # Recorded zero is retained in raw, not invented spray.
                start,end=raw_row['StartTime'+phase],raw_row['EndTime'+phase]
                for clock in (start,end):
                    if clock: datetime.strptime(clock,'%H:%M')
                concentration=number(raw_row['Type1Concentration' if phase=='1' else 'Type4AConcentration'])
                if concentration is not None and not 0<=Decimal(concentration)<=100:
                    raise ValueError('Invalid recorded concentration.')
                item['fluids'].append(dict(source_phase=phase,fluid_type=fluid,
                    quantity=quantity,quantity_unit=raw_row['LiquidUOM'] or None,
                    product_sku=raw_row['Type'+phase+'SKU'] or None,
                    concentration=concentration,start_local_clock=start or None,end_local_clock=end or None,
                    surface=None,shared_pass_id=None,occurred_at_utc=None))
                if not start or not end:
                    result['issues'].append(dict(source_row=index,field='StartTime'+phase+'/EndTime'+phase,
                        message='Positive quantity has missing pass timing; no time reconstructed.'))
            contributions+=len(item['fluids'])
        except ValueError as exc:
            result['issues'].append(dict(source_row=index,message=str(exc)))
            item['requires_review']=True
        result['rows'].append(item)
    result.update(status='requires_review' if result['issues'] else 'extracted',
        source_row_count=len(rows),contribution_count=sum(len(row['fluids']) for row in result['rows']),
        date_start=min(dates).isoformat() if dates else None,date_end=max(dates).isoformat() if dates else None)
    return result
