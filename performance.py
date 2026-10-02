"""Bounded opt-in diagnostics. Only allowlisted numeric/categorical data is retained."""
import argparse
import contextvars
import json
import math
import os
import queue
import shutil
import subprocess
import threading
import time
import uuid
from collections import deque
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

VERSION = 1
MAX_FILE_BYTES = 10 * 1024 * 1024
FILE_COUNT = 5
STAGES = {'queue_wait', 'catalog', 'cleanup', 'vector_lookup', 'duplicate_lookup', 'duplicate_vector',
          'drive_auth', 'source_open', 'source_read', 'checksum', 'decode', 'model_lock_wait',
          'image_model_load', 'text_model_load', 'embedding', 'text_embedding', 'normalization',
          'revision_check', 'vector_lock_wait', 'vector_upsert', 'sqlite_commit', 'image_total'}
COUNTS = {'visited', 'skipped', 'failed_skipped', 'inferred', 'reused', 'success', 'new_success', 'failed', 'stale'}
_job = contextvars.ContextVar('performance_job', default=None)
_stack = contextvars.ContextVar('performance_stack', default=())
_image = contextvars.ContextVar('performance_image', default=None)
_image_meta = contextvars.ContextVar('performance_image_meta', default=None)


def migrate(conn):
    conn.executescript('''CREATE TABLE IF NOT EXISTS performance_settings(
        id INTEGER PRIMARY KEY CHECK(id=1),enabled INTEGER NOT NULL DEFAULT 0,
        resources INTEGER NOT NULL DEFAULT 0);
        INSERT OR IGNORE INTO performance_settings(id) VALUES(1);''')


def category(error):
    if isinstance(error, TimeoutError) or isinstance(error, subprocess.TimeoutExpired):return 'timeout'
    if isinstance(error, OSError):return 'io'
    if isinstance(error, ValueError):return 'validation'
    return 'operation_failed'


def info(values):
    """Never accept arbitrary strings, filenames, URLs, error messages or content."""
    result = {}
    for key, allowed in {'encoder':{'siglip2','clip'}, 'backend':{'Qdrant local','Qdrant server'},
                         'source':{'local','drive'}, 'runtime':{'auto','cpu','cuda','gpu','none'},
                         'kind':{'embeddings','model_prepare','search'}}.items():
        if isinstance(values.get(key),str) and values[key] in allowed:result[key] = values[key]
    for key in ('image_id','bytes','pixels','width','height','batch_size'):
        value = values.get(key)
        if isinstance(value,int) and not isinstance(value,bool) and value >= 0:result[key] = value
    for key in ('image_providers','text_providers'):
        if isinstance(values.get(key), (list,tuple)):
            result[key] = [p for p in values[key] if isinstance(p,str) and p in {'CUDAExecutionProvider','CPUExecutionProvider','AzureExecutionProvider','TensorrtExecutionProvider'}]
    for key in ('ocr_enabled','recognition_enabled','detection_enabled','scan_running','ocr_running','recognition_running','detection_running'):
        if isinstance(values.get(key),bool):result[key] = values[key]
    return result


def read_jobs(data):
    """Read only our five bounded files at startup/CLI time, never on dashboard polling."""
    records = deque(maxlen=20)
    root = Path(data)/'performance'
    for index in reversed(range(FILE_COUNT)):
        path = root/('events.jsonl' if index == 0 else f'events.{index}.jsonl')
        try:
            if path.stat().st_size > MAX_FILE_BYTES:continue
            with path.open(encoding='utf-8') as stream:
                for line in stream:
                    if len(line) > 64000:continue
                    try:
                        event = json.loads(line)
                        if isinstance(event,dict) and event.get('version') == VERSION and event.get('type') == 'job' and isinstance(event.get('job'),dict):records.append(event['job'])
                    except (ValueError,KeyError,TypeError):continue
        except OSError:continue
    return list(records)


