# Projekta karte

Atjaunots: 2026-06-08

## Mērķis

Izveidot praktisku rīku Latvijas anonimizēto tiesu nolēmumu meklēšanai, apskatei un sākotnējai analītikai. Pirmais mērķis ir stabils pilots, ko var demonstrēt un lietot bez sarežģītas infrastruktūras.

## Galvenās daļas

### Streamlit pilots

Fails: `streamlit_app.py`

Šī ir galvenā lietotāja saskarne. Tā izmanto `pilot/pilot.sqlite`, lai rādītu statistiku, meklēšanas rezultātus, tēmu filtrus, lietas detaļas, līdzīgās lietas un pārskata ģenerēšanu.

### Lokāla pilotdatubāze

Fails: `pilot/pilot.sqlite`

SQLite datubāze ar nolēmumu metadatiem, dokumentu tekstiem un FTS5 pilnteksta indeksu. Tā ļauj pilotam strādāt bez PostgreSQL un bez Docker.

### Komandrindas meklēšana

Fails: `search_pilot.py`

Vienkārša pārbaudes utilīta, kas meklē `documents_fts` indeksā un izvada atrastos nolēmumus. Laba ātrai datu un meklēšanas pārbaudei.

### Datu sinhronizācija

Fails: `sync_anon_nolemumi.py`

Lejupielādē DAGR anonimizēto nolēmumu CSV, aprēķina hash, arhivē CSV un importē metadatus SQLite datubāzē.

### Pilotdatubāzes būvēšana

Fails: `scripts/build_pilot_sqlite.py`

No DAGR CSV un nolēmumu PDF failiem būvē `pilot/pilot.sqlite`: metadati, dokumentu statuss, izvilktais teksts un FTS5 indekss.

### Tēmu klasifikācija

Fails: `scripts/classify_topics.py`

Lokāls, noteikumos balstīts klasifikators. Tas aizpilda `case_topics` tabulu ar tēmām, piemēram, kredīti un parādi, darba tiesības, ģimenes tiesības, būvniecība, nodokļi un citas.

### AI kopsavilkumi

Fails: `scripts/generate_ai_summaries.py`

Ģenerē un kešo juridiskus kopsavilkumus `ai_summaries` tabulā. Šim solim vajag ārēju modeļu piekļuvi, piemēram, GitHub Models vai OpenAI saderīgu endpointu.

### Semantiskais indekss

Fails: `scripts/build_semantic_index.py`

Būvē lokālu embedding indeksu `semantic_embeddings` tabulā līdzīgo lietu meklēšanai. Vajag `sentence-transformers` atkarību un modeļa lejupielādi.

### FastAPI un PostgreSQL virziens

Faili: `app/main.py`, `app/db.py`, `db/schema.sql`, `docker-compose.yml`

Šis ir lielākas sistēmas virziens ar PostgreSQL, pgvector, teksta meklēšanu un semantisko meklēšanu caur API. Šonedēļ galvenais fokuss paliek uz Streamlit pilotu.

## Datu plūsma

1. `sync_anon_nolemumi.py` lejupielādē DAGR CSV.
2. `scripts/build_pilot_sqlite.py` izveido vai papildina `pilot/pilot.sqlite`.
3. `scripts/classify_topics.py` pievieno tēmu klasifikāciju.
4. `scripts/generate_ai_summaries.py` pēc vajadzības pievieno AI kopsavilkumus.
5. `scripts/build_semantic_index.py` pēc vajadzības pievieno līdzīgo lietu indeksu.
6. `streamlit_app.py` rāda to lietotājam.

## Tuvākie darbi

- Sakārtot Streamlit sākuma un rezultātu skatu.
- Padarīt tēmu klasifikāciju redzamu un pārbaudāmu lietotnē.
- Uzlabot lietas detaļu skatu.
- Pievienot pārskatu eksportu ērtā formā.
- Sagatavot skaidru demo scenāriju.
