# AGENTS.md — Kursplan

Instruktioner för AI-kodagenter som arbetar i detta repository. Gäller hela projektet. Läs relevanta filer och tester innan du ändrar kod.

## 1. Projektets syfte och teknik

Kursplan (kursplan.se) är en svensk webbapplikation för att begära och administrera företagsutbildningar. Kunder kan skapa konto, skicka utbildningsförfrågningar och följa sina bokningar. Administratörer hanterar inkomna förfrågningar och bokar datum/ort.

- Backend: Python och Flask med blueprints.
- Frontend: Jinja2-mallar, vanlig HTML, CSS och JavaScript (ingen etablerad frontend-buildkedja).
- Databas: PostgreSQL 18 i drift via psycopg 3; temporär SQLite-databas i utvecklingsläge.
- Drift: Docker Compose bakom Cloudflare Tunnel.
- Tester: pytest, med PostgreSQL-testdatabaser och separat test av utvecklingsläget.

Skriv gränssnittstexter, felmeddelanden och användardokumentation på svenska. Behåll befintlig teknik om uppgiften inte uttryckligen kräver något annat.

## 2. Hitta rätt i koden

| Fil/katalog | Ansvar |
| --- | --- |
| `app.py` | Flask-app, miljökonfiguration, registrering av blueprints, databasinitiering, `/health`. Innehåller även äldre kod för en annan bokningsmodell. |
| `users/routes.py` | Kundregistrering, inloggning, dashboard, ny bokning och avbokning. |
| `admin/routes.py` | Administratörsinloggning, behörighetskontroll, översikt och hantering av bokningar. |
| `database.py` | SQL, anslutningar och databasschema för `bookings` och `users`. |
| `gen_func.py` | Läsning av utbildningslista och e-postbekräftelser. |
| `utbildningar.json` | Nuvarande katalog med valbara utbildningar. |
| `templates/` | Jinja-mallar, bl.a. `book.html`, `dashboard.html`, `admin_dashboard.html`. |
| `static/style/` | Sidornas CSS. |
| `tests/` | Databastester och tester av `DEV_MODE`. |
| `docker-compose.yml`, `dockerfile` | Container- och nätverkskonfiguration. |
| `.github/workflows/` | Test-, Docker- och säkerhetskontroller. |

Utgå från nuvarande datamodell, routes och mallar. Använd inte äldre översättnings-/jobbflöden i `app.py` (`get_jobs`, `accept_job`, `submit`) som förebild för nya Kursplan-funktioner; de är legacy och använder delvis andra tabellfält och direkta SQLite-anrop.

## 3. Arbetssätt vid varje uppgift

1. Läs berörda routes, databashjälpare, mallar och tester. Bekräfta hur funktionaliteten faktiskt fungerar innan du ändrar den.
2. Genomför minsta sammanhängande lösning. Undvik orelaterad refaktorering, nya ramverk och omfattande designändringar.
3. Håll logik för databasåtkomst i `database.py`, HTTP-/behörighetslogik i respektive blueprint och presentation i mallar/CSS.
4. Validera indata på serversidan även om formuläret har HTML- och JavaScript-validering. Använd parameteriserade SQL-frågor.
5. Tänk igenom både kund- och administratörsflöden. Ändrade bokningsfält måste följa med genom lagring, visning, redigering/status och eventuella e-postutskick.
6. Lägg till eller uppdatera relevanta tester. Rapportera vilka tester som faktiskt kördes och eventuella återstående begränsningar.
7. Ändra inte produktionsmiljö, databas eller deployment som en sidoeffekt av en vanlig koduppgift.

## 4. Bokningar och datamodell

Nuvarande tabell `bookings` skapas i `database.create_database()` och har fälten `id`, `name`, `email`, `organization_number`, `utbildning`, `antal`, `ort`, `lokal`, `datum`, `status`. Status används som `pending`, `accepted` och `cancelled`.

- Förfrågan är inte samma sak som en bekräftad bokning. Bevara korrekt statusövergång och förhindra att redan avbokade eller accepterade poster behandlas som väntande.
- Flera mallar använder positionsindex som `b[4]` och `b[9]`. Vid schemaändringar ska alla beroenden granskas; föredra tydligt namngivna datafält vid ny kod.
- Bevara befintliga bokningar och konton. `CREATE TABLE IF NOT EXISTS` är ingen migrering av befintligt schema: lägg till en uttrycklig, säker migreringsstrategi när kolumner behöver ändras.
- All databasfunktionalitet som ska fungera i både utveckling och drift måste hantera både `pathlib.Path` (SQLite) och PostgreSQL-DSN (psycopg). Testa båda där det är relevant.
- Ändra inte befintlig information tyst vid schema- eller statusförändringar. Ingen `DROP TABLE`, databasreset eller dataradering utan uttryckligt uppdrag.

### Planerade bokningsförbättringar (inte antagna som redan implementerade)

När ett uppdrag gäller nästa version av bokningsformuläret ska följande produktkrav beaktas:

- Kunden ska kunna välja **antingen ett exakt utbildningsdatum eller ett datumintervall med start- och slutdatum**; alternativen ska vara ömsesidigt uteslutande och valideras.
- Utbildning väljs i en **sökbar lista**, men det ska också gå att skriva ett eget utbildningsnamn om kursen saknas.
- Önskad **ort är frivillig** vid förfrågan och kan kompletteras senare.
- **Antal deltagare** ska anges som ett positivt heltal.
- Kunden ska välja **vem som ordnar utbildningslokalen**. Om kunden ordnar egen lokal ska adress vara obligatorisk; annars ska adressen inte krävas.
- Se till att både exakt datum och intervall, fritextutbildning, ort, deltagarantal och lokalval representeras korrekt i databasen och administratörens vy.

