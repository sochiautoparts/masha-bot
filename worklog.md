---
Task ID: 1
Agent: Super Z (main)
Task: Клонировать asya-bot, изучить все файлы, исправить дублирование постов

Work Log:
- Клонировал репозиторий sochiautoparts/asya-bot
- Детально изучил все ключевые файлы: content_engine.py, channel.py, database.py, config.py, main.py, news.py, partners.py, web_search.py, bot.yml
- Обнаружил 7 критических багов дедупликации
- Исправил все баги и запушил в main

Stage Summary:
- Topic Registry теперь загружается из БД при старте бота (раньше только при первом вызове get_best_news_item)
- Добавлена функция _extract_core_words() для извлечения identity-слов (бренд+модель+событие)
- Добавлен Check 6 (NORMALIZED CORE MATCH) в is_duplicate_post()
- Fingerprints в news.py больше НЕ сбрасываются каждый цикл
- Семантическая дедупликация усилена двухуровневой проверкой
- Добавлен Layer 1.5 — прямая проверка channel_posts с core-word matching
- Недавние заголовки постов загружаются из БД при старте
- Дневной лимит постов снижен: 96 → 48
- GitHub Actions перезапущен (Run #222)
