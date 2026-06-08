# AI, semantika un publicēšana

Atjaunots: 2026-06-08

## Pašreizējais statuss

Streamlit pilots ir demo gatavs pilnteksta meklēšanai, tēmu filtriem un pārskatu eksportam.

AI un semantikas slāņi ir sagatavoti, bet nav pilnībā aizpildīti:

- `ai_summaries`: vēl nav ģenerēti kopsavilkumi.
- `semantic_embeddings`: vēl nav uzbūvēts semantiskais indekss.

## AI kopsavilkumi

Skripts:

```bash
python scripts/generate_ai_summaries.py
```

Drošs tests bez modeļa izsaukuma:

```bash
python scripts/generate_ai_summaries.py --dry-run --limit 1
```

Reāls mazais tests:

```bash
set GITHUB_TOKEN=...
python scripts/generate_ai_summaries.py --limit 5 --sleep 1
```

GitHub Actions vai Codespaces vidē `GITHUB_TOKEN` parasti ir pieejams automātiski. Lokāli vajag tokenu ar piekļuvi GitHub Models vai saderīgam OpenAI endpointam.

## Semantiskais indekss

Skripts:

```bash
python scripts/build_semantic_index.py
```

Statusa pārbaude bez modeļa lejupielādes:

```bash
python scripts/build_semantic_index.py --status
```

Drošs tests bez modeļa ielādes:

```bash
python scripts/build_semantic_index.py --dry-run --limit 1
```

Mazais reālais tests:

```bash
pip install -r requirements.txt
python scripts/build_semantic_index.py --limit 50 --batch-size 16
```

Pilns pilotindekss:

```bash
python scripts/build_semantic_index.py --limit 1500 --batch-size 32
```

Piezīme: pirmajā reizē `sentence-transformers/all-MiniLM-L6-v2` tiks lejupielādēts lokāli.

## Publicēšanas varianti

### 1. Streamlit Community Cloud

Vienkāršākais ceļš demo publicēšanai:

1. Pieslēgt GitHub repo Streamlit Community Cloud.
2. Kā entrypoint norādīt `streamlit_app.py`.
3. Nodrošināt, ka repo satur `pilot/pilot.sqlite`.
4. Publicēt lietotni kā privātu vai publisku demo.

Pluss: ātri un tieši piemērots Streamlit lietotnei.

Mīnuss: lielāka datubāze un smagi modeļi jālieto piesardzīgi.

### 2. Docker/VPS

Stabilāks ceļš ilgākai lietošanai:

```bash
pip install -r requirements.txt
streamlit run streamlit_app.py --server.port 8501
```

Var palaist uz VPS aiz reverse proxy.

### 3. FastAPI + PostgreSQL

Lielākas sistēmas virziens:

```bash
docker compose up
```

Šis ceļš ir piemērots, ja gribam API, PostgreSQL/pgvector un atsevišķu frontend.

## Ieteiktā secība

1. Publicēt Streamlit pilotu ar esošo `pilot.sqlite`.
2. Ģenerēt 20-50 AI kopsavilkumus kā kvalitātes testu.
3. Uzbūvēt semantisko indeksu 200 lietām.
4. Pārbaudīt līdzīgo lietu skatu.
5. Tikai pēc tam iet uz pilnu 1 500 lietu semantiku un plašāku AI kopsavilkumu ģenerēšanu.

## Riska piezīmes

- AI kopsavilkumi var kļūdīties; tie jālieto kā melnraksts.
- Semantiska līdzība nav juridiska līdzība pilnā nozīmē.
- Publicējot jāņem vērā datubāzes izmērs un hostinga limits.
- Ja lietotne kļūst publiska, jāatstāj skaidrs brīdinājums, ka tas nav juridisks atzinums.
