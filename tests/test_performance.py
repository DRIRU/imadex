import base64
import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import app
import performance as p


class Clock:
    def __init__(self):self.now = 0
    def __call__(self):return self.now
    def add(self, seconds):self.now += seconds


class PerformanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory();self.clock = Clock()
        self.r = p.Recorder(self.tmp.name,clock=self.clock)
    def tearDown(self):self.r.close();self.tmp.cleanup()
    def enable(self):self.r.action({'action':'start','seconds':10})

    def test_nested_exclusive_timing_and_privacy(self):
        self.enable()
        with self.r.job('embeddings',encoder='siglip2',path='SECRET'):
            with p.image(4):
                with p.span('source_open',url='SECRET',image_id=4):
                    self.clock.add(.1)
                    with p.span('drive_auth'):self.clock.add(.2)
                    self.clock.add(.1)
                with p.span('embedding',first_call=True):self.clock.add(.6)
                p.count('new_success');p.count('success')
        self.r.events.join()
        job = self.r.status()['jobs'][0];stages = {s['stage']:s for s in job['stages']}
        self.assertEqual(stages['source_open']['total_ms'],200)
        self.assertEqual(stages['drive_auth']['total_ms'],200)
        self.assertEqual(stages['image_total']['total_ms'],1000)
        self.assertEqual(job['uninstrumented_ms'],0);self.assertEqual(job['images_per_minute'],60)
        self.assertEqual(job['first_inference_ms'],600)
        self.assertNotIn('SECRET',(Path(self.tmp.name)/'performance/events.jsonl').read_text())

    def test_failure_category_preserves_deepest_stage(self):
        self.enable()
        with self.assertRaises(OSError),self.r.job('embeddings'),p.image(1):
            with p.span('source_read'):raise OSError('SECRET /private/image.jpg')
        self.r.events.join()
        self.assertEqual(self.r.status()['jobs'][0]['last_failure_stage'],'source_read')
        text=(Path(self.tmp.name)/'performance/events.jsonl').read_text()
        self.assertIn('"error_category":"io"',text);self.assertNotIn('SECRET',text)

    def test_lock_wait_is_separate_from_inference(self):
        self.enable()
        class Lock:
            def acquire(inner):self.clock.add(.2)
            def release(inner):pass
        with self.r.job('embeddings'),p.timed_lock(Lock(),'model_lock_wait'):
            with p.span('embedding'):self.clock.add(.4)
        stages={s['stage']:s for s in self.r.status()['jobs'][0]['stages']}
        self.assertEqual(stages['model_lock_wait']['total_ms'],200)
        self.assertEqual(stages['embedding']['total_ms'],400)

    def test_off_capture_expiry_and_partial_window(self):
        with self.r.job('embeddings'),p.span('decode'):self.clock.add(1)
        self.assertEqual(self.r.status()['jobs'],[])
        self.enable()
        with self.r.job('embeddings'):
            with p.span('decode'):self.clock.add(1)
            self.r.action({'action':'settings','enabled':False});self.clock.add(100)
        job=self.r.status()['jobs'][0]
        self.assertEqual(job['wall_ms'],1000);self.assertTrue(job['capture_partial'])
        self.enable();self.clock.add(11)
        with self.r.job('embeddings'),p.span('decode'):self.clock.add(.1)
        self.r.events.join();self.assertFalse(self.r.status()['capturing'])
        self.assertEqual(len(self.r.status()['jobs']),2)

    def test_mid_job_capture_and_clear_epoch(self):
        with self.r.job('embeddings'):
            self.clock.add(5);self.enable()
            with p.span('decode'):self.clock.add(1)
        self.assertEqual(self.r.status()['jobs'][0]['wall_ms'],1000)
        self.assertTrue(self.r.status()['jobs'][0]['capture_partial'])
        with self.r.job('embeddings'),p.span('decode'):
            self.r.action({'action':'clear','confirmed':True});self.clock.add(1)
        self.r.events.join();self.assertEqual(self.r.status()['jobs'],[])
        self.assertFalse((Path(self.tmp.name)/'performance/events.jsonl').exists())

    def test_bounded_queue_and_disk_failure_do_not_raise(self):
        with patch.object(self.r,'start_threads'):
            self.enable()
            for _ in range(520):self.r.emit({'type':'test'})
        self.assertEqual(self.r.events.qsize(),512);self.assertEqual(self.r.dropped,8)
        self.r.start_threads();self.r.events.join()
        with patch.object(Path,'open',side_effect=OSError('SECRET disk full')):
            self.r.emit({'type':'test'});self.r.events.join()
        self.assertEqual(self.r.write_errors,1)
        self.assertNotIn('SECRET',json.dumps(self.r.status()))

    def test_rotation_restart_and_bounded_percentiles(self):
        self.enable()
        with self.r.job('embeddings'):
            for i in range(600):
                with p.span('decode'):self.clock.add(.001)
        self.r.events.join()
        self.assertEqual(self.r.status()['jobs'][0]['stages'][0]['samples'],512)
        with patch.object(p,'MAX_FILE_BYTES',2048):
            for _ in range(100):self.r.emit({'type':'test','padding':'x'*200})
            self.r.events.join()
            paths=list((Path(self.tmp.name)/'performance').glob('*.jsonl'))
            self.assertLessEqual(len(paths),5);self.assertTrue(all(f.stat().st_size<=2048 for f in paths))
        with self.r.job('embeddings'),p.span('decode'):self.clock.add(.1)
        self.r.events.join()
        restarted=p.Recorder(self.tmp.name)
        try:self.assertEqual(restarted.status()['jobs'][0]['kind'],'embeddings')
        finally:restarted.close()

    def test_validation_and_authenticated_endpoints(self):
        for payload in ({'action':'start','seconds':1801},{'action':'start','seconds':True},{'action':'settings','enabled':'true'},{'action':'clear'}):
            with self.assertRaises(ValueError):self.r.action(payload)
        server=ThreadingHTTPServer(('127.0.0.1',0),app.Handler);server.access_password='test-password';server.public_origin=None
        worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
        origin='http://127.0.0.1:'+str(server.server_port)
        auth='Basic '+base64.b64encode(b'frame:test-password').decode()
        with patch.object(app,'semantic_index',SimpleNamespace(performance=self.r)):
            try:
                for endpoint in ('/api/performance','/api/performance/export'):
                    with self.assertRaises(HTTPError) as e:urlopen(origin+endpoint)
                    self.assertEqual(e.exception.code,401);e.exception.close()
                    with urlopen(Request(origin+endpoint,headers={'Authorization':auth})) as response:
                        self.assertEqual(json.load(response)['version'],1);self.assertEqual(response.headers['Cache-Control'],'no-store')
                headers={'Authorization':auth,'Origin':origin,'Content-Type':'application/json'}
                with urlopen(Request(origin+'/api/performance',data=b'{"action":"start","seconds":10}',headers=headers)) as response:self.assertTrue(json.load(response)['ok'])
                self.assertTrue(self.r.status()['capturing'])
            finally:server.shutdown();server.server_close();worker.join()

    def test_resource_sampler_timeout_and_invalid_metrics_are_nonfatal(self):
        self.r.enabled=True;self.r.resources=True;self.r.detail_until=10
        class Stop:
            calls=0
            def wait(inner,seconds):
                inner.calls+=1;return inner.calls>1
        original=self.r.stop;self.r.stop=Stop()
        try:
            with patch.object(p.shutil,'which',return_value='nvidia-smi'),patch.object(p.subprocess,'run',side_effect=p.subprocess.TimeoutExpired('nvidia-smi',1)) as run:
                self.r.sample_loop()
                self.assertEqual(run.call_args.kwargs['timeout'],1)
            self.assertEqual(self.r.status()['resource_state']['gpu_status'],'unavailable')
            self.assertEqual(len(self.r.status()['resource_state']['samples']),1)
        finally:self.r.stop=original


if __name__ == '__main__':unittest.main()
