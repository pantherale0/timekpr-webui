import signal
import time

from app import initialize_runtime, task_manager


def main():
    initialize_runtime(start_background_tasks=True)

    def _stop_worker(_signum, _frame):
        task_manager.stop()
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, _stop_worker)
    signal.signal(signal.SIGINT, _stop_worker)

    while True:
        time.sleep(60)


if __name__ == '__main__':
    main()
