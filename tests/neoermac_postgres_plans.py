"""EXPLAIN actual captured poll SQL against isolated synthetic PostgreSQL data."""
import json
from pathlib import Path
from datetime import datetime, time, timedelta
from sqlalchemy import event, text
from flask import g
from app.extensions import db
from app.models import NeoErmacDoorPull, NeoErmacUldRequest, NeoSektorUldOnTheWayEvent, SortDateMission
from app.services.sort_timeline import ensure_sort_timeline_settings
from app.models import SortDateOperation, SortDateParkingAssignment, SortDateGoogleMissionLink

def explain_polls(fixture):
    gateway=fixture.gateway
    operation=fixture.operation
    fixture._add_master_departure('UPS501','SDF')
    for i in range(49):
        fixture._add_operation_departure('UPS'+str(600+i),'X'+str(i),tail='N'+str(i),parking='B'+str(i+1))
        fixture._add_master_departure('UPS'+str(600+i),'X'+str(i))
    current_missions=SortDateMission.query.all()
    db.session.add_all([SortDateGoogleMissionLink(sort_date_operation_id=operation.id,
        sort_date_mission_id=m.id,mission_type='departure',source_sheet='Departures',source_row=i+2)
        for i,m in enumerate(current_missions)])
    ensure_sort_timeline_settings(gateway)
    db.session.commit()
    # 100 prior sorts, 50 missions/pulls/parking/link rows per sort.
    for day in range(1,101):
        prior=SortDateOperation(gateway_id=gateway.id,gateway_code=gateway.code,
            sort_date=operation.sort_date-timedelta(days=day),sort_name='night')
        db.session.add(prior);db.session.flush()
        missions=[]
        for i in range(50):
            missions.append(SortDateMission(sort_date_operation_id=prior.id,sort_date=prior.sort_date,
                gateway_code=gateway.code,sort_name='night',mission_type='departure',mission_source='master',
                flight_number='UPS'+str(600+i),origin=gateway.code,destination='X'+str(i),
                planned_datetime_local=datetime.combine(prior.sort_date,time(2,15)),
                planned_datetime_utc=datetime.combine(prior.sort_date,time(7,15))))
        db.session.add_all(missions);db.session.flush()
        db.session.add_all([NeoErmacDoorPull(gateway_id=gateway.id,sort_date_operation_id=prior.id,
            sort_date_mission_id=m.id,door='D1',destination=m.destination) for m in missions])
        db.session.add_all([SortDateParkingAssignment(sort_date_operation_id=prior.id,
            tail_number='N'+str(i),position_code='B'+str(i+1),lane_number=1) for i in range(50)])
        # Include arrivals, links, ULD requests and expiring events as real
        # revision dependencies, not empty tables that trivially scan cheaply.
        arrivals=[SortDateMission(sort_date_operation_id=prior.id,sort_date=prior.sort_date,
            gateway_code=gateway.code,sort_name='night',mission_type='arrival',mission_source='master',
            flight_number='ARR'+str(i),origin='SDF',destination=gateway.code,
            api_assumed_arrived_time_utc=datetime.combine(prior.sort_date,time(6)))
            for i in range(25)]
        db.session.add_all(arrivals)
        db.session.add_all([SortDateGoogleMissionLink(sort_date_operation_id=prior.id,
            sort_date_mission_id=m.id,mission_type='departure',source_sheet='Departures',source_row=i+2)
            for i,m in enumerate(missions)])
        db.session.add_all([NeoErmacUldRequest(gateway_id=gateway.id,sort_date_operation_id=prior.id,
            door='D'+str(i+1),a2_count=1) for i in range(50)])
        db.session.add_all([NeoSektorUldOnTheWayEvent(gateway_id=gateway.id,sort_date_operation_id=prior.id,
            door='D'+str(i+1),uld_type='A2',quantity=1,
            expires_at_utc=datetime.combine(prior.sort_date,time(3))) for i in range(50)])
    db.session.commit()
    # Current pulls are real signed writes, not fabricated current aggregates.
    fixture.post(fixture.form())
    with db.engine.begin() as conn: conn.execute(text('ANALYZE'))
    routes={'Door View':'/neoermac/door-view/state?door=D1&',
        'Building Lineup':'/neoermac/building-lineup/state?',
        'Upcoming Pulls':'/neoermac/upcoming-pulls/state?',
        'View Outbound':'/neoermac/view-outbound/state?'}
    report={'fixture':{'current_missions':50,'prior_operations':100,'historical_missions':7500,
        'historical_pulls':5000,'historical_parking':5000,'historical_links':5000,'historical_uld_requests':5000,'historical_on_way_events':5000},'screens':{}}
    for name,path in routes.items():
        g.__dict__.clear();db.session.remove()
        response=fixture.client.get(path+'revision=old')
        assert response.status_code==200,(name,response.status_code)
        revision=response.get_json()['revision']
        for changed in (False, True):
            g.__dict__.clear();db.session.remove()
            captured=[]
            commits=[]
            def record(conn,cursor,statement,parameters,context,many):
                captured.append((statement,parameters))
            def committed(conn):
                commits.append(True)
            event.listen(db.engine,'before_cursor_execute',record)
            event.listen(db.engine,'commit',committed)
            try:
                response=fixture.client.get(path+'revision='+('old' if changed else revision))
            finally:
                event.remove(db.engine,'before_cursor_execute',record)
                event.remove(db.engine,'commit',committed)
            assert response.status_code==200
            assert response.get_json()['changed'] is changed
            assert all(s.lstrip().upper().startswith('SELECT') for s,p in captured)
            assert not commits
            plans=[]
            with db.engine.connect() as conn:
                for sql,params in captured:
                    plan=conn.exec_driver_sql('EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) '+sql,params).scalar()[0]
                    plans.append({'sql':sql,'parameters':params,'plan':plan})
            report['screens'][name+(' changed' if changed else ' unchanged')]={
                'selects':len(captured),'bytes':len(response.data),'writes':0,
                'commits':len(commits),'changed':changed,'plans':plans}
    report['indexes']=[dict(row) for row in db.session.execute(text(
        "SELECT tablename,indexname,indexdef FROM pg_indexes WHERE schemaname=:schema"),
        {'schema':fixture.schema}).mappings() if row['tablename'] in (
            'sort_date_missions','neoermac_door_pulls','neoermac_building_lineups',
            'sort_date_parking_assignments','sort_date_operations','master_flight_schedules')]
    target=Path('instance/neoermac-postgres-proof')
    target.mkdir(exist_ok=True)
    def scans(node):
        result=[]
        if 'Scan' in node['Node Type']:
            result.append({k:node[k] for k in ('Node Type','Relation Name','Index Name','Actual Rows',
                'Rows Removed by Filter','Shared Hit Blocks','Shared Read Blocks') if k in node})
        for child in node.get('Plans',[]):result.extend(scans(child))
        return result
    for name,screen in report['screens'].items():
        print(name,screen['selects'],'SELECT',screen['bytes'],'bytes')
        for entry in screen['plans']:
            entry['scans']=scans(entry['plan']['Plan'])

    (target/'plans.json').write_text(json.dumps(report,indent=2,default=str),encoding='utf-8')
    return report
