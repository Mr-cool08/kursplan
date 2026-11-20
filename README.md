# Kursplan

## Kör appen i utvecklingsläge

Sätt `DEV_MODE=true` i `.env` och starta appen med `python app.py`. Det aktiverar debugloggar och försöker först använda den beständiga PostgreSQL-databasen i `db_path`, med fem sekunders anslutningstimeout. Då finns konton och bokningar kvar mellan omstarter, och ändringar sparas i den konfigurerade databasen.

Om `db_path` saknas, är ogiltig eller databasen inte går att ansluta till faller appen tillbaka till en tom SQLite-fil direkt i projektets rot, bredvid `app.py`. Filen heter `kursplan-dev-<slumpmässigt namn>.db` och får ett unikt namn vid varje start. Startloggen visar en varning och filens sökväg. Filen tas bort när appen avslutas normalt, så reservdatabasen börjar tom vid varje start. Fel i exempelvis databasens schema avbryter fortfarande starten och döljs inte av reservlösningen.

### Inloggning i utvecklingsläget

I utvecklingsläget skapas automatiskt två konton. Båda loggar in via `/login`; administratören kommer till administratörsöversikten och standardanvändaren till kundens dashboard.

| Konto | Förvald e-post | Inställningar i `.env` | Nyckel i kontofilen |
| --- | --- | --- | --- |
| Administratör | `admin@example.invalid` | `test_email`, `test_password` | `admin` |
| Standardanvändare | `user@example.invalid` | `dev_user_email`, `dev_user_password` | `user` |

Det finns inget gemensamt standardlösenord. Om ett lösenord varken har angetts i miljön eller sparats tidigare genereras ett separat slumpmässigt lösenord för kontot. Inställningar i miljön, exempelvis från `.env`, har företräde framför sparade uppgifter och förvalda adresser.

Inloggningsuppgifterna sparas i `.dev-accounts.json` bredvid `app.py`. Filen ignoreras av Git och återanvänds mellan omstarter, även när SQLite-databasen skapas om. Startloggen visar filens sökväg utan att skriva ut lösenorden. Dela eller versionshantera inte filen. Nya utvecklingskontons lösenord lagras hashade i databasen.

För att se de aktuella lokala inloggningsuppgifterna, öppna filen och läs `email` och `password` under `admin` respektive `user`. I PowerShell, från projektets rot:

```powershell
Get-Content .dev-accounts.json
```

Befintliga databaskonton med samma e-postadress behåller sina uppgifter och sin roll. Att ändra `.env` eller kontofilen återställer därför inte deras lösenord. Kontona skapas i den databas som utvecklingsläget använder. Sätt `send_mail=0` om du vill testa bokningar utan att skicka bekräftelser.

Vid utveckling i Docker finns `.dev-accounts.json` inne i webbcontainern. Använd kontoinställningarna i `.env` om inloggningsuppgifterna ska finnas kvar även när containern ersätts.

```console
docker compose exec web cat /app/.dev-accounts.json
```

### Exempelbokningar

Den vanliga utvecklingsanvändaren får också fyra exempelbokningar: två väntande förfrågningar, en accepterad och en avbokad. Exemplen visar exakt datum, datumintervall, egen utbildning, frivillig ort och olika lokalval. De syns både i kundens dashboard och administratörsöversikten; där visas accepterade och avbokade poster via filtren. Inga mejl skickas när exemplen skapas.

Exemplen skapas en gång per utvecklingsanvändare och databas. En separat tabell, `dev_booking_seeds`, skapas automatiskt i utvecklingsläget och håller reda på detta. Omstarter ändrar inte befintliga bokningar och återställer inte exempel som du har redigerat, avbokat eller tagit bort. En ny tillfällig SQLite-databas får nya exempel. Med `DEV_MODE=false` skapas varken exempelbokningar eller markörtabellen.

