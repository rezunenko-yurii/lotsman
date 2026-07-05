# Lotsman — инструкции для агента

## Навигация по коду: используй lotsman (dogfood)

Этот проект — сам lotsman. Перед чтением файлов используй его же:

1. Незнакомая область → `python3 -m lotsman map --budget 1500 --mention <идентификатор>`
2. «Где код, отвечающий за X?» → `python3 -m lotsman search "X"` вместо серии grep
3. «Что в файле?» → `python3 -m lotsman outline <file>`, затем читай только нужные строки
4. «Кто использует / где определён?» → `python3 -m lotsman refs <name>` / `defs <name>`
5. Открытые в контексте файлы передавай через `--focus`
6. Файл целиком читается только после того, как outline/search подтвердил необходимость

MCP-вариант: сервер объявлен в `.mcp.json` (инструменты map/search/outline/defs/refs).

## Проект

- Манифест и алгоритмы: `docs/MANIFEST.md`; журнал разработки: `docs/PLAN.md`
- Тесты: `python3 -m unittest discover -s tests` (обязательны перед коммитом)
- Зависимости: tree-sitter + tree-sitter-language-pack; model2vec опционален —
  весь код должен деградировать без него (см. `embed.available()`)
- Смена семантики извлечения/схемы → bump `INDEX_VERSION` в `indexer.py`
- Изменение ранжирования → перезамер качества карты на Django
  (клон в scratchpad; эталон: в топе `cached_property`, `ValidationError`,
  `ForeignKey`, без generic-имён вроде `value`/`list`)
