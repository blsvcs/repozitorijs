# Latvijas tiesu nolēmumu pilots

Rīks anonimizēto Latvijas tiesu nolēmumu izgūšanai, saglabāšanai, pilnteksta meklēšanai un sākotnējai analītikai.

Projekta pašreizējais fokuss ir praktisks pilots: lietotājs var meklēt nolēmumus, filtrēt rezultātus, skatīt fragmentus, lietas detaļas, tēmas un sagatavot sākotnēju pārskatu.

## Kas ir iekšā

- `streamlit_app.py` - galvenā pilota lietotne pārlūkā.
- `pilot/pilot.sqlite` - lokāla SQLite pilotdatubāze.
- `search_pilot.py` - ātra komandrindas meklēšana pilotdatubāzē.
- `sync_anon_nolemumi.py` - DAGR CSV metadatu lejupielāde un arhivēšana.
- `scripts/build_pilot_sqlite.py` - pilotdatubāzes būvēšana no CSV un PDF tekstiem.
- `scripts/classify_topics.py` - lokāla, noteikumos balstīta lietu tēmu klasifikācija.
- `scripts/generate_ai_summaries.py` - AI kopsavilkumu ģenerēšana, ja pieejams GitHub Models/OpenAI savienojums.
- `app/` un `db/` - FastAPI + PostgreSQL/pgvector virziens lielākai sistēmai.

## Pašreizējais datu stāvoklis

2026-06-08 lokālajā pilotdatubāzē ir:

- 10 000 nolēmumu metadatu ieraksti.
- 1 500 lejupielādēti dokumenti ar izvilktu tekstu.
- 1 500 pilnteksta meklēšanas ieraksti.
- 1 500 dokumentiem ir pievienota lokāla tēmu klasifikācija.
- AI kopsavilkumu un semantiskā indeksa tabulas ir papildināmas atsevišķos soļos.

Detalizētāks audits: `docs/DATA_AUDIT.md`.
Demo scenāriji: `docs/DEMO_SCENARIOS.md`.
AI, semantika un publicēšana: `docs/AI_SEMANTIC_DEPLOYMENT.md`.

## Ātrā palaišana

### Meklēšana komandrindā

```bash
python search_pilot.py "kredīta parāds" --limit 5
```

### Streamlit pilots

```bash
pip install -r requirements.txt
python run_pilot.py
```

`run_pilot.py` pirms palaišanas pārbauda, vai lokāli ir `pilot/pilot.sqlite`; ja nav, tas paņem jaunāko GitHub Actions artefaktu un tad palaiž Streamlit.

### Tēmu klasifikācija

```bash
python scripts/classify_topics.py --limit 1500
```

### Datu sinhronizācija

```bash
python sync_anon_nolemumi.py
```

### Jaunākās pilotdatubāzes lejupielāde

Ja `pilot/pilot.sqlite` nav lokāli vai gribi paņemt jaunāko GitHub Actions artefaktu:

```bash
python scripts/download_pilot_artifact.py --force
```

Skripts paņem jaunāko veiksmīgo `Build pilot dataset` palaišanu un izpako `pilot.sqlite` uz `pilot/pilot.sqlite`.

## API virziens

PostgreSQL/pgvector API var palaist ar Docker:

```bash
docker compose up
```

API noklusēti izmanto `postgresql://nolemumi:nolemumi@localhost:5432/nolemumi`.

## GitHub Actions

- `.github/workflows/sync.yml` lejupielādē DAGR CSV un saglabā datus kā artefaktu.
- `.github/workflows/build_pilot_dataset.yml` būvē `pilot/pilot.sqlite`, saglabā to kā artefaktu un publicē repo, ja datubāze mainās.

## Šīs nedēļas attīstības fokuss

1. Stabilizēt Streamlit pilotu kā galveno lietotāja saskarni.
2. Aizpildīt un pārbaudīt tēmu klasifikāciju.
3. Uzlabot meklēšanas rezultātu un lietas detaļu skatus.
4. Sakārtot pārskatu eksportu.
5. Sagatavot GitHub izmaiņas un īsu demo scenāriju.
