"""Собирает оптимизированную модель qwen-constitution: Q3_K_M + параметры + system под задачу.

    python3 build_model.py        # пишет Modelfile и вызывает `ollama create`

Опции из пресета `prompt` (num_ctx 4096, temperature 0) зашиваются в модель, поэтому пресет
`quant` шлёт запросы без опций, а `ollama run qwen-constitution` работает с теми же настройками.
SYSTEM в Modelfile — общая роль. Конкретная инструкция (реранк или ответ с цитатами) приходит
в запросе: system-сообщение запроса заменяет SYSTEM модели.
"""

import pathlib
import subprocess
import sys

import presets

SYSTEM = ("Ты — справочник по действующей Конституции Российской Федерации (kremlin.ru, с поправками "
          "2020 года). Отвечай по-русски, только по фрагментам Конституции из сообщения пользователя, "
          "не по памяти. Если во фрагментах ответа нет — говори «не знаю».")

PARAMS = {"num_ctx": 4096, "temperature": 0, "num_predict": 400}


def modelfile():
    lines = [f"FROM {presets.Q3}", ""]
    lines += [f"PARAMETER {key} {value}" for key, value in PARAMS.items()]
    lines += ["", f'SYSTEM """{SYSTEM}"""', ""]
    return "\n".join(lines)


def main():
    path = pathlib.Path(__file__).with_name("Modelfile")
    path.write_text(modelfile(), encoding="utf-8")
    print(path.read_text(encoding="utf-8"))
    done = subprocess.run(["ollama", "create", presets.TUNED, "-f", str(path)])
    return done.returncode


if __name__ == "__main__":
    sys.exit(main())
