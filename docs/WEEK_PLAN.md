# Darba plāns nedēļai

Periods: 2026-06-08 līdz 2026-06-12

## Nedēļas mērķis

Pacelt projektu līdz stabilam Streamlit pilotam, ko var demonstrēt un lietot tiesu nolēmumu meklēšanai, filtrēšanai, sākotnējai analītikai un pārskatu sagatavošanai.

## Pirmdiena, 2026-06-08

Mērķis: sakārtot pamatu.

- Atjaunot README.
- Pievienot projekta karti.
- Pievienot datu auditu.
- Pārbaudīt `pilot/pilot.sqlite`.
- Aizpildīt `case_topics` tabulu ar lokālo klasifikatoru.
- Pārbaudīt pamata meklēšanu.

Statuss: sākts un pirmā kārta izdarīta.

Papildus paveikts:

- Streamlit meklēšanai pievienoti ātrie vaicājumi.
- Sākuma skats rāda datu profilu un biežākās tēmas.
- Meklēšanas rezultātos pievienota atlase pārskatam.
- Atlasītos nolēmumus var eksportēt Markdown vai HTML formātā.
- Pārskata teksts pārveidots par juridiskās prakses melnrakstu ar atlases profilu, novērojumiem, iznākumiem, ierobežojumiem un avotiem.
- Tēmu klasifikators uzlabots ar administratīvo pārkāpumu tēmu, maksātnespēju, apdrošināšanu un procesuālo fallback loģiku.
- Zemas pārliecības tēmu gadījumi samazināti no 132 līdz 16.
- Pievienoti demo scenāriji lietotnē un `docs/DEMO_SCENARIOS.md`.
- AI kopsavilkumu un semantiskā indeksa skripti papildināti ar drošiem `--dry-run`/`--status` režīmiem.
- Pievienots `docs/AI_SEMANTIC_DEPLOYMENT.md` ar nākamo palaidienu un publicēšanas secību.

## Otrdiena, 2026-06-09

Mērķis: uzlabot Streamlit lietošanas pieredzi.

- Sakārtot sākuma ekrānu.
- Padarīt statistiku un filtrus pārskatāmākus.
- Uzlabot rezultātu kartītes.
- Uzlabot tukšos stāvokļus un kļūdu paziņojumus.
- Pārbaudīt, ka tēmu filtrs strādā ar jauno `case_topics` tabulu.

## Trešdiena, 2026-06-10

Mērķis: padarīt analītiku noderīgāku.

- Pārskatīt tēmu klasifikācijas kvalitāti.
- Papildināt atslēgvārdu vārdnīcu.
- Uzlabot tendenču skatu pa gadiem, tiesām un tēmām.
- Pievienot skaidrus skaidrojumus, kad klasifikācija ir zemas pārliecības.

## Ceturtdiena, 2026-06-11

Mērķis: sagatavot pārskatu ģenerēšanu.

- Uzlabot gudrā ziņojuma tekstu.
- Pievienot ērtu HTML/Markdown eksportu.
- Sakārtot avotu sadaļu.
- Pievienot juridiskās pārbaudes brīdinājumu.
- Izvērtēt AI kopsavilkumu mazo testa partiju, ja ir pieejama modeļu piekļuve.

## Piektdiena, 2026-06-12

Mērķis: stabilizēt un sagatavot nodošanai.

- Iziet cauri demo scenārijiem.
- Sakārtot README pēc faktiskā stāvokļa.
- Pārbaudīt galvenos vaicājumus.
- Sagatavot GitHub izmaiņas.
- Pierakstīt nākamās nedēļas prioritātes.

## Demo scenāriji

- Meklēt: `kredīta parāds`
- Meklēt: `darba samaksa`
- Meklēt: `būvatļauja`
- Filtrēt pēc tēmas: `Administratīvās lietas`
- Filtrēt pēc tēmas: `Kredīti un parādi`
- Atvērt lietas detaļu skatu un sagatavot īsu pārskatu.
