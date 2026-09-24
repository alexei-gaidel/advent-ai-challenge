"""Планировщик: запускает пайплайн по расписанию.

    python3 scheduler.py --setup            # завести задание: каждый час в :02
    python3 scheduler.py --once             # один проход того, что подошло по времени
    python3 scheduler.py --once --force     # выполнить активные задания сейчас
    python3 scheduler.py                    # бесконечный цикл, Ctrl+C для выхода

Режим --once вызывает GitHub Actions: там процесс живёт секунды, а не сутки.
"""

import sys
import time
from datetime import datetime, timedelta, timezone

import notify
import pipeline
import store

# Проверять каждый час в :02, как просил пользователь.
CHECK_MINUTE = 2
# После матча мониторить нечего — задание выключится само.
UNTIL = datetime(2026, 10, 4, tzinfo=timezone.utc)

DEFAULT_JOBS = [
    ("tickets", "pipeline", 3600, {"at_minute": CHECK_MINUTE, "notify": True}),
]


def next_aligned(minute=CHECK_MINUTE, moment=None):
    """Ближайший момент с нужной минутой часа."""
    moment = moment or store.now()
    candidate = moment.replace(minute=minute, second=0, microsecond=0)
    if candidate <= moment:
        candidate += timedelta(hours=1)
    return candidate


def run_job(job):
    """Выполняет одно задание. Возвращает строку результата."""
    kind = job["kind"]
    params = job.get("params") or {}

    if kind == "pipeline":
        if store.now() > UNTIL:
            store.cancel_job(job["name"])
            return "матч прошёл, задание выключено"
        result = pipeline.run(notify_on_change=bool(params.get("notify")), verbose=False)
        return result["detail"]

    if kind == "remind":
        return notify.send(f"Напоминание: {params.get('text', 'без текста')}")

    raise ValueError(f"неизвестный вид задания: {kind}")


def tick(force=False, verbose=True):
    """Один проход планировщика. Возвращает список выполненного."""
    jobs = store.list_jobs() if force else store.due_jobs()
    done = []

    for job in jobs:
        try:
            detail = run_job(job)
            status = "ok"
        except (RuntimeError, ValueError) as error:
            # Упавшее задание не должно останавливать остальные и весь планировщик.
            detail, status = str(error), "error"

        store.log_run(job["name"], job["kind"], status, detail)
        store.reschedule(job)
        done.append({"job": job["name"], "kind": job["kind"],
                     "status": status, "detail": detail})
        if verbose:
            print(f"  {'✓' if status == 'ok' else '✕'} {job['name']:<10} {detail}", flush=True)

    return done


def setup(every=3600, at_minute=CHECK_MINUTE, first_now=True):
    """Заводит задание проверки билетов."""
    store.init()
    for name, kind, period, params in DEFAULT_JOBS:
        params = {**params, "at_minute": at_minute}
        # Первый запуск сразу: иначе ждать до ближайшей нужной минуты.
        first = store.now() if first_now else next_aligned(at_minute)
        store.add_job(name, kind, every, params, first_run=first)
    return store.list_jobs()


def loop(sleep_seconds=5.0):
    print(f"планировщик запущен · проверка каждый час в :{CHECK_MINUTE:02d} · "
          f"Ctrl+C для выхода", flush=True)
    try:
        while True:
            tick()
            time.sleep(sleep_seconds)
    except KeyboardInterrupt:
        print("\nостановлено", flush=True)


def main():
    args = sys.argv[1:]
    store.init()

    if "--setup" in args:
        jobs = setup()
        print("задания заведены:")
        for job in jobs:
            print(f"  {job['name']:<10} {job['kind']:<9} каждые {job['every']} c "
                  f"(выравнивание на :{job['params'].get('at_minute', 0):02d}) · "
                  f"следующий запуск {job['next_run']}")
        return 0

    if "--once" in args:
        if not store.list_jobs():
            setup()
        force = "--force" in args
        print(f"проход планировщика{' (принудительный)' if force else ''}:")
        done = tick(force=force)
        if not done:
            upcoming = store.list_jobs()
            print(f"  ничего не подошло по времени · ближайший запуск "
                  f"{upcoming[0]['next_run'] if upcoming else '—'}")
        return 0

    if not store.list_jobs():
        setup()
    loop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
