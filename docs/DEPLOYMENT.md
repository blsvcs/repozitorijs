# Publicēšana

## Datu avots

Pilotdatubāze netiek glabāta repozitorijā, jo `pilot.sqlite` pārsniedz GitHub parasto faila limitu.

Stabilais datu avots ir GitHub Release:

- Release: `pilot-dataset-latest`
- Asset: `pilot-dataset.zip`
- Fails ZIP arhīvā: `pilot.sqlite`

Workflow `.github/workflows/build_pilot_dataset.yml` atjauno šo release assetu pēc grafika un pēc būtiskām datu būves izmaiņām.

## Streamlit hostings

Hostinga starta fails:

```bash
streamlit run streamlit_entrypoint.py
```

`streamlit_entrypoint.py` pirms lietotnes importēšanas pārbauda `pilot/pilot.sqlite`. Ja datubāzes nav, tas lejupielādē jaunāko `pilot-dataset-latest/pilot-dataset.zip` un izpako `pilot.sqlite`.

## Streamlit Community Cloud

1. Izvēlies repo `blsvcs/repozitorijs`.
2. Branch: `main`.
3. Main file path: `streamlit_entrypoint.py`.
4. Secrets nav obligāti, jo repo un release ir publiski.
5. Ja GitHub API ierobežo lejupielādi, pievieno secret:

```toml
GH_TOKEN = "github_pat_..."
```

## Lokāla palaišana

```bash
pip install -r requirements.txt
python run_pilot.py
```

Piespiedu datubāzes atjaunošana:

```bash
python scripts/download_pilot_artifact.py --source release --force
```

## Demo pārbaude

```bash
python scripts/smoke_demo.py
```

Šis pārbauda datubāzes minimumu, trīs demo vaicājumus un Streamlit rezultātu/pārskata plūsmu.
