# masha-bot 🏎️

BMW-focused Telegram bot for [@bmw_mpower_club](https://t.me/bmw_mpower_club) channel.

Маша — владелица BMW M5 F90 Competition (625 л.с., S63), бывший юрист, ставшая автомобильным экспертом и главредом канала.

## Features

- **AI-Powered Content**: Uses Pollinations.ai with dual-key failover for text and image generation
- **BMW Knowledge Base**: Three-level knowledge system (models, technical, culture)
- **6 Editorial Characters**: Маша (главред), Серёга (механик), Костя (кодер), Лена (дизайнер), Доктор Ван Дамм (кот), Кинг Конг (попугай)
- **Fact Checking**: BMW-specific validation against known AI hallucinations
- **Smart Scheduling**: Theme days (M-Monday, Tech Tuesday, etc.) + urgent news priority
- **Evergreen Buffer**: Pre-made content for when no fresh news is available
- **Partner Integration**: Admitad partner system (Rossko, Autopiter, AvtoALL)
- **Semantic Dedup**: Prevents duplicate or very similar posts

## Architecture

```
masha-bot/
├── ai/                     # AI provider (Pollinations)
│   ├── router.py           # AI routing and content generation
│   └── providers/
│       ├── base.py         # Base AI provider interface
│       └── pollinations_provider.py  # Pollinations with dual-key failover
├── bot/
│   ├── core/               # Core modules
│   │   ├── config.py       # Environment config
│   │   ├── pipeline.py     # Content orchestration
│   │   └── scheduler.py    # Theme days and scheduling
│   ├── sources/            # Content sources
│   │   ├── rss_fetcher.py  # BMW RSS + web search
│   │   ├── evergreen.py    # Pre-made content buffer
│   │   └── community.py    # Subscriber questions and polls
│   ├── generation/         # Content generation
│   │   ├── writer.py       # AI text generation
│   │   ├── fact_checker.py # BMW fact validation
│   │   ├── persona.py      # Character/tone/mood management
│   │   └── image_gen.py    # AI image generation
│   ├── publishing/         # Channel posting
│   │   ├── telegram.py     # Telegram API + dedup
│   │   └── formatter.py    # Character limit handling
│   ├── analytics/          # Metrics
│   │   ├── tracker.py      # Post tracking
│   │   └── reporter.py     # Weekly reports
│   ├── knowledge/          # BMW knowledge
│   │   ├── bmw_base.py     # Model range, engines, tech, culture
│   │   ├── characters.py   # Editorial team
│   │   └── topics.py       # Topic scheduling
│   ├── data/               # Static data
│   │   ├── evergreen_pool.json
│   │   ├── topic_schedule.json
│   │   └── persona_state.json
│   ├── database.py         # SQLite with aiosqlite
│   ├── partners.py         # Admitad partner integration
│   └── main.py             # Entry point
├── news.py                 # BMW RSS fetching
├── .github/workflows/bot.yml  # GitHub Actions CI/CD
├── .env.example            # Environment template
├── requirements.txt        # Python dependencies
└── README.md
```

## Setup

1. Clone the repository:
```bash
git clone https://github.com/sochiautoparts/masha-bot.git
cd masha-bot
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```

3. Copy `.env.example` to `.env` and fill in your credentials:
```bash
cp .env.example .env
```

4. Run the bot:
```bash
# Single cycle (for GitHub Actions)
MASHA_BOT_MODE=single python -m bot.main

# Interactive mode (for development)
MASHA_BOT_MODE=interactive python -m bot.main
```

## Content Types

| Type | Distribution | Description |
|------|-------------|-------------|
| news+reaction | 40% | BMW news with Маша's expert opinion |
| DIY/how-to | 15% | BMW maintenance, coding, VANOS |
| polls/debates | 15% | M3 vs M4, xDrive vs RWD, etc. |
| lore/history | 10% | E30 M3, M1 Procar, CSL lineage |
| garage stories | 10% | Stories from Серёга's shop |
| partner | 10% | Rossko, Autopiter, AvtoALL |

## Theme Days

| Day | Theme | Focus |
|-----|-------|-------|
| Monday | 🔥 M-Monday | M-models, M-division |
| Tuesday | 🔧 Tech Tuesday | Engines, VANOS, coding |
| Wednesday | 🔩 Workshop Wednesday | DIY, Серёга's tips |
| Thursday | ⏪ Throwback Thursday | Classic BMW, history |
| Friday | 🤪 Freaky Friday | Tuning, Alpina, custom |
| Saturday | 🔦 Spotlight Saturday | Model deep-dive |
| Sunday | 🛣️ Sunday Drive | Nürburgring, road trips |

## Characters

| Character | Role | BMW Connection |
|-----------|------|---------------|
| Маша | Главред | M5 F90 Competition owner |
| Серёга | Механик-BMWист | 20 years in BMW service |
| Костя | Кодер-энджинист | BimmerCode enthusiast |
| Лена | Дизайнер | Individual colors expert |
| Доктор Ван Дамм | Кот редакции | Sleeps on M5 hood |
| Кинг Конг | Попугай | Screams "///M-Power!" |

## GitHub Actions

The bot runs automatically every 30 minutes via GitHub Actions. The workflow:
1. Checks out the repo
2. Restores the database cache
3. Runs one content cycle
4. Commits any database changes
5. Self-dispatches the next run

## License

Proprietary — @bmw_mpower_club