class Job:
    def __init__(self, recorder, kind, job_id, metadata):
        self.recorder = recorder;self.kind = kind;self.job_id = job_id
        self.metadata = info(metadata);self.start = recorder.clock();self.capture_start = None
        self.id = uuid.uuid4().hex;self.stages = {};self.counts = dict.fromkeys(COUNTS,0)
        self.outcome = 'running';self.first_inference_ms = None;self.last_failure_stage = None
        self.epoch = recorder.epoch;self.partial = not recorder.enabled
        self.capture_end = None
        self.queue_wait_ms = 0
        self.last_error = None;self.last_http_status = None

    def activate(self):
        recorder = self.recorder
        if not recorder.enabled or recorder.closed or self.capture_end is not None or self.epoch != recorder.epoch:return False
        if self.capture_start is None:
            with recorder.lock:
                if len(recorder.active)>=20:return False
                self.capture_start = recorder.clock();recorder.active[self.id] = self
        return True

    def count(self, key):
        if key in COUNTS and self.activate():
            with self.recorder.lock:self.counts[key] += 1

    def snapshot(self):
        elapsed = max(0,(self.capture_end if self.capture_end is not None else self.recorder.clock())-(self.capture_start if self.capture_start is not None else self.start))*1000
        stages = []
        for name, stat in self.stages.items():
            samples = sorted(stat['samples']);n = len(samples)
            stages.append({'stage':name,'total_ms':round(stat['total'],3),'count':stat['count'],
                'p50_ms':round(samples[math.ceil(n*.5)-1],3) if n else None,
                'p95_ms':round(samples[math.ceil(n*.95)-1],3) if n else None,
                'samples':n,'percentiles':'last_512','max_ms':round(stat['max'],3)})
        exclusive = sum(s['total_ms'] for s in stages if s['stage'] != 'image_total')
        stages.sort(key=lambda s:s['total_ms'],reverse=True)
        for stage in stages:stage['share_percent'] = round(stage['total_ms']/max(elapsed,.001)*100,1) if stage['stage'] != 'image_total' else None
        measured = [s for s in stages if s['stage'] not in {'image_total','queue_wait'}]
        return {'id':self.id,'job_id':self.job_id,'kind':self.kind,'run_id':self.recorder.run_id,
            'started_utc':self.started_utc,'outcome':self.outcome,'capture_partial':self.partial,
            'wall_ms':round(elapsed,3),'uninstrumented_ms':round(max(0,elapsed-exclusive),3),
            'queue_wait_ms':round(self.queue_wait_ms,3),
            'counts':dict(self.counts),'images_per_minute':round(self.counts['new_success']*60000/max(elapsed,1),2),
            'metadata':dict(self.metadata),'stages':stages,'largest_stage':measured[0]['stage'] if measured else None,
            'first_inference_ms':self.first_inference_ms,'last_failure_stage':self.last_failure_stage,
            'last_http_status':self.last_http_status}