Detta är målbild för berörda ändringsuppdrag, inte ett skäl att ändra formuläret när uppgiften gäller något annat. I `main` har formuläret fortfarande ett enskilt datum, vanlig kurslista och obligatorisk ort.

## 5. Säkerhet och åtkomst

Applikationen hanterar personuppgifter och bokningsuppgifter. Säkerhet ska prioriteras framför bekväma genvägar.

- Använd `@admin_required` för skyddade administratörsroutes. Kontrollera även ägarskap/behörighet på varje post som en kund läser, ändrar eller avbokar; inloggning ensam räcker inte.
- Lita inte på formulärfält, URL-parametrar eller sessionsdata som auktorisering. Förhindra IDOR, massuppdatering av fel poster och otillåtna statusövergångar.
- Lägg inte till mer lösenordshantering i klartext. Befintlig kod lagrar och jämför lösenord i klartext; se detta som en säkerhetsskuld som kräver planerad, bakåtkompatibel migrering till säkra lösenordshashar, inte som ett mönster att kopiera.
- Använd CSRF-skydd för tillståndsändrande formulär vid ändringar i autentisering/bokning. Validera och normalisera e-post, datum, heltal och fritext.
- Lagra inte autentiseringsuppgifter, SMTP-lösenord, sessionsnycklar eller andra hemligheter i Git, loggar, tester eller klientkod. `.env` är lokal; `.example.env` är en mall och ska aldrig innehålla riktiga hemligheter.
- Logga inte onödigt namn, e-post, personuppgifter eller lösenord. Behåll Jinja-escaping; markera inte användarinmatning som `safe`.
- E-post ska endast skickas för avsedd händelse, till rätt mottagare, och inte som bieffekt av tester.

## 6. Miljöer, drift och begränsningar

`DEV_MODE=true` i `.env` ger enligt `README.md` en temporär SQLite-databas som tas bort när processen avslutas normalt. `DEV_MODE=false` använder PostgreSQL via `db_path`. `send_mail=0` stänger av bokningsmejl i testmiljö.

- Kör helst ändringar lokalt eller i testmiljö först. Anslut inte tester till produktionsdatabasen.
- `docker-compose.yml` använder den externa Docker-nätverksresursen `jk_tunnel_net`, aliaset `kursplan`, webbport **8000** internt och PostgreSQL **18** med volymen `postgres_data`. Bevara dessa kopplingar om inte deployment uttryckligen ska ändras.
- Databasen ligger på internt backend-nätverk. `db-access` kan publicera PostgreSQL endast på `127.0.0.1:5432` i utvecklingsprofilen. Exponera inte databasen publikt.
- Ändra inte Cloudflare Tunnel, befintliga Docker-nätverk, andra containerprojekt eller serverns övriga tjänster som en del av detta repositorys uppgifter.
- Kör inte `docker compose down -v`, `docker system prune`, `git reset --hard`, omformatering av volymer eller liknande destruktiva åtgärder utan uttrycklig begäran.
- `update.py` kör bl.a. `git pull` och omstart av Compose-tjänster. Kör **inte** det skriptet automatiskt för testning eller som ett normalt steg vid kodändringar.
- Bevara `/health` och containerhälsokontrollen.

## 7. Tester och kvalitetskontroll

Installera beroenden enligt projektets filer:

```bash
python -m pip install -r requirements.txt -r requirements-test.txt
python -m pytest
```

Testerna i `tests/conftest.py` startar normalt en temporär PostgreSQL 18-server via `embedded-postgres`, skapar en separat databas per test och städar efter sig. Kör som vanlig användare, inte root. Alternativt används `TEST_DATABASE_URL` mot en **separat testserver** där testkontot får skapa och ta bort testdatabaser.

Vid ändringar av bokningsformulär och behörighet bör tester täcka:
- Giltig och ogiltig indata, inklusive datumintervall, tom ort, egna kursnamn och lokaladress.
- Kundens respektive administratörens åtkomst, inklusive nekad åtkomst till andra kunders bokningar.
- Statusövergångar och att endast valda väntande bokningar uppdateras.
- Datalagring och visning utan förlust av befintliga fält.
- `DEV_MODE`/SQLite och PostgreSQL för ändrad SQL-logik.
- E-post med `send_mail=0` eller mockad SMTP; inga riktiga utskick.
- Att `/health` fortfarande svarar korrekt.

Kontrollera också berörda Jinja-mallar, formulärfält och mobilanpassning. GitHub Actions testar pytest, Dockerbygge och säkerhetsverktyg, men en grön pipeline ersätter inte tester för nya funktioner.

## 8. Slutrapport från agenten

När en koduppgift är klar, svara kort på svenska med:
1. Vad som ändrats och i vilka filer.
2. Hur användarflödet påverkas.
3. Vilka kontroller/tester som körts och resultatet.
4. Eventuella migreringar, säkerhetsrisker eller manuella driftssteg.

**Grundregel:** Behåll fungerande beteende och produktionsdata, gör avgränsade verifierbara ändringar och utgå alltid från repositoryts faktiska tillstånd.
