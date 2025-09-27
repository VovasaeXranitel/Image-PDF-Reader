# Image PDF Reader (Stateless OCR API)

Минимальный сервис распознавания текста из PDF: один маршрут `/ocr` принимает PDF (application/pdf) байтами и возвращает распознанный текст. Никаких файлов на диске не сохраняется. Конфигурация OCR жёстко зашита в коде и меняется только через правку `_OCR_CONFIG` в `app/interfaces/api/api.py`.

## Текущая жёсткая конфигурация
```
languages: rus+eng
psm: 6              # один блок текста
oem: 1              # LSTM
oversample: 350     # повышение DPI
optimize: 0         # без оптимизации (устойчивее к битым JPEG)
whitelist: русский алфавит + цифры + базовые знаки препинания
preserve_spaces: true
```

## Зависимости
На старте сервис выполняет авто-проверку наличия:
- Tesseract (и языков `rus`, `eng`)
- Ghostscript
Если что-то отсутствует — запрос к `/ocr` вернёт `503` с JSON-отчётом:
```json
{
  "detail": {
    "error": "OCR dependencies not ready",
    "dependencies": {
      "tesseract": { "found": false, ... },
      "ghostscript": { "found": true, ... },
      "languages_ok": false,
      "missing_languages": ["rus"]
    }
  }
}
```

## Установка локально (без Docker)
```bash
pip install -r requirements.txt
uvicorn run_api:app --reload --port 8000
```
Swagger UI: http://127.0.0.1:8000/swagger  
ReDoc:      http://127.0.0.1:8000/redoc

(Если Tesseract и Ghostscript не в PATH на Windows — добавьте их вручную.)

## Единственный маршрут
| Метод | Путь | Описание |
|-------|------|----------|
| POST  | /ocr | Принимает PDF (application/pdf) -> JSON с текстом |

### Пример запроса (curl)
```bash
curl -X POST http://127.0.0.1:8000/ocr \
  -H "Content-Type: application/pdf" \
  --data-binary "@C:/Users/Vovas/Downloads/Путевой_Проезд.pdf"
```
Ответ (пример):
```json
{
  "success": true,
  "text": "...распознанный текст...",
  "chars": 12345,
  "languages": "rus+eng"
}
```

### Обработка ошибок
| Код | Причина | Пример detail |
|-----|---------|---------------|
| 400 | Пустые или повреждённые данные | "Пустые или повреждённые данные PDF" |
| 503 | Отсутствуют зависимости | { error: "OCR dependencies not ready", dependencies: {...} } |

## Docker
### Сборка
```bash
docker build -t ocr-api:latest .
```
### Запуск
```bash
docker run --rm -p 8000:8000 ocr-api:latest
```
Проверка:
```bash
curl -X POST http://127.0.0.1:8000/ocr \
  -H "Content-Type: application/pdf" \
  --data-binary @sample.pdf | jq '.chars'
```

### Что внутри образа
Dockerfile устанавливает:
- `tesseract-ocr`, `tesseract-ocr-rus`, `tesseract-ocr-eng`
- `ghostscript`, `qpdf`, `pngquant` (для ocrmypdf)
- Python зависимости из `requirements.txt`
На этапе сборки выполняется ранняя проверка зависимостей — build упадёт, если что-то критичное отсутствует.

## Изменение конфигурации OCR
Редактируйте `_OCR_CONFIG` в `app/interfaces/api/api.py`. Пример (если нужно только русский):
```python
_OCR_CONFIG = OcrSimpleOptions(
    languages="rus",
    psm=6,
    oem=1,
    oversample=300,
    optimize=0,
    whitelist="АБВГДЕЁ...",  # ваш набор
    preserve_spaces=True,
)
```
После правки пересоберите образ или перезапустите сервис.

## Качество распознавания
- `oversample=350` подходит для слабых сканов (можно 300–400)
- `psm=6` — «один блок текста» (для форм/таблиц попробуйте 11 — при необходимости в коде)
- Жёсткий whitelist снижает «мусор» и латиницу
- Если появляются пропуски букв — проверьте исходник и DPI

## Обновление языков в Docker (пример)
```bash
docker exec -it <container> bash -c "apt-get update && apt-get install -y tesseract-ocr-eng tesseract-ocr-rus"
```
(Обычно не нужно — уже включены.)

## Ограничения и планы
- Нет поддержки изображений (PNG/JPG) напрямую — можно добавить конвертацию через `pdfimages`/Pillow
- Нет health-роута (статус виден по /swagger — если открывается, сервер жив)
- Нет стриминга прогресса (всё делается целиком)
- Нет аутентификации (можно повесить reverse proxy / OAuth / API ключ)

## Лицензия
Свободно для внутреннего и учебного использования. Добавьте ссылку на репозиторий при распространении.

## Project cleanup notes

This repository has had a cleanup operation to remove test files and unused artifacts. If you need to revert changes, check your VCS history.
