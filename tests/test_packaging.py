"""Execute the actual Compose healthcheck against real HTTP response statuses."""
import subprocess
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
import yaml


class PackagingTests(unittest.TestCase):
    def test_healthcheck_accepts_ok_or_auth_required_and_rejects_server_errors(self):
        path=Path(__file__).resolve().parents[1]/'docker-compose.yml'
        config=yaml.safe_load(path.read_text(encoding='utf-8'))
        code=config['services']['imadex']['healthcheck']['test'][-1]
        class Fixture(BaseHTTPRequestHandler):
            status=200
            def log_message(self,*args):pass
            def do_GET(self):self.send_response(self.status);self.end_headers()
        server=ThreadingHTTPServer(('127.0.0.1',0),Fixture)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        code=code.replace('http://127.0.0.1:8765/api/status',f'http://127.0.0.1:{server.server_port}/api/status')
        try:
            for status,healthy in ((200,True),(401,True),(404,False),(500,False),(503,False)):
                with self.subTest(status=status):
                    Fixture.status=status
                    result=subprocess.run([sys.executable,'-c',code],capture_output=True,timeout=10)
                    self.assertEqual(result.returncode==0,healthy)
        finally:server.shutdown();server.server_close();thread.join()


if __name__=='__main__':unittest.main()
