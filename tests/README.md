# Tester för Kursplan

Kör hela sviten från projektroten:

```powershell
.\venv\Scripts\python.exe -m pytest -q --tb=short
```

För parallell körning med fyra processer:

```powershell
.\venv\Scripts\python.exe -m pytest -n 4 -q --tb=short
```

Installera först `requirements-test.txt`, som även inkluderar `requirements.txt`. Använd samma Python för installation och testkörning. I PowerShell:

```powershell
.\venv\Scripts\python.exe -m pip install -r requirements-test.txt
.\venv\Scripts\python.exe -m playwright install chromium
```

På Linux kan du skapa en separat testmiljö från projektroten:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-test.txt
.venv/bin/python -m playwright install --with-deps chromium
.venv/bin/python -m pytest -q --tb=short
```

`--with-deps` installerar även Chromiums systembibliotek och kan kräva administratörsbehörighet. Kör själva pytest som vanlig användare.

## Testtäckning och Codacy

`pytest-cov` ingår i `requirements-test.txt`. För att skapa samma coverage-rapport
lokalt som i CI, kör från projektroten:

```powershell
.\venv\Scripts\python.exe -m pytest -n 4 --cov --cov-report=term-missing --cov-report=xml:coverage.xml
```

Rapporten visas i terminalen och sparas som `coverage.xml` i Cobertura-format.
`.coveragerc` aktiverar rad- och grentäckning, använder sökvägar relativt
projektroten och undantar testerna samt de virtuella miljöerna. Rapportfilerna
ignoreras av Git och Docker. Vanlig `pytest` utan `--cov` körs utan mätning.
Kod i separata processer som testerna själva startar mäts inte automatiskt.

GitHub Actions skapar rapporten på både Python 3.12 och 3.15 och sparar den
tillsammans med testresultaten i respektive artefakt. Efter godkända tester
skickar endast Python 3.12-jobbet rapporten till Codacy, så att samma commit
inte får två uppladdningar.

Hämta projektets API-token (repository-token) enligt
[Codacys instruktioner](https://github.com/codacy/codacy-coverage-reporter-action#uploading-coverage-to-codacy)
och spara den i GitHub-repositoryt under **Settings → Secrets and variables →
Actions → New repository secret**, med namnet `CODACY_PROJECT_TOKEN`.
Om hemligheten saknas, exempelvis för pull requests från en fork, hoppas
uppladdningen över och XML-rapporten finns fortfarande i artefakten.
Lokala testkörningar skickar inga rapporter till Codacy.

## Om Playwright saknas

`ModuleNotFoundError: No module named 'playwright'` under insamlingen betyder att testmiljön saknar Playwright. `pytest-playwright` i `requirements-test.txt` installerar Python-paketet; Chromium installeras separat med kommandot ovan. Enbart `requirements.txt` räcker inte för hela testsviten.

Installera testberoendena med samma Python som du använder för `-m pytest`, även om en annan virtuell miljö redan har dem. Kontrollera sedan installationen och testinsamlingen, till exempel på Linux:

```sh
.venv/bin/python -m pip check
.venv/bin/python -m pytest --collect-only -q
```

`--collect-only` läser in testerna utan att köra dem. Den vanliga webbimagen innehåller bara applikationens beroenden; använd den separata testmiljön eller projektets testimage i `tests/dockerfile` för hela sviten.

## Scenarier

| Område | Kompletterande testfil | Exempel på beteenden |
| --- | --- | --- |
| Konton och inloggning | `test_auth_scenarios.py` | Registreringsfel, dubbletter, lösenordshashning, roller, sessionsbyte, borttagna konton och säkra återgångslänkar. |
| Kundbokningar | `test_booking_scenarios.py` | Datumformat, skottår, deltagargränser, formulärfel, ägarskap, engångssvar, främmande CSRF-token och HTML-escaping. |
| Administratörsåtgärder | `test_admin_action_scenarios.py` | Åtkomst till samtliga admin-routes, CSRF, filter, förhandsgranskning, utgångna signaturer, privata anteckningar och misslyckade mejl. |
| Databaslogik | `test_database_scenarios.py` | Transaktioners återställning, återförsök, valideringsgränser, historik, sortering, filtrering och raderingsisolering. |
| Formulärskydd | `test_csrf_scenarios.py` | Tokenlivslängd, olika sessioner, felaktiga typer och Unicode i manipulerade token. |
| Mejl och kurskatalog | `test_mail_and_catalog_scenarios.py` | SMTP-inställningar, TLS-/inloggnings-/leveransfel, säkert mejlinnehåll och saknad eller trasig katalog. |
| Applikation och webbläsare | `test_application_scenarios.py` | `/health`, 404/405, sidresurser, utloggning, webbläsarvalidering och tangentbordsinloggning efter ett fel. |

De här filerna kompletterar befintliga tester för bokningarnas hela livscykel, samtidiga svar, migreringar, utvecklingsläge, responsiv layout, företagsuppslag, loggning och deploymentlogik.

## Releaseprov för återställning av webbimagen

`update.py` kör provet före driftsättning med ID:t för den befintliga webbimagen.
GitHub Actions kör samma prov i containerjobbet med en image byggd från pull
requestens bascommit eller från föregående commit på `main` vid en push.

1. Den gamla imagen startar mot en isolerad PostgreSQL 18-databas. Provet skapar
   ett kundkonto och testbokningar via HTTP, inklusive en avbokad förfrågan.
   E-post är avstängd och testkontots e-postadress verifieras direkt i testdatabasen.
2. Den nya imagen startar mot samma databas och kör sina vanliga startmigreringar.
   Provet kontrollerar att både kunden och administratören ser testbokningarna.
3. Den gamla imagen startar igen mot den uppgraderade databasen. Kunden loggar in,
   avbokar en befintlig förfrågan och skickar en ny. Administratören loggar in och
   ändrar status på en annan befintlig förfrågan. Provet kontrollerar resultatet i
   båda vyerna och jämför databasschemat före och efter återställningen.

Den föregående versionen behöver stödja de registrerings- och bokningsflöden som
provet använder. `test_deployment.py` verifierar ordningen och felhanteringen med
mockade Docker-kommandon. Det fullständiga releaseprovet kör de riktiga imagerna.

## Isolering

`all_db_path` och `scenario_app` i `conftest.py` kör databasberoende scenarier mot både tillfällig SQLite och PostgreSQL. PostgreSQL-fixturen skapar en separat testdatabas för varje test. Den inbyggda PostgreSQL-servern och Flask-servern för webbläsartester behöver lokal nätverksåtkomst.

`TEST_DATABASE_URL` får endast peka på en separat testserver där testkontot får skapa och ta bort testdatabaser. Använd aldrig produktionsdatabasen. Mejl är avstängda eller SMTP-mockade; externa företagsuppslag mockas i sina tester. Deploymenttesterna mockar kommandon och startar ingen riktig uppdatering.

Tester mot en lokal Flask-server och Chromium verifierar lokala användarflöden. De verifierar inte produktionsmiljöns Cloudflare Tunnel, SMTP-server eller Docker-installation.
