"""Local HTTP capacity probe. Creates and drops ONLY a guarded scratch database on TEST_DATABASE_URL.

Run from backend: uv run python tools/loadtest.py --linux-client --output ../docs/loadtest-results.json
Requires Docker and the freshly built bp-load-{backend,frontend,client}:local images.
No production settings, application code, or shared test schema are changed.
"""

import argparse
import asyncio
from collections import Counter, defaultdict
from datetime import date
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def docker(*args, check=True, include_stderr=False):
    result = subprocess.run(["docker", *args], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if check and result.returncode:
        raise RuntimeError(f"docker {args[0]} failed (details suppressed to protect temporary credentials)")
    return (result.stdout + (result.stderr if include_stderr else '')).strip()


def port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def seed(url):
    # This process's app settings are pinned to the guarded disposable URL BEFORE importing models.
    from app.scripts.reset_test_db import assert_is_test_database
    assert_is_test_database(url)
    os.environ.update(DATABASE_URL=url, APP_ENV="development", AUTH_MODE="session")
    from sqlalchemy import create_engine, insert, text
    from sqlalchemy.orm import Session
    from app.models import Customer, CustomerType, Item, ItemType, OrganizationUser, Role, User
    from app.models.auth import UserCredential
    from app.core import passwords
    from app.modules.sales.models import Transaction, TransactionLine
    from tests.factories import make_org
    from tests.auth_support import make_session

    engine = create_engine(url)
    accounts = []
    password = "load test synthetic password 2026"
    encoded = passwords.hash_password(password)  # fixture optimisation only; real login is exercised separately
    with Session(engine) as db:
        for org_index in range(20):
            org = make_org(db, name=f"Capacity fixture {org_index}")
            users = [User(id=uuid.uuid4(), email=f"load-{org_index}-{i}@tests.invalid", name="Synthetic user") for i in range(50)]
            db.add_all(users)
            db.flush()
            db.execute(insert(OrganizationUser), [dict(organization_id=org.id, user_id=u.id, role=Role.OWNER if i == 0 else Role.EMPLOYEE) for i, u in enumerate(users)])
            db.execute(insert(UserCredential), [dict(user_id=u.id, password_hash=encoded, password_changed_at=__import__('datetime').datetime.now(__import__('datetime').timezone.utc)) for u in users])
            for u in users:
                handle = make_session(db, u)
                accounts.append(dict(org=str(org.id), email=u.email, token=handle.token, csrf=handle.csrf))
            customers = [dict(id=uuid.uuid4(), organization_id=org.id, name=f"Customer {i:04d}", customer_type=CustomerType.COMPANY) for i in range(250)]
            db.execute(insert(Customer), customers)
            db.execute(insert(Item), [dict(organization_id=org.id, name=f"Service {i:04d}", type=ItemType.SERVICE, unit="hour", price_ex_vat=100, vat_rate=25) for i in range(100)])
            orders = [dict(id=uuid.uuid4(), organization_id=org.id, billing_customer_id=customers[i % 250]['id'], transaction_date=date(2026, 10, 1), status="completed", currency="SEK") for i in range(250)]
            db.execute(insert(Transaction), orders)
            db.execute(insert(TransactionLine), [dict(organization_id=org.id, transaction_id=order['id'], position=j+1, description=f"Service {j}", unit="hour", quantity=1, unit_price_ex_vat=100, vat_rate=25, net_amount=100, vat_amount=25, gross_amount=125) for order in orders for j in range(3)])
            db.commit()
        db.execute(text("ANALYZE"))
        db.commit()
    engine.dispose()
    return accounts, password


def percentile(values, q):
    return round(sorted(values)[min(len(values)-1, int((len(values)-1)*q))] * 1000, 1) if values else None


async def phase(base, accounts, secret, name, concurrency, duration, paths, *, bff=False, login=False, password=None, interval=0, writes=False):
    import httpx
    latencies = []
    success_latencies = []
    statuses = Counter()
    by_endpoint = defaultdict(list)
    deadline = time.perf_counter() + duration
    begin = time.perf_counter()
    cpu_begin = time.process_time()
    limits = httpx.Limits(max_connections=max(1100, concurrency), max_keepalive_connections=max(1100, concurrency))
    async with httpx.AsyncClient(base_url=base, timeout=20, limits=limits, trust_env=False) as client:
        async def user(i):
            n = 0
            if interval:
                await asyncio.sleep(interval*i/concurrency)
            while time.perf_counter() < deadline:
                account = accounts[(i+n*concurrency) % len(accounts)]
                path = paths[n % len(paths)]
                headers = {"x-bff-secret": secret, "Authorization": "Bearer " + account['token'], "x-organization-id": account['org'], "x-csrf-token": account['csrf']}
                if bff:
                    headers = {"cookie": f"bp_session={account['token']}; bp_csrf={account['csrf']}", "x-csrf-token": account['csrf'], "Origin": os.environ.get('LOADTEST_PUBLIC_ORIGIN',base)}
                    path = f"/api/o/{account['org']}/{path.removeprefix('/api/')}"
                if login:
                    # Exercise fresh logins without rotating the sessions used by the business phases.
                    headers = {"x-bff-secret": secret}
                if name.startswith('ssr_'):
                    headers = {"cookie": f"bp_session={account['token']}; bp_csrf={account['csrf']}"}
                    path = f"/o/{account['org']}"
                t = time.perf_counter()
                try:
                    if login:
                        response = await client.post('/api/auth/login', headers=headers, json=dict(email=account['email'], password=password))
                    elif writes and n % 5 == 4:
                        path = f"/api/o/{account['org']}/customers" if bff else '/api/customers'
                        response = await client.post(path, headers=headers, json=dict(customer_type="person", name="Load-created customer"))
                    else:
                        response = await client.get(path, headers=headers)
                    status = str(response.status_code)
                    if name.startswith('ssr_') and response.status_code==200 and 'data-testid="dashboard-org"' not in response.text:
                        # Next may return HTTP 200 with a streamed error boundary: don't count it as success.
                        status='render_error_200'
                except httpx.HTTPError as exc:
                    status = type(exc).__name__
                elapsed = time.perf_counter()-t
                latencies.append(elapsed)
                statuses[status] += 1
                by_endpoint[path.split('?')[0] if not bff else path.split(account['org'])[-1].split('?')[0]].append(elapsed)
                if status.startswith('2'):
                    success_latencies.append(elapsed)
                n += 1
                if interval:
                    await asyncio.sleep(min(max(0, interval-elapsed),max(0,deadline-time.perf_counter())))
        await asyncio.gather(*(user(i) for i in range(concurrency)))
    elapsed = time.perf_counter()-begin
    result = dict(name=name, concurrency=concurrency, requested_duration_s=duration, elapsed_s=round(elapsed, 2), interval_s=interval,
                  requests=len(latencies), success_rps=round(len(success_latencies)/elapsed, 1), statuses=dict(statuses),
                  p50_ms=percentile(latencies,.5), p95_ms=percentile(latencies,.95), p99_ms=percentile(latencies,.99),
                  successful_p95_ms=percentile(success_latencies,.95), generator_cpu_percent=round((time.process_time()-cpu_begin)/elapsed*100,1),
                  endpoints={p:dict(requests=len(v),p95_ms=percentile(v,.95)) for p,v in by_endpoint.items()})
    print(json.dumps(result), flush=True)
    return result


async def suite(backend, frontend, accounts, secret, password, duration):
    import httpx
    simple = ['/api/customers?limit=25', '/api/items?limit=25', '/api/transactions?limit=25', '/api/invoices?limit=25', '/api/inventory/summary']
    async with httpx.AsyncClient(timeout=30, trust_env=False) as client:
        # Prove fixture authentication, routes, BFF and tenant isolation before benchmarking.
        a=accounts[0]
        headers={'x-bff-secret':secret,'authorization':'Bearer '+a['token'],'x-organization-id':a['org']}
        for p in simple + ['/api/invoices/summary', '/api/invoiceable-transactions?limit=25']:
            r=await client.get(backend+p,headers=headers)
            if r.status_code!=200:
                raise RuntimeError(f"Preflight {p}: {r.status_code}")
        r=await client.get(frontend+f"/api/o/{a['org']}/customers?limit=25",headers={'cookie':'bp_session='+a['token']})
        if r.status_code!=200:
            raise RuntimeError(f"BFF preflight: {r.status_code}")
        foreign=accounts[50]['org']
        r=await client.get(backend+'/api/customers',headers={**headers,'x-organization-id':foreign})
        assert r.status_code==404, 'Tenant isolation preflight failed'
        r=await client.post(backend+'/api/auth/login',headers={'x-bff-secret':secret},json={'email':a['email'],'password':password})
        assert r.status_code==200, f'Login preflight {r.status_code}'
    results=[]
    for c in [1,10,25,50,100,200,500,1000]:
        results.append(await phase(frontend,accounts,secret,'bff_business_mixed',c,duration,simple,bff=True,writes=True))
    for c in [1,10,50,100]:
        results.append(await phase(frontend,accounts,secret,'bff_dashboard',c,duration,['/api/invoices/summary'],bff=True))
    for c in [1,10,50,100]:
        results.append(await phase(backend,accounts,secret,'api_login',c,duration,['/api/auth/login'],login=True,password=password))
    results.append(await phase(frontend,accounts,secret,'bff_1000_paced',1000,max(30,duration),simple,bff=True,interval=10))
    # API comparison and a generator/BFF control to help locate bottlenecks.
    results.append(await phase(backend,accounts,secret,'api_business_mixed',50,duration,simple,writes=True))
    results.append(await phase(frontend,accounts,secret,'bff_health_control',50,duration,['/api/health']))
    return results


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--duration',type=int,default=15)
    parser.add_argument('--client-fixture',type=Path)
    parser.add_argument('--linux-client',action='store_true')
    parser.add_argument('--phase')
    parser.add_argument('--concurrency',type=int,default=1)
    parser.add_argument('--workers',type=int,default=1,help='Experimental worker override; defaults to the unchanged image CMD')
    parser.add_argument('--comparison',action='store_true',help='Shorter suite for comparing a worker configuration')
    parser.add_argument('--admission',action='store_true',help='Mount an experimental request admission wrapper; does not change app code')
    parser.add_argument('--diagnostic',action='store_true',help='Only business burst 100 and paced 1,000-account phases')
    parser.add_argument('--boundary',action='store_true',help='Refine the baseline burst threshold at 60, 75 and 90 concurrent requests')
    args=parser.parse_args()
    if not args.client_fixture and not args.linux_client:
        parser.error('Use --linux-client so each phase has independent generation and app-container recovery')
    args.output.parent.mkdir(parents=True,exist_ok=True)
    if args.client_fixture:
        fixture=json.loads(args.client_fixture.read_text())
        os.environ['LOADTEST_PUBLIC_ORIGIN']=fixture['origin']
        import httpx
        a=fixture['accounts'][0]
        with httpx.Client(timeout=20,trust_env=False) as client:
            headers={'x-bff-secret':fixture['secret'],'authorization':'Bearer '+a['token'],'x-organization-id':a['org']}
            response=client.get(fixture['backend']+'/api/customers?limit=1',headers=headers)
            if response.status_code!=200:
                raise RuntimeError(f'Authenticated preflight failed: {response.status_code}')
            foreign=fixture['accounts'][50]['org']
            response=client.get(fixture['backend']+'/api/customers?limit=1',headers={**headers,'x-organization-id':foreign})
            if response.status_code!=404:
                raise RuntimeError('Tenant isolation preflight failed')
        if args.phase:
            name=args.phase
            simple=['/api/customers?limit=25','/api/items?limit=25','/api/transactions?limit=25','/api/invoices?limit=25','/api/inventory/summary']
            paths=['/api/invoices/summary'] if name=='bff_dashboard' else ['/api/auth/login'] if name=='api_login' else ['/api/health'] if name=='bff_health_control' else simple
            bff=name.startswith('bff_') and name!='bff_health_control'
            base=fixture['frontend'] if name.startswith(('bff_','ssr_')) else fixture['backend']
            results=[asyncio.run(phase(base,fixture['accounts'],fixture['secret'],name,args.concurrency,args.duration,paths,bff=bff,login=name=='api_login',password=fixture['password'],interval=10 if name=='bff_1000_paced' else 30 if name=='ssr_1000_paced' else 0,writes=name in ('bff_business_mixed','api_business_mixed')))]
        else:
            results=asyncio.run(suite(fixture['backend'],fixture['frontend'],fixture['accounts'],fixture['secret'],fixture['password'],args.duration))
        args.output.write_text(json.dumps(results,indent=2)+'\n')
        return
    from sqlalchemy.engine import make_url
    from tests.db_support import disposable_database, disposable_roles, run_migrate, psycopg_url
    from app.scripts.bootstrap_roles import bootstrap
    from app.scripts.reset_test_db import test_database_url, assert_is_test_database
    test_url=test_database_url()
    assert_is_test_database(test_url)
    parsed=make_url(test_url)
    # Explicitly restrict the tool to the documented local test server, even if TEST_DATABASE_URL is changed.
    if parsed.host not in ('localhost','127.0.0.1') or parsed.port!=5433:
        raise RuntimeError('This probe only accepts the dedicated local test server on localhost:5433')
    suffix=secrets.token_hex(4)
    backend_name='bp-load-api-'+suffix
    frontend_name='bp-load-web-'+suffix
    network='bp-load-'+suffix
    secret=secrets.token_hex(24)
    backend_port,frontend_port=port(),port()
    resources=[]
    try:
        with disposable_roles('load_owner','load_app') as roles:
            with disposable_database('capacity') as url:
                bootstrap(psycopg_url(url),owner_role=roles['names']['load_owner'],owner_password=roles['passwords']['load_owner'],app_role=roles['names']['load_app'],app_password=roles['passwords']['load_app'])
                owner_url=make_url(url).set(username=roles['names']['load_owner'],password=roles['passwords']['load_owner']).render_as_string(hide_password=False)
                migrated=run_migrate(owner_url,env={'RUNTIME_DB_ROLE':roles['names']['load_app']})
                if migrated.returncode:
                    raise RuntimeError('Scratch database migration failed')
                print('Scratch database migrated; seeding 1,000 accounts / 20 organizations / 5,000 orders / 15,000 lines',flush=True)
                accounts,password=seed(url)
                runtime_url=make_url(url).set(host='host.docker.internal',username=roles['names']['load_app'],password=roles['passwords']['load_app']).render_as_string(hide_password=False)
                docker('network','create',network)
                resources.append(('network',network))
                with tempfile.TemporaryDirectory(prefix='bp-load-env-') as temp:
                    backend_env=Path(temp)/'backend.env'
                    backend_env.write_text('\n'.join(f'{k}={v}' for k,v in dict(APP_ENV='production',AUTH_MODE='session',DATABASE_URL=runtime_url,SECURITY_KEY=secrets.token_hex(24),BFF_INTERNAL_SECRET=secret,PUBLIC_ORIGIN='https://capacity.example.test',CORS_ORIGINS='[]',**json.loads(os.environ.get('LOAD_BACKEND_EXTRA_ENV','{}'))).items()))
                    frontend_env=Path(temp)/'frontend.env'
                    frontend_env.write_text('\n'.join(f'{k}={v}' for k,v in dict(APP_ENV='development',AUTH_MODE='session',PUBLIC_ORIGIN=f'http://127.0.0.1:{frontend_port}',BACKEND_URL=f'http://{backend_name}:8000',BFF_INTERNAL_SECRET=secret).items()))
                    entrypoint='admission_app:app' if args.admission else 'app.main:app'
                    mounts=['--mount',f'type=bind,source={Path(__file__).with_name("admission_app.py").resolve()},target=/app/admission_app.py,readonly'] if args.admission else []
                    worker_command=[] if args.workers==1 and not args.admission else ['uvicorn',entrypoint,'--host','0.0.0.0','--port','8000','--workers',str(args.workers),'--no-access-log','--no-proxy-headers','--no-server-header']
                    docker('run','-d','--name',backend_name,'--network',network,'--env-file',str(backend_env),'-p',f'127.0.0.1:{backend_port}:8000',*mounts,'bp-load-backend:local',*worker_command)
                    resources.append(('container',backend_name))
                    docker('run','-d','--name',frontend_name,'--network',network,'--env-file',str(frontend_env),'-p',f'127.0.0.1:{frontend_port}:3000','bp-load-frontend:local')
                    resources.append(('container',frontend_name))
                import httpx
                for _ in range(60):
                    try:
                        if httpx.get(f'http://127.0.0.1:{frontend_port}/api/ready',timeout=4,trust_env=False).status_code==200:
                            break
                    except httpx.HTTPError:
                        pass
                    time.sleep(1)
                else:
                    raise RuntimeError('Scratch services never became ready')
                print('Services ready; measuring real HTTP (frontend production build, session auth, restricted API database role)',flush=True)
                # Keep only sanitised aggregate metrics; never save fixture passwords or session tokens.
                if args.linux_client:
                    with tempfile.TemporaryDirectory(prefix='bp-load-client-') as client_temp:
                        fixture=Path(client_temp)/'fixture.json'
                        fixture.write_text(json.dumps(dict(backend=f'http://{backend_name}:8000',frontend=f'http://{frontend_name}:3000',origin=f'http://127.0.0.1:{frontend_port}',accounts=accounts,secret=secret,password=password)))
                        stages=[('bff_business_mixed',c) for c in (1,10,25,50,100,200,500,1000)]+[('bff_dashboard',c) for c in (1,10,50,100)]+[('api_login',c) for c in (1,10,50,100)]+[('bff_1000_paced',1000),('api_business_mixed',50),('bff_health_control',50)]
                        if args.comparison:
                            stages=[('bff_business_mixed',c) for c in (50,100,200)]+[('bff_dashboard',c) for c in (10,50)]+[('api_login',c) for c in (10,50)]+[('bff_1000_paced',1000)]
                            stages += [('ssr_dashboard',10),('ssr_1000_paced',1000)]
                        if args.diagnostic:
                            stages=[('bff_business_mixed',100),('bff_1000_paced',1000)]
                        if args.boundary:
                            stages=[('bff_business_mixed',c) for c in (60,75,90)]
                        results=[]
                        for name,c in stages:
                            # Restart after EVERY phase: timed-out BFF fetches can leave queued API work behind.
                            docker('restart',backend_name,frontend_name)
                            for _ in range(60):
                                try:
                                    if httpx.get(f'http://127.0.0.1:{frontend_port}/api/ready',timeout=4,trust_env=False).status_code==200:
                                        break
                                except httpx.HTTPError:
                                    pass
                                time.sleep(1)
                            else:
                                raise RuntimeError('Services not ready after phase restart')
                            samples=[]
                            stop=threading.Event()
                            def monitor():
                                while not stop.is_set():
                                    stats=docker('stats','--no-stream','--format','{{json .}}',backend_name,frontend_name,'business-platform-postgres-test-1',check=False)
                                    samples.extend(json.loads(s) for s in stats.splitlines() if s.startswith('{'))
                                    stop.wait(2)
                            observer=threading.Thread(target=monitor,daemon=True)
                            observer.start()
                            try:
                                phase_duration=max(60,args.duration) if name=='ssr_1000_paced' else max(30,args.duration) if name=='bff_1000_paced' else args.duration
                                run=subprocess.run(['docker','run','--rm','--network',network,'--mount',f'type=bind,source={client_temp},target=/probe','--mount',f'type=bind,source={Path(__file__).resolve()},target=/script/loadtest.py,readonly','bp-load-client:local','python','/script/loadtest.py','--client-fixture','/probe/fixture.json','--output','/probe/results.json','--duration',str(phase_duration),'--phase',name,'--concurrency',str(c)],check=False)
                                if run.returncode:
                                    raise RuntimeError('Linux generator failed')
                            finally:
                                stop.set()
                                observer.join(timeout=5)
                            result=json.loads((Path(client_temp)/'results.json').read_text())[0]
                            grouped=defaultdict(list)
                            for s in samples:
                                label='backend' if s['Name']==backend_name else 'frontend' if s['Name']==frontend_name else 'postgres'
                                grouped[label].append(float(s['CPUPerc'].strip('%')))
                            result['cpu_samples']={k:dict(mean_percent=round(sum(v)/len(v),1),max_percent=max(v),samples=len(v)) for k,v in grouped.items()}
                            result['memory_samples']={k:[s['MemUsage'].split(' / ')[0] for s in samples if ('backend' if s['Name']==backend_name else 'frontend' if s['Name']==frontend_name else 'postgres')==k] for k in grouped}
                            results.append(result)
                            args.output.write_text(json.dumps(dict(generator='Linux Docker',results=results),indent=2)+'\n',encoding='utf-8')
                else:
                    results=asyncio.run(suite(f'http://127.0.0.1:{backend_port}',f'http://127.0.0.1:{frontend_port}',accounts,secret,password,args.duration))
                logs=docker('logs',backend_name,check=False,include_stderr=True)
                errors=Counter()
                for line in logs.splitlines():
                    if 'QueuePool limit' in line: errors['pool_exhaustion']+=1
                    if 'connection slots' in line: errors['postgres_connections']+=1
                report=dict(date='2026-10-13',environment='Local Docker Desktop Linux; PostgreSQL 17 dedicated test server on tmpfs; no container CPU/RAM limits',generator='Linux Docker' if args.linux_client else 'Windows host',api_workers=args.workers,experimental_admission=args.admission,docker_resources=docker('info','--format','{{.NCPU}} CPUs {{.MemTotal}} bytes RAM'),dataset=dict(users=1000,organizations=20,customers=5000,items=2000,orders=5000,lines=15000),duration_s=args.duration,results=results,backend_error_counts=dict(errors))
                args.output.parent.mkdir(parents=True,exist_ok=True)
                args.output.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
                print('Aggregate report saved. Removing scratch services/database/roles.',flush=True)
                # Stop connections before disposable database and role context managers clean up.
                for kind,name in reversed(resources):
                    docker('rm','-f',name,check=False) if kind=='container' else docker('network','rm',name,check=False)
                resources.clear()
    finally:
        for kind,name in reversed(resources):
            docker('rm','-f',name,check=False) if kind=='container' else docker('network','rm',name,check=False)


if __name__=='__main__':
    main()
