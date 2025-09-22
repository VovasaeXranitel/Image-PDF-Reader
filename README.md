# Image PDF Reader (OCR)

Небольшой CLI-скрипт для распознавания текста (OCR) в PDF/изображениях и сохранения результатов в отдельную папку проекта.

Что делает:
- Принимает путь к входному файлу (PDF или изображение) через терминал.
- Создаёт папку `output` в корне проекта (по умолчанию) и сохраняет туда:
  - поисковый PDF c текстовым слоем: `<имя>__ocr.pdf`
  - извлечённый текст (sidecar): `<имя>__ocr.txt`
- Избегает перезаписи: при совпадении имён добавляет суффиксы `-1`, `-2`, ... (если не указать `--overwrite`).

## Требования
- Python 3.10+
- Системные зависимости:
  - Tesseract OCR (с языковыми данными для нужных языков, напр. `rus`)
  - Ghostscript (x64 на 64-битной Windows)
- Python-зависимости: см. `requirements.txt` (`ocrmypdf`, `pillow`).

## Установка (Windows, cmd.exe)
1) (Опционально) создать и активировать виртуальное окружение:
```bat
python -m venv .venv
".venv\Scripts\activate"
```

2) Установить Python-зависимости:
```bat
pip install -U pip
pip install -r requirements.txt
```

3) Установить системные зависимости:
- Tesseract OCR: скачайте инсталлятор (рекомендуется сборка UB Mannheim) и установите, убедившись, что установлен язык `rus`.
  - Страница: https://github.com/UB-Mannheim/tesseract/wiki
  - После установки типичный путь: `C:\Program Files\Tesseract-OCR\tesseract.exe`
- Ghostscript (x64): скачайте инсталлятор с официального сайта и установите.
  - Страница: https://ghostscript.com/releases/index.html
  - После установки типичный путь: `C:\Program Files\gs\<версия>\bin\gswin64c.exe`

(Опционально) через winget — сначала найдите доступные пакеты:
```bat
winget search tesseract
winget search ghostscript
```
Затем установите по найденному идентификатору, например:
```bat
winget install --id=UB-Mannheim.TesseractOCR -e
rem Пример для Ghostscript (идентификатор может отличаться):
winget install --id=Ghostscript.Ghostscript -e
```

4) Убедиться, что исполняемые файлы доступны:
- Либо добавьте их в PATH/переменные среды на время текущей сессии:
```bat
set "PATH=C:\Program Files\Tesseract-OCR;%PATH%"
set "OCRMYPDF_GS=C:\Program Files\gs\gs10.06.0\bin\gswin64c.exe"
```
- Либо передайте пути параметрами запуска (`--tesseract-dir`, `--gs-dir`).

Проверка версий:
```bat
tesseract --version
"C:\Program Files\gs\gs10.06.0\bin\gswin64c.exe" --version
```

Проверка установленных языков Tesseract:
```bat
tesseract --list-langs
```

(Для PowerShell эквивалент):
```powershell
$env:PATH = "C:\Program Files\Tesseract-OCR;" + $env:PATH
$env:OCRMYPDF_GS = "C:\Program Files\gs\gs10.06.0\bin\gswin64c.exe"
```

## Использование
Запуск из корня проекта (cmd.exe):
```bat
python main.py "C:\путь\к\файлу.pdf"
```
- По умолчанию результаты будут в папке проекта: `output`.

Частые опции:
- Задать папку вывода:
```bat
python main.py "C:\путь\к\файлу.pdf" -o "D:\OCR_Results"
```
- Указать языки Tesseract (например, русский+английский):
```bat
python main.py "C:\путь\к\файлу.pdf" -l rus+eng
```
- Разрешить перезапись существующих файлов:
```bat
python main.py "C:\путь\к\файлу.pdf" --overwrite
```
- Отключить прогресс-бар:
```bat
python main.py "C:\путь\к\файлу.pdf" --no-progress
```
- Явно указать каталоги Tesseract/Ghostscript, если они не в PATH:
```bat
python main.py "C:\путь\к\файлу.pdf" -l rus ^
  --tesseract-dir "C:\Program Files\Tesseract-OCR" ^
  --gs-dir "C:\Program Files\gs\gs10.06.0\bin"
```

Справка по всем параметрам:
```bat
python main.py --help
```

## Как улучшить качество OCR
- Повысить DPI перед распознаванием (часто критично для сканов низкого качества):
```bat
python main.py "C:\путь\к\файлу.pdf" -l rus --oversample 350
```
- Настроить режим сегментации страницы (PSM): 6 — «один блок текста», 11 — «разреженный текст». Попробуйте оба:
```bat
python main.py "C:\путь\к\файлу.pdf" -l rus --psm 6
python main.py "C:\путь\к\файлу.pdf" -l rus --psm 11
```
- Выбрать движок Tesseract OEM: 1 — LSTM-only (часто лучший результат):
```bat
python main.py "C:\путь\к\файлу.pdf" -l rus --oem 1
```
- Комбинировать языки, если есть английские буквы, номера, латиница: `-l rus+eng`.
- Сохранить пробелы как есть (иногда полезно для табличных форм): `--preserve-spaces`.
- Ограничить алфавит (уменьшить «белиберду») через whitelist, например:
```bat
python main.py "C:\путь\к\файлу.pdf" -l rus --psm 6 --oem 1 --oversample 350 ^
  --whitelist "АБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯ0-9.-,"
```

## Диагностика и устранение проблем
- Вывести диагностику путей к зависимостям (что именно найдено):
```bat
python main.py "C:\путь\к\файлу.pdf" --debug-deps
```
- Сообщение `[tesseract] lots of diacritics - possibly poor OCR`:
  - Повышайте `--oversample` (300–400), проверьте правильность языков (`-l rus`/`rus+eng`), попробуйте `--psm 6` или `--psm 11`, `--oem 1`.
- Предупреждения об «invalid jpeg data / image file is truncated»:
  - Встречаются в «битых» PDF/изображениях. В проекте включена устойчивость к усечённым JPEG. Держите `--optimize 0` (по умолчанию). Если требуется строгий PDF/A, попробуйте `--output-type pdfa` (может не сработать на повреждённых данных).
- Ошибка о `unpaper` на Windows:
  - Утилита часто недоступна; не используйте `--clean` или просто оставьте по умолчанию — скрипт сам повторит без очистки.
- Ghostscript/Tesseract не находятся:
  - Добавьте в PATH/установите `OCRMYPDF_GS` (см. выше) или используйте `--gs-dir`/`--tesseract-dir`.

## Примеры
- Базовый запуск с повышением DPI и улучшенной сегментацией:
```bat
python main.py "C:\Users\Vovas\Downloads\Путевой_Проезд.pdf" -l rus --oversample 350 --psm 6 --oem 1
```
- С явными путями к зависимостям:
```bat
python main.py "C:\Users\Vovas\Downloads\Путевой_Проезд.pdf" -l rus ^
  --tesseract-dir "C:\Program Files\Tesseract-OCR" ^
  --gs-dir "C:\Program Files\gs\gs10.06.0\bin"
```

## Выводы
- Результирующие файлы попадают в `output` (или папку, указанную `-o`).
- Имена не перезаписываются, если не указан `--overwrite`.
- Для проблемных сканов используйте комбинацию: `--oversample 300-400`, `--psm 6/11`, `--oem 1`, `-l rus+eng`, `--whitelist`.
