# Pilotdatu audits

Atjaunots: 2026-06-08

## Kopsavilkums

Lokālā pilotdatubāze `pilot/pilot.sqlite` ir derīga un izmantojama pilnteksta meklēšanai.

Pašreizējais stāvoklis pirms papildu apstrādes:

| Rādītājs | Vērtība |
| --- | ---: |
| Datubāzes izmērs | 80.1 MB |
| Nolēmumu metadati | 10 000 |
| Dokumentu ieraksti | 1 500 |
| Lejupielādēti dokumenti | 1 500 |
| Dokumenti ar izvilktu tekstu | 1 500 |
| FTS5 meklēšanas ieraksti | 1 500 |
| Tēmu klasifikācija | 1 500 |
| AI kopsavilkumi | nav aizpildīti sākotnējā kopijā |
| Semantiskais indekss | nav aizpildīts sākotnējā kopijā |

## Tabulas

Sākotnējā datubāzē ir šādas galvenās tabulas:

- `decisions`
- `documents`
- `documents_fts`
- `documents_fts_config`
- `documents_fts_content`
- `documents_fts_data`
- `documents_fts_docsize`
- `documents_fts_idx`

Papildus tabulas, ko izveido atsevišķi skripti:

- `case_topics` no `scripts/classify_topics.py` - aizpildīta 2026-06-08
- `ai_summaries` no `scripts/generate_ai_summaries.py`
- `semantic_embeddings` no `scripts/build_semantic_index.py`

## Biežākās tiesas metadatos

| Tiesa | Ieraksti |
| --- | ---: |
| Zemgales rajona tiesa | 740 |
| Rīgas pilsētas Vidzemes priekšpilsētas tiesa | 734 |
| Rīgas rajona tiesa | 729 |
| Administratīvā rajona tiesa Rīgas tiesu nams | 623 |
| Rīgas pilsētas Latgales priekšpilsētas tiesa | 615 |
| Augstākās tiesas Senāts | 517 |
| Kurzemes rajona tiesa | 507 |
| Rīgas pilsētas tiesa | 442 |

## Tēmu klasifikācija

2026-06-08 sākotnēji palaists:

```bash
python scripts/classify_topics.py --limit 1500
```

Pēc tam klasifikators uzlabots ar procesuālo fallback loģiku, papildu tēmām un palaists atkārtoti:

```bash
python scripts/classify_topics.py --refresh --limit 1500
```

Aktuālais rezultāts:

| Tēma | Dokumenti |
| --- | ---: |
| Administratīvo pārkāpumu lietas | 412 |
| Kredīti un parādi | 312 |
| Krimināllietas | 169 |
| Nekustamais īpašums | 142 |
| Administratīvās lietas | 141 |
| Nodokļi | 128 |
| Ģimenes tiesības | 71 |
| Darba tiesības | 46 |
| Būvniecība un plānošana | 34 |
| Maksātnespēja | 11 |
| Publiskie iepirkumi | 11 |
| Izglītība | 9 |
| Patērētāju tiesības | 7 |
| Civillietas | 4 |
| Apdrošināšana | 3 |

Pārliecības punktu sadalījums pēc atkārtotas klasifikācijas:

| Punkti | Dokumenti |
| --- | ---: |
| 1 | 16 |
| 2 | 122 |
| 3 | 293 |
| 4 | 466 |
| 5 | 297 |
| 6 | 151 |
| 7 | 114 |
| 8 | 37 |
| 9 | 4 |

Zemas pārliecības gadījumi samazināti no 132 līdz 16.

## Pārbaudītais pamatscenārijs

Komandrindas meklēšana strādā:

```bash
python search_pilot.py "kredīta parāds" --limit 3
```

Tā atgriež reālus nolēmumus ar lietas numuru, tiesu, datumu, procesu, nolēmuma veidu un teksta fragmentu.

## Secinājumi

1. Pilots ir labs sākumpunkts Streamlit saskarnes uzlabošanai.
2. Dati jau ļauj demonstrēt pilnteksta meklēšanu.
3. `case_topics` tabula tagad ir aizpildīta un izmantojama Streamlit filtros un tendenču skatā.
4. Nākamā lielākā praktiskā vērtība ir Streamlit rezultātu un lietas detaļu skata uzlabošana.
5. AI kopsavilkumi un semantiskais indekss jāplāno pēc tam, jo tiem vajag papildu atkarības vai ārēju modeļu piekļuvi.
