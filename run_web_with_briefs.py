"""Opt-in Railway start command; separate child processes, never Gunicorn threads."""
import os
import signal
import subprocess
import sys
import time


def supervise(web_command, worker_command, *, popen=subprocess.Popen, sleep=time.sleep, clock=time.monotonic):
    stopping=False
    def stop(signum, frame):
        nonlocal stopping
        stopping=True
    handlers={s:signal.signal(s,stop) for s in (signal.SIGTERM,signal.SIGINT)}
    web=worker=None
    def start_worker():
        try:
            return popen(worker_command)
        except Exception:
            print('Brief worker could not start; web service remains available.',flush=True)
            return None
    try:
        web=popen(web_command)
        worker=start_worker() if worker_command else None
        retry_at=clock()+60
        while not stopping and web.poll() is None:
            if worker_command and (worker is None or worker.poll() is not None):
                if clock()>=retry_at:
                    print('Brief worker exited; restarting independently of web traffic.',flush=True)
                    worker=start_worker()
                    retry_at=clock()+60
            sleep(1)
        return web.returncode if web.returncode is not None else 0
    finally:
        for child in (worker,web):
            if child is not None and child.poll() is None:
                child.terminate()
        for child in (worker,web):
            if child is not None:
                try: child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    child.kill(); child.wait(timeout=5)
        for s,h in handlers.items(): signal.signal(s,h)


def main():
    port=int(os.environ.get('PORT','8080'))
    web=[sys.executable,'-m','gunicorn','-w','2','--threads','8','--timeout','60',
         '-b',f'[::]:{port}','pickem_flask_htmx_tabs:app']
    worker=[sys.executable,'shared_brief_worker.py'] if os.environ.get('SHARED_BRIEF_AUTOMATION_ENABLED')=='1' else None
    return supervise(web,worker)


if __name__=='__main__':
    raise SystemExit(main())
