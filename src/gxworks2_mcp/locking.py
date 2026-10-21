"""Reentrant per-project advisory locks for cooperating threads and processes.

All writers must participate. External editors and interrupted multi-file commits
remain outside this guarantee. The persistent lock file must never be unlinked
while a project may be in use. In particular, project deletion must keep this lock
inode: the lock resides in the parent directory rather than the deleted project.
"""
from contextlib import contextmanager
import os
import threading
import time

_guard = threading.Lock()
_locks = {}
_local = threading.local()


@contextmanager
def project_lock(project, timeout: float = 10.0):
    """Serialize a project's cooperative writers; same-thread nesting is safe."""
    root = project.root.resolve()
    key = os.path.normcase(str(root))
    with _guard:
        local_lock = _locks.setdefault(key, threading.RLock())
    if not local_lock.acquire(timeout=timeout):
        raise TimeoutError('Timed out acquiring project thread lock')
    depths = getattr(_local, 'depths', None)
    if depths is None:
        depths = _local.depths = {}
    try:
        if depths.get(key, 0):
            depths[key] += 1
            try:
                yield
            finally:
                depths[key] -= 1
            return
        # A sibling lock survives deletion/recreation of the project directory.
        root.parent.mkdir(parents=True, exist_ok=True)
        path = root.parent / ('.' + root.name + '.patch.lock')
        with path.open('a+b') as lock:
            if os.name == 'nt':
                import msvcrt
                lock.seek(0, os.SEEK_END)
                if lock.tell() == 0:
                    lock.write(b'\0')
                    lock.flush()
                def acquire():
                    lock.seek(0)
                    msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                def release():
                    lock.seek(0)
                    msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                def acquire():
                    fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                def release():
                    fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
            deadline = time.monotonic() + timeout
            while True:
                try:
                    acquire()
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError('Timed out acquiring project process lock')
                    time.sleep(0.02)
            depths[key] = 1
            try:
                yield
            finally:
                depths.pop(key)
                release()
    finally:
        local_lock.release()
