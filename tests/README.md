# Tester för Kursplan

Kör hela sviten från projektroten:

```powershell
.\venv\Scripts\python.exe -m pytest -q --tb=short
```

För parallell körning med fyra processer:

```powershell
.\venv\Scripts\python.exe -m pytest -n 4 -q --tb=short
```

Installera beroendena från `requirements.txt` och `requirements-test.txt`. Webbläsartesterna behöver Chromium, som installeras med `python -m playwright install chromium` i samma virtuella miljö.

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

## Isolering

`all_db_path` och `scenario_app` i `conftest.py` kör databasberoende scenarier mot både tillfällig SQLite och PostgreSQL. PostgreSQL-fixturen skapar en separat testdatabas för varje test. Den inbyggda PostgreSQL-servern och Flask-servern för webbläsartester behöver lokal nätverksåtkomst.

`TEST_DATABASE_URL` får endast peka på en separat testserver där testkontot får skapa och ta bort testdatabaser. Använd aldrig produktionsdatabasen. Mejl är avstängda eller SMTP-mockade; externa företagsuppslag mockas i sina tester. Deploymenttesterna mockar kommandon och startar ingen riktig uppdatering.

Tester mot en lokal Flask-server och Chromium verifierar lokala användarflöden. De verifierar inte produktionsmiljöns Cloudflare Tunnel, SMTP-server eller Docker-installation.