Med `DEV_MODE=false` eller utan inställningen använder appen PostgreSQL via `db_path` och skapar inga utvecklingskonton. Den befintliga administratörsinställningen `test_email`/`test_password` används som tidigare.

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
- När kunden accepterar ett datumförslag skrivs datum, ort och lokal till
  `faststallt_datum`, `faststalld_ort` och `faststalld_lokal`. Kundens ursprungliga
  önskemål ändras inte. Administratören kan inte acceptera åt kunden.

De äldre kolumnerna och det befintliga 10-fältskontraktet bevaras för gamla
läsare. Vid migration fylls strukturerat exakt datum och önskad ort i för äldre
väntande rader, där betydelsen kan härledas från det gamla formuläret. För äldre
bokade eller avbrutna rader lämnas nya önskemålsfält tomma eftersom det inte går
att säkert avgöra om deras äldre datum och ort redan har ändrats. Deras befintliga
värden behålls oförändrade. `utbildningskalla`, `lokalansvar`, `lokaladress` och
de fastställda fälten lämnas `NULL` för äldre rader när betydelsen inte kan
härledas utan antaganden. Äldre accepterade bokningar behåller sin bekräftade
status; deras redan sparade datum, ort och lokal fylls i som fastställda uppgifter
utan att ett nytt kundgodkännande eller ett historiskt datumförslag konstrueras.

## Bokningsflöde med datumförslag

En ny förfrågan får ett bokningsnummer och status **Ny förfrågan** (`pending`).
Administratören kan frivilligt markera den som **Registrerad** (`registered`)
eller direkt öppna bokningen och förbereda ett datumförslag. Datum, ort och lokal
förifylls när de finns i kundens önskemål eller tidigare förslag. Förslaget
valideras och visas i en sammanfattning innan administratören skickar det.

Bokningen får då status **Inväntar kundens svar** (`awaiting_response`). Kunden
ser förslaget i portalen och får, om e-post är aktiverad, ett meddelande med tre
alternativ. Länkarna leder till bokningen via inloggning. Att öppna en länk ändrar
aldrig bokningen: kunden bekräftar sitt svar i ett CSRF-skyddat formulär.

- **Acceptera förslaget** fastställer uppgifterna och ger status **Accepterad**
  (`accepted`). Endast kunden som äger bokningen kan godkänna förslaget.
- **Begär nytt datum** ger status **Nytt datum önskas** (`new_date_requested`).
  Det tidigare förslaget avböjs och bevaras. Administratören kan sedan skicka fler
  förslag inom samma bokningsnummer. Gamla förslag kan aldrig accepteras.
- **Avbryt bokningen** ger status **Avbruten** (`cancelled`) och avaktiverar
  utestående förslag. Kunden kan också avbryta före ett förslag och när ett nytt
  datum har begärts.

Efter kundens godkännande kan administratören markera bokningen som **Slutförd**
(`completed`), **Ombokad** (`rescheduled`) eller **Avbruten**. Ombokad ändrar endast
status: inga datum eller lokaler ändras, inga mejl skickas och inget nytt
godkännande begärs. En ombokad bokning kan därefter slutföras eller avbrytas.
Administratören kan även avbryta en bokning som inväntar kundens svar. Övriga
statusövergångar avvisas på servern.

Administratörsöversikten har filter för samtliga statusar och prioriterar nya
förfrågningar och önskemål om nytt datum. Detaljvyerna skiljer på ursprungliga
önskemål, aktuellt förslag och fastställda uppgifter. Administrativa anteckningar
är interna och visas inte i kundportalen.

### Migration och historik

`create_database()` lägger till `faststalld_lokal` och tabellerna
`booking_proposals` och `booking_history` med tillhörande index. Migrationen körs
vid appstart och kan upprepas i både PostgreSQL och SQLite. Bokningsnummer,
statusvärden, konton och befintliga uppgifter behålls. Varje äldre bokning får en
enda baslinjehändelse som tydligt anger att historiken börjar vid migreringen;
tidigare händelser eller kundsvar återskapas inte i efterhand.

