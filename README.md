# Kursplan

## Kör appen i utvecklingsläge

Sätt `DEV_MODE=true` i `.env` och starta appen med `python app.py`. Det aktiverar debugloggar och skapar en tom SQLite-fil i systemets temporära katalog. Sökvägen visas i startloggen. Filen tas bort när appen avslutas normalt, så varje start börjar med en ny databas.

Ange `test_email` och `test_password` i `.env` om du vill att appen skapar en administratör vid start. Sätt `send_mail=0` om du vill testa bokningar utan att skicka bekräftelser.

Med `DEV_MODE=false` eller utan inställningen använder appen PostgreSQL via `db_path`.

## Databasuppgradering för bokningsönskemål

`database.create_database()` skapar inte bara nya tabeller utan kör även den
återkörbara migrationen av `bookings`. Den körs automatiskt när appen startas
med `python app.py`, både för PostgreSQL och för SQLite i utvecklingsläget. En
normal omstart efter att koden har uppdaterats räcker därför; kör inte någon
databasreset och ta som vanligt en säkerhetskopia före en produktionsuppgradering.

Nya förfrågningar lagrar kundens uppgifter strukturerat:

- `datumtyp=exakt` använder `onskat_datum`, medan `datumtyp=intervall` använder
  `onskat_startdatum` och `onskat_slutdatum`.
- `utbildningskalla` skiljer katalogval från fritext.
- `onskad_ort`, `lokalansvar` och `lokaladress` håller önskad ort och lokalval
  åtskilda.
- När en administratör bekräftar en förfrågan skrivs datum och ort till
  `faststallt_datum` och `faststalld_ort`. Kundens ursprungliga önskemål ändras
  inte.

De äldre kolumnerna och det befintliga 10-fältskontraktet bevaras för gamla
läsare. Vid migration fylls strukturerat exakt datum och önskad ort i för äldre
väntande rader, där betydelsen kan härledas från det gamla formuläret. För äldre
bokade eller avbrutna rader lämnas nya önskemålsfält tomma eftersom det inte går
att säkert avgöra om deras äldre datum och ort redan har ändrats. Deras befintliga
värden behålls oförändrade. `utbildningskalla`, `lokalansvar`, `lokaladress` och
de fastställda fälten lämnas `NULL` för äldre rader när betydelsen inte kan
härledas utan antaganden.

## Ge en användare administratörsbehörighet

Alla användare och administratörer loggar in på samma sida, `/login`. Åtkomsten
till adminpanelen styrs enbart av kolumnen `users.role`; det finns inget rollval
i inloggningsformuläret.

När du är ansluten till PostgreSQL med exempelvis DBeaver kan du ge eller ta bort
administratörsbehörighet med:

```sql
UPDATE users SET role = 'admin' WHERE email = 'anvandare@example.com';
UPDATE users SET role = 'user' WHERE email = 'anvandare@example.com';
```

Endast de exakta värdena `user` och `admin` används. En rolländring kontrolleras
mot databasen vid nästa anrop till adminpanelen, så användaren behöver inte få en
ny separat admininloggning.

### Anslut till PostgreSQL med DBeaver i utvecklingsmiljön

`COMPOSE_PROFILES` i `.env` följer värdet på `DEV_MODE`. När `DEV_MODE=true`
startas den profil som binder PostgreSQL-porten till den lokala datorn. Med
`DEV_MODE=false` startas inte profilen och ingen databasport publiceras.

```sh
docker compose up -d --remove-orphans
```

Anslut DBeaver till `127.0.0.1:5432`, databas `postgres`, användare `postgres`
och lösenordet från `db_password` i `.env` (standardvärdet är `password`).

## Kör databastesterna lokalt

```sh
python -m pip install -r requirements-test.txt
python -m playwright install chromium
python -m pytest
```

Efter installationen startar pytest en tillfällig PostgreSQL 18-instans automatiskt. Det behövs varken Docker, en `.env`-fil eller en redan körande databas. Varje test får en egen tom databas, som tas bort efter testet. När testkörningen är klar stoppas instansen och dess data tas bort. Kör som vanlig användare.

Chromium används av webbläsartesterna. I CI installeras den automatiskt med
Playwright innan pytest körs.

Om den inbyggda PostgreSQL-versionen inte finns för din plattform kan du installera `requirements.txt` och sätta `TEST_DATABASE_URL` till en separat PostgreSQL-testserver. Testanvändaren behöver rätt att skapa databaser. Testerna skapar och tar bort sina egna databaser även i det läget.

## Från pull request till produktion

**Kursplan CI** körs på pull requests till `main` och vid push till `main`. Jobbet
**Release gate** kräver gröna statiska kontroller av Python, JavaScript,
kurskatalogen och GitHub Actions, samt pytest på Python 3.12 och 3.15 med
PostgreSQL 18, SQLite och Chromium. På pull requests granskar GitHub även nya
sårbara beroenden. Snyk rapporterar separat i Actions och Code Scanning.

CI kör samma bygge och isolerade tester som `update.py`. Därefter startar den
projektets Compose-konfiguration mot en separat PostgreSQL-instans och provar
`/health`, statiska filer och en
administratörsinloggning via nätverksaliaset som tunneln använder. Testrapporter
och eventuella webbläsarspår sparas i sju dagar i Actions.

Aktivera **Release gate** som obligatorisk kontroll i GitHub och lägg till en
oberoende granskare med skrivbehörighet för obligatoriska godkännanden. Följ
[guiden för kodgranskning](.github/code-review.md) för inställningarna för `main`.

### Uppdatera produktionen från servern

Kör kommandot i Kursplans befintliga projektkatalog på produktionsservern.

```sh
python3 update.py
```

Skriptet hämtar senaste `origin/main` och kontrollerar att de spårade filerna
är rena och att `main` kan uppdateras med en fast-forward. Det bygger webbimagen
och kör tester med avstängd e-post, ett tillfälligt privat Docker-nätverk och
en egen PostgreSQL-databas. Om ett test misslyckas kör den befintliga
webbversionen vidare. Om aktuell commit redan körs kontrollerar skriptet hälsan
och avslutar.

Efter godkända tester flyttar skriptet fram `main`, driftsätter den testade
webbimagen i det befintliga Compose-projektet och kontrollerar Compose-hälsan,
databasanslutningen och `https://kursplan.se/health`. Vid fel under
driftsättningen försöker skriptet återställa föregående webbimage. Granska
ändringar i `docker-compose.yml` separat innan de införs i produktion.
Skriptet avbryter uppdateringen när den filen skiljer sig. Säkerhetskopiera
databasen före schemaändringar och hantera eventuell återställning av en
databasmigration separat.

Servern behöver Linux, Git, Docker och Docker Compose med stöd för `up --wait`.
Använd Python 3.11.4 eller nyare, eller en distributionsversion som har
`tarfile.data_filter` via en säkerhetsuppdatering. Kör på `main` i den befintliga
driftsmappen med dess `.env`, `DEV_MODE=false` och en fungerande Compose-stack.
Serverkontot behöver åtkomst till Docker, Git-repot och de register som bygget
och testerna använder.
Kommandot kör kontrollerna på servern med Docker. GitHub Actions granskar
ändringar i pull requests. Se [driftkommandona](Commands.md) för första hämtningen.
