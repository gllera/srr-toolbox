"""srr-telegram holds an exclusive flock beside the session for the whole poll:
two concurrent account-mode polls (threads here, processes in production) never
overlap, and the lock path is derived from the session file.

Run in the repo's uv project venv (deps come from pyproject.toml):
    uv run tests/test_telegram_lock.py
"""
import asyncio
import importlib.machinery
import importlib.util
import os
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "..", "bin", "srr-telegram")

loader = importlib.machinery.SourceFileLoader("srr_telegram", SCRIPT)
spec = importlib.util.spec_from_loader("srr_telegram", loader)
mod = importlib.util.module_from_spec(spec)
loader.exec_module(mod)

failures = []


def check(name, got, want):
    ok = got == want
    print(("PASS" if ok else "FAIL"), name)
    if not ok:
        print("   want:", repr(want))
        print("   got :", repr(got))
        failures.append(name)


tmp = tempfile.mkdtemp()
sess = os.path.join(tmp, "tg.session")
with open(sess, "w") as fh:
    fh.write("x")
os.environ["SRR_TG_API_ID"] = "1"
os.environ["SRR_TG_API_HASH"] = "h"
os.environ["SRR_TG_SESSION_FILE"] = sess
os.environ.pop("SRR_TG_SESSION", None)
os.environ.pop("SRR_TG_LOCK_FILE", None)

check("lock path beside the session file", mod.lock_path(), sess + ".lock")
os.environ["SRR_TG_LOCK_FILE"] = os.path.join(tmp, "custom.lock")
check("explicit lock path wins", mod.lock_path(), os.path.join(tmp, "custom.lock"))
del os.environ["SRR_TG_LOCK_FILE"]

state = {"active": 0, "peak": 0, "polls": 0}
mu = threading.Lock()


class StubStringSession:
    def __init__(self, s):
        pass


class StubClient:
    """connect() .. disconnect() is the poll's network window."""

    def __init__(self, *a):
        pass

    async def connect(self):
        with mu:
            state["active"] += 1
            state["peak"] = max(state["peak"], state["active"])
            state["polls"] += 1
        await asyncio.sleep(0.3)

    async def disconnect(self):
        with mu:
            state["active"] -= 1

    async def is_user_authorized(self):
        return False   # ends the poll right after connect via SystemExit


mod.TelegramClient = StubClient
mod.StringSession = StubStringSession


def poll():
    try:
        asyncio.run(mod.run_ingest({"url": "https://t.me/x"}))
    except SystemExit:
        pass


ts = [threading.Thread(target=poll) for _ in range(3)]
for t in ts:
    t.start()
for t in ts:
    t.join()
check("all polls ran", state["polls"], 3)
check("polls serialize (never two clients at once)", state["peak"], 1)

# The lock is released on the error path: a later poll is not blocked.
t0 = time.time()
poll()
check("lock released after a failed poll", time.time() - t0 < 2, True)

print()
if failures:
    raise SystemExit("FAILED: %d test(s): %s" % (len(failures), ", ".join(failures)))
print("ALL PASSED")