class Recorder:
    def __init__(self, data, db=None, clock=time.perf_counter, queue_size=512):
        self.data = Path(data);self.db = db;self.clock = clock
        self.lock = threading.RLock();self.file_lock = threading.Lock()
        self.enabled = False;self.resources = False;self.detail_until = 0
        self.epoch = 0;self.closed = False;self.run_id = uuid.uuid4().hex
        self.active = {};self.recent = deque(read_jobs(data),maxlen=20)
        self.events = queue.Queue(maxsize=queue_size);self.writer = None;self.sampler = None
        self.stop = threading.Event();self.dropped = 0;self.write_errors = 0;self.last_write_error = None
        self.resource_state = {'gpu_status':'not_requested','process_status':'not_requested','samples':[]}
        self.activity = lambda: {}
        if db:
            with db() as conn:
                migrate(conn)
                row = conn.execute('SELECT * FROM performance_settings WHERE id=1').fetchone()
                self.enabled = bool(row['enabled']);self.resources = bool(row['resources'])

    def action(self, payload):
        action = payload.get('action')
        if action == 'settings':
            enabled = payload.get('enabled');resources = payload.get('resources',self.resources)
            if not isinstance(enabled,bool) or not isinstance(resources,bool):raise ValueError('Diagnostics settings must be true or false.')
            with self.lock:
                self.enabled = enabled;self.resources = resources
                if not enabled:
                    self.detail_until = 0
                    for job in self.active.values():
                        job.capture_end = self.clock();job.partial = True
        elif action == 'start':
            seconds = payload.get('seconds',600);resources = payload.get('resources',False)
            if not isinstance(seconds,int) or isinstance(seconds,bool) or not 10 <= seconds <= 1800:raise ValueError('Capture duration must be 10–1800 seconds.')
            if not isinstance(resources,bool):raise ValueError('Resource sampling must be true or false.')
            with self.lock:
                self.enabled = True;self.resources = resources;self.detail_until = self.clock()+seconds
        elif action == 'stop':
            with self.lock:self.detail_until = 0
        elif action == 'clear':
            if payload.get('confirmed') is not True:raise ValueError('Confirm clearing diagnostics.')
            with self.file_lock, self.lock:
                self.enabled = False;self.detail_until = 0;self.epoch += 1;self.active.clear();self.recent.clear()
                self.resource_state = {'gpu_status':'not_requested','process_status':'not_requested','samples':[]}
                for index in range(FILE_COUNT):
                    path = self.data/'performance'/('events.jsonl' if index == 0 else f'events.{index}.jsonl')
                    try:path.unlink(missing_ok=True)
                    except OSError:self.write_errors += 1;self.last_write_error = 'io'
        else:raise ValueError('Unknown performance action.')
        if self.db:
            with self.db() as conn:conn.execute('UPDATE performance_settings SET enabled=?,resources=? WHERE id=1',(int(self.enabled),int(self.resources)))
        if self.enabled:self.start_threads()
        return {'ok':True}

    def start_threads(self):
        with self.lock:
            if self.writer is None:
                self.writer = threading.Thread(target=self.write_loop,daemon=True,name='performance-writer');self.writer.start()
            if self.resources and self.sampler is None:
                self.sampler = threading.Thread(target=self.sample_loop,daemon=True,name='performance-resources');self.sampler.start()

    def emit(self, event, detail=False, epoch=None):
        if self.closed or detail and self.clock() >= self.detail_until:return
        event = dict(event,version=VERSION,utc=datetime.now(timezone.utc).isoformat())
        try:self.events.put_nowait((self.epoch if epoch is None else epoch,event))
        except queue.Full:self.dropped += 1

    def write_loop(self):
        while not self.stop.is_set() or not self.events.empty():
            try:epoch,event = self.events.get(timeout=.2)
            except queue.Empty:continue
            try:
                encoded = json.dumps(event,separators=(',',':'),allow_nan=False)+'\n'
                with self.file_lock:
                    if epoch != self.epoch:continue
                    root = self.data/'performance';root.mkdir(parents=True,exist_ok=True)
                    path = root/'events.jsonl'
                    if path.exists() and path.stat().st_size+len(encoded.encode('utf-8')) > MAX_FILE_BYTES:
                        (root/f'events.{FILE_COUNT-1}.jsonl').unlink(missing_ok=True)
                        for index in reversed(range(1,FILE_COUNT-1)):
                            old = root/f'events.{index}.jsonl'
                            if old.exists():old.replace(root/f'events.{index+1}.jsonl')
                        path.replace(root/'events.1.jsonl')
                    with path.open('a',encoding='utf-8') as stream:stream.write(encoded)
            except (OSError,ValueError,TypeError):
                self.write_errors += 1;self.last_write_error = 'log_write_failed'
            finally:self.events.task_done()

    @contextmanager
    def job(self, kind, job_id=None, **metadata):
        job = Job(self,kind,job_id,metadata);job.started_utc = datetime.now(timezone.utc).isoformat()
        token = _job.set(job);stack_token = _stack.set(())
        if self.enabled:self.start_threads();job.activate()
        try:yield job
        except BaseException:
            job.outcome = 'failed';raise
        finally:
            if job.outcome == 'running':job.outcome = 'complete'
            with self.lock:
                if job.capture_start is not None and job.epoch == self.epoch:
                    snapshot = job.snapshot();self.recent.append(snapshot);self.emit({'type':'job','job':snapshot})
                self.active.pop(job.id,None)
            _stack.reset(stack_token);_job.reset(token)

    def status(self):
        with self.lock:
            return {'version':VERSION,'enabled':self.enabled,'resources':self.resources,
                'capturing':self.enabled and self.clock() < self.detail_until,
                'remaining_seconds':max(0,math.ceil(self.detail_until-self.clock())),
                'active':[job.snapshot() for job in self.active.values()], 'jobs':list(reversed(self.recent)),
                'dropped_events':self.dropped,'write_errors':self.write_errors,'write_error':self.last_write_error,
                'resource_state':{**self.resource_state,'samples':list(self.resource_state['samples'])},'retention_bytes':MAX_FILE_BYTES*FILE_COUNT,
                'note':'Embedding wall time includes preprocessing/transfers; GPU samples cover the whole GPU. Percentiles use the last 512 observations per stage.'}

    def sample_loop(self):
        cpu_previous = None
        while not self.stop.wait(2):
            if not (self.enabled and self.resources and self.clock() < self.detail_until):
                cpu_previous = None;continue
            epoch = self.epoch
            sample = {'elapsed_seconds':round(self.clock(),3)}
            try:sample.update(info(self.activity()))
            except Exception:pass
            command = shutil.which('nvidia-smi')
            if command:
                try:
                    result = subprocess.run([command,'--query-gpu=index,utilization.gpu,memory.used,memory.total','--format=csv,noheader,nounits'],
                        capture_output=True,text=True,timeout=1,creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
                    rows = [list(map(float,line.split(','))) for line in result.stdout.strip().splitlines()]
                    if result.returncode or not rows or any(len(row)!=4 or not all(math.isfinite(n) for n in row) for row in rows):raise ValueError()
                    sample['gpus'] = [{'index':int(row[0]),'utilization_percent':row[1],'memory_mib':row[2],'total_mib':row[3]} for row in rows[:8]]
                    self.resource_state['gpu_status'] = 'available'
                except (OSError,ValueError,subprocess.TimeoutExpired):self.resource_state['gpu_status'] = 'unavailable'
            else:self.resource_state['gpu_status'] = 'unavailable'
            try:
                import psutil
                process = psutil.Process();cpu = sum(process.cpu_times()[:2]);now = self.clock()
                sample['rss_bytes'] = process.memory_info().rss
                if cpu_previous:sample['cpu_percent'] = round((cpu-cpu_previous[0])/max(now-cpu_previous[1],.001)*100,1)
                cpu_previous = cpu,now;self.resource_state['process_status'] = 'available'
            except Exception:self.resource_state['process_status'] = 'unavailable'
            with self.lock:
                if epoch!=self.epoch or not (self.enabled and self.resources and self.clock()<self.detail_until):continue
                samples = self.resource_state['samples'];samples.append(sample)
                if len(samples)>120:del samples[:-120]
            self.emit({'type':'resources','sample':sample},detail=True,epoch=epoch)

    def close(self):
        self.closed = True;self.stop.set()
        if self.writer:self.writer.join(timeout=3)
        if self.sampler:self.sampler.join(timeout=3)


@contextmanager
def span(stage, **metadata):
    job = _job.get()
    if job is None or stage not in STAGES or not job.activate():yield;return
    recorder = job.recorder;start = recorder.clock();frame = [0.0];parents = _stack.get()
    token = _stack.set((*parents,frame));outcome = 'ok';error_category = None
    try:yield
    except BaseException as error:
        outcome = 'failed';error_category = category(error)
        if job.last_error is not error:
            job.last_failure_stage = stage;job.last_error = error
        code = getattr(error,'code',None)
        if isinstance(code,int) and not isinstance(code,bool) and 100<=code<=599:
            job.last_http_status = code;metadata['http_status'] = code
        raise
    finally:
        elapsed = max(0,recorder.clock()-start)*1000
        if parents:parents[-1][0] += elapsed
        exclusive = elapsed if stage == 'image_total' else max(0,elapsed-frame[0])
        _stack.reset(token)
        if recorder.enabled and job.epoch == recorder.epoch:
            with recorder.lock:
                stat = job.stages.setdefault(stage,{'count':0,'total':0,'max':0,'samples':deque(maxlen=512)})
                stat['count'] += 1;stat['total'] += exclusive;stat['max'] = max(stat['max'],exclusive);stat['samples'].append(exclusive)
                job.metadata.update(info(metadata))
                if metadata.get('first_call') is True:job.first_inference_ms = round(elapsed,3)
            event = {'type':'span','job':job.id,'stage':stage,'elapsed_ms':round(elapsed,3),
                     'exclusive_ms':round(exclusive,3),'outcome':outcome,'metadata':info({**(_image_meta.get() or {}),**metadata})}
            if 'http_status' in metadata:event['http_status'] = metadata['http_status']
            if isinstance(metadata.get('first_call'),bool):event['first_call'] = metadata['first_call']
            if isinstance(_image.get(),int) and not isinstance(_image.get(),bool):event['image_id'] = _image.get()
            if error_category:event['error_category'] = error_category
            recorder.emit(event,detail=True,epoch=job.epoch)


@contextmanager
def image(image_id):
    token = _image.set(image_id)
    meta_token = _image_meta.set({})
    try:
        with span('image_total'):yield
    finally:_image.reset(token);_image_meta.reset(meta_token)


@contextmanager
def timed_lock(lock, stage):
    with span(stage):lock.acquire()
    try:yield
    finally:lock.release()


def count(key):
    job = _job.get()
    if job:job.count(key)


def metadata(**values):
    job = _job.get()
    if job and job.activate():
        with job.recorder.lock:
            clean = info(values);job.metadata.update(clean)
            if _image_meta.get() is not None:_image_meta.get().update(clean)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Show the last bounded performance summaries.')
    parser.add_argument('--data',type=Path,default=Path(__file__).parent/'data')
    args = parser.parse_args();print(json.dumps({'version':VERSION,'jobs':read_jobs(args.data)},indent=2))