Varje nytt förslag har eget ID, föreslagna uppgifter, tillstånd, skapandetid,
eventuell utskickstid och kundens svarstid. Förslagen skrivs inte över.
Statusändringar sparar tid, aktör och anteckning i samma transaktion som
ändringen. Bokningslås och ett unikt index för utestående förslag skyddar mot
konkurrerande svar och dubbla formuläranrop.

### E-post och leveransstatus

Datumförslag använder samma `smtp_email`, `smtp_password`,
`smtp_server_address` och `smtp_port` som tidigare, med STARTTLS. Länkarna använder
den fasta adressen `https://kursplan.se` och påverkas inte av HTTP-anropets
Host-header. `send_mail=0` stänger av utskick; detta visas uttryckligen i
administratörsvyn och räknas inte som skickad e-post.

Förslaget sparas först och kan besvaras i kundportalen även om SMTP misslyckas.
Ett misslyckat utskick markeras som misslyckat och kan försökas igen i
bokningsdetaljen utan att ett nytt datumförslag skapas. Endast ett samtidigt
anrop får försöka skicka ett visst förslag. SMTP-uppgifter och felinnehåll skrivs
inte till loggen. Testerna använder avstängd e-post eller mockad SMTP.

Om processen avbryts mitt under ett SMTP-anrop kan leveransstatus bli kvar som
**Utskick pågår**. Kontrollera då leveransen hos e-postleverantören före ett
manuellt nytt försök; automatisk omsändning skulle kunna ge dubbletter. SMTP och
databastransaktionen kan inte tillsammans garantera exakt ett mottaget mejl.
Ingen produktionsdatabas eller driftsättning ändras när testerna körs.

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
python -m pytest -n auto
```

`pytest-xdist` ingår i beroendena. Med `-n auto` körs testerna parallellt och
antalet testprocesser väljs automatiskt. Du kan även köra `pytest -n auto` i
den aktiverade virtuella miljön. Utelämna `-n auto` för att köra sekventiellt.

Efter installationen startar pytest en tillfällig PostgreSQL 18-instans per
testprocess automatiskt. Det behövs varken Docker, en `.env`-fil eller en redan
körande databas. Varje test får en egen tom databas, som tas bort efter testet.
När testkörningen är klar stoppas instanserna och deras data tas bort. Kör som
vanlig användare.

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
och kör tester parallellt med `pytest -n auto`, avstängd e-post, ett tillfälligt
privat Docker-nätverk och en egen PostgreSQL-databas. Om ett test misslyckas kör
den befintliga webbversionen vidare. Om aktuell commit redan körs kontrollerar skriptet
databasanslutningen och avslutar.

Efter godkända tester flyttar skriptet fram `main`, driftsätter den testade
webbimagen i det befintliga Compose-projektet. Skriptet inväntar den interna
hälsokontrollen och verifierar databasanslutningen. Vid fel under driftsättningen
försöker skriptet återställa föregående webbimage.

Granska ändringar i `docker-compose.yml` separat innan de införs i produktion.
Skriptet avbryter uppdateringen när den filen skiljer sig. Säkerhetskopiera
databasen före schemaändringar och hantera eventuell återställning av en
databasmigration separat.

Servern behöver Linux, Git, Docker och Docker Compose med stöd för `up --wait`.
Använd Python 3.11.4 eller nyare, eller en distributionsversion som har
`tarfile.data_filter` via en säkerhetsuppdatering. Kör på `main` i den befintliga
driftsmappen med dess `.env` och en fungerande Compose-stack. Med `DEV_MODE`
aktiverat fortsätter driftsättningen med en varning om att appen kan falla tillbaka
till tillfällig SQLite-lagring om den konfigurerade databasen inte kan nås.
Serverkontot behöver åtkomst till Docker, Git-repot och de register som bygget
och testerna använder.
Kommandot kör kontrollerna på servern med Docker. GitHub Actions granskar
ändringar i pull requests. Se [driftkommandona](Commands.md) för första hämtningen.
