"""Планировщик: выполняет задания по расписанию.

    python3 scheduler.py                    # бесконечный цикл, Ctrl+C для выхода
    python3 scheduler.py --once             # один проход: то, что подошло по времени
    python3 scheduler.py --once --force     # выполнить все активные задания сейчас
    python3 scheduler.py --setup            # завести задания по умолчанию
    python3 scheduler.py --speed 3600       # ускорить время: час проходит за секунду

Режим --once вызывает GitHub Actions: там процесс живёт секунды, а не сутки.
"""

import sys
import time
from datetime import timedelta

import digest
import market
import notify
import store

# Задания по умолчанию: наблюдение каждый час, сводка каждые восемь часов.
DEFAULT_JOBS = [
    ("collect", "collect", 3600, {}),
    ("digest", "digest", 8 * 3600, {"period": "24h"}),
]


def run_job(job):
    """Выполняет одно задание. Возвращает строку результата."""
    kind = job["kind"]
    params = job["params"]

    if kind == "collect":
        samples = market.fetch(params.get("symbols"))
        store.append(samples)
        return f"наблюдений: {len(samples)}"

    if kind == "digest":
        summary = digest.build(params.get("period", "24h"))
        text = digest.as_text(summary)
        delivery = notify.send(text)
        return f"инструментов в сводке: {len(summary['items'])} · {delivery}"

    if kind == "remind":
        text = params.get("text", "напоминание")
        return notify.send(f"Напоминание: {text}")

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
            mark = "✓" if status == "ok" else "✕"
            print(f"  {mark} {job['name']:<12} {detail}", flush=True)

    return done


def setup(interval=None, digest_every=None):
    """Заводит задания по умолчанию. Интервалы можно переопределить (в секундах)."""
    store.init()
    for name, kind, every, params in DEFAULT_JOBS:
        if name == "collect" and interval:
            every = interval
        if name == "digest" and digest_every:
            every = digest_every
        # Первый запуск — сразу: иначе первое наблюдение ждать целый час.
        store.add_job(name, kind, every, params, first_run=store.now())
    return store.list_jobs()


def loop(speed=1.0, sleep_seconds=1.0):
    """Бесконечный цикл. speed ускоряет время для демонстрации."""
    print(f"планировщик запущен · ускорение ×{speed:g} · Ctrl+C для выхода", flush=True)
    try:
        while True:
            done = tick()
            if done:
                print(f"  — {store.iso(store.now())}", flush=True)
            time.sleep(sleep_seconds / max(speed, 0.001))
    except KeyboardInterrupt:
        print("\nостановлено", flush=True)


def main():
    args = sys.argv[1:]
    store.init()

    if "--setup" in args:
        jobs = setup()
        print("задания заведены:")
        for job in jobs:
            print(f"  {job['name']:<10} {job['kind']:<8} каждые {job['every']} c · "
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
            following = upcoming[0]["next_run"] if upcoming else "—"
            print(f"  ничего не подошло по времени · ближайший запуск {following}")
        return 0

    speed = 1.0
    if "--speed" in args:
        speed = float(args[args.index("--speed") + 1])
    if not store.list_jobs():
        setup()
    loop(speed=speed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
