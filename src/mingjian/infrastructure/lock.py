"""Advisory single-process project lock, released by the OS on abnormal exit."""
import os

from mingjian.domain.models import MingjianError


class ProjectLock:
    def __init__(self, root):
        self.stream = (root / ".mingjian.lock").open("a+b")
        try:
            if os.name == "nt":
                import msvcrt
                self.stream.seek(0)
                self.stream.write(b"0")
                self.stream.flush()
                self.stream.seek(0)
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.stream.close()
            raise MingjianError("该项目已在另一个进程中打开，请先关闭其他窗口") from exc

    def close(self):
        if not self.stream.closed:
            self.stream.close()
