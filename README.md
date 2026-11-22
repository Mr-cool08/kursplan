# Kursplan

## Inloggningens giltighetstid

Kunder och administratörer förblir inloggade i **60 dagar från senaste besöket**.
Varje anrop till webbplatsen med en giltig inloggningscookie förnyar tiden med
60 dagar, även vid besök på en offentlig sida. Cookien sparas med ett
utgångsdatum så att inloggningen finns kvar när webbläsaren stängs och öppnas
igen. Efter 60 dagar utan aktivitet krävs en ny inloggning. **Logga ut**
avslutar inloggningen direkt i webbläsaren.

Inställningen gäller båda rollerna automatiskt, även redan inloggade konton
när de besöker webbplatsen igen med en giltig cookie. Ingen databasmigrering
eller ny miljövariabel behövs. Behåll samma `app_secret_key` mellan omstarter
för att befintliga inloggningscookies ska fortsätta vara giltiga. Om användaren
raderar sina cookies eller använder privat surfning kan en ny inloggning
behövas tidigare.

## E-postverifiering och lösenord

Nya kundkonton skapas via `/signup` och måste verifiera sin e-postadress innan
de kan logga in. Efter registreringen visas inloggningssidan direkt, med en
banderoll som berättar att verifieringsmejlet har skickats. Registreringen
skickar ett mejl med en engångslänk som gäller
i 24 timmar. Kunden öppnar länken och trycker på **Verifiera min e-postadress**.
Därefter loggar kunden in som vanligt. Att bara öppna länken förbrukar den inte,
så automatiska länkkontroller i e-posttjänster aktiverar inte kontot.

Knappen **Skicka verifieringsmejlet igen** i inloggningssidans banderoll använder
e-postadressen och lösenordet i inloggningsformuläret för att begära en ny länk.
Kunden stannar på inloggningssidan även efter omskick. Högst en länk skickas
per minut och konto. En ny länk
ersätter den tidigare. Fel vid utskick lämnar kontot overifierat och tillåter
ett nytt försök; gränssnittet visar även om utskicken är avstängda.

Mejlen använder `smtp_email`, `smtp_password`, `smtp_server_address` och
`smtp_port`, med certifikatkontrollerad STARTTLS. Sätt `PUBLIC_BASE_URL` till
webbplatsens betrodda adress. Om inställningen lämnas tom använder drift
`https://kursplan.se`, medan `DEV_MODE=true` använder `http://127.0.0.1:<app_port>`
(port 80 om `app_port` saknas). Med exempelvis `app_port=4753` leder mejlet
till `http://127.0.0.1:4753/verify-email` i den lokala appen. Länkarna byggs
aldrig från besökarens Host-header.

Om utvecklingsappen nås genom en proxy, Docker-portmappning, en annan enhet
eller `flask run --port`, ange den faktiska adressen uttryckligen i
`PUBLIC_BASE_URL`. Vanlig HTTP tillåts bara för localhost i utvecklings- eller
testläge; använd en betrodd HTTPS-adress för andra värdar. Starta om appen när
konfigurationen ändras och begär en ny verifieringslänk; tidigare mejl ändras
inte. Lokala länkar fungerar så länge samma utvecklingsdatabas finns kvar.

Med `send_mail=0` skickas inga mejl och
verifieringskravet kvarstår. De automatiskt skapade utvecklingskontona är
redan verifierade och kan användas för lokal testning utan utskick.

Alla nya lösenord lagras som envägshashar med **Argon2id**, unikt slumpmässigt
salt och RFC 9106:s profil med 64 MiB minne, tre iterationer och parallellism
fyra. Lösenord går inte att dekryptera. Verifieringslänkarnas hemliga token
lagras endast som SHA-256-hashar och raderas efter användning.

Vid nästa normala appstart migreras databasen automatiskt och utan att
radera konton eller bokningar. Befintliga konton behåller sin åtkomst och
markeras som verifierade. Lösenord som tidigare låg i klartext omvandlas till
Argon2id. Befintliga scrypt-/PBKDF2-hashar uppgraderas efter nästa lyckade
inloggning. Migreringen är idempotent och fungerar med både PostgreSQL och
SQLite. Första starten kan ta längre tid om många klartextlösenord behöver
hashas; gamla databasbackuper kan fortfarande innehålla den tidigare lagringen.

Inför driftsättning: installera beroendena från `requirements.txt` (eller bygg
om webbcontainern), kontrollera SMTP-inställningarna och den publika adressen,
och ta en normal databasbackup enligt ordinarie driftsrutin före uppdateringen.
Ingen manuell databasreset krävs.

## Kör appen i utvecklingsläge

Sätt `DEV_MODE=true` i `.env` och starta appen med `python app.py`. Appen försöker först använda den beständiga PostgreSQL-databasen i `db_path`, med fem sekunders anslutningstimeout. Då finns konton och bokningar kvar mellan omstarter, och ändringar sparas i den konfigurerade databasen. Debugloggning styrs separat av `DEBUG` enligt avsnittet om loggning nedan.

Om `db_path` saknas, är ogiltig eller databasen inte går att ansluta till faller appen tillbaka till en tom SQLite-fil direkt i projektets rot, bredvid `app.py`. Filen heter `kursplan-dev-<slumpmässigt namn>.db` och får ett unikt namn vid varje start. Startloggen visar en varning; med `DEBUG=true` visas även filens sökväg. Filen tas bort när appen avslutas normalt, så reservdatabasen börjar tom vid varje start. Fel i exempelvis databasens schema avbryter fortfarande starten och döljs inte av reservlösningen.

### Inloggning i utvecklingsläget

I utvecklingsläget skapas automatiskt två konton. Båda loggar in via `/login`; administratören kommer till administratörsöversikten och standardanvändaren till kundens dashboard.

| Konto | Förvald e-post | Inställningar i `.env` | Nyckel i kontofilen |
| --- | --- | --- | --- |
| Administratör | `admin@example.invalid` | `test_email`, `test_password` | `admin` |
| Standardanvändare | `user@example.invalid` | `dev_user_email`, `dev_user_password` | `user` |

Det finns inget gemensamt standardlösenord. Om ett lösenord varken har angetts i miljön eller sparats tidigare genereras ett separat slumpmässigt lösenord för kontot. Inställningar i miljön, exempelvis från `.env`, har företräde framför sparade uppgifter och förvalda adresser.

Inloggningsuppgifterna sparas i `.dev-accounts.json` bredvid `app.py`. Filen ignoreras av Git och återanvänds mellan omstarter, även när SQLite-databasen skapas om. Med `DEBUG=true` visar startloggen filens sökväg utan att skriva ut lösenorden. Dela eller versionshantera inte filen. Nya utvecklingskontons lösenord lagras hashade i databasen.

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

Med `DEV_MODE=true` får den vanliga utvecklingsanvändaren **16 exempelbokningar**
i en ny databas. De fyra ursprungliga exemplen visar exakt datum, datumintervall,
egen utbildning, frivillig ort och olika lokalval. Tolv ytterligare exempel
visar hela bokningsflödet med sparade datumförslag, kundsvar, fastställda uppgifter
och historik. De syns både i kundens dashboard och administratörsöversikten.
Inga mejl skickas när exemplen skapas.

De tolv nya bokningarnas utbildningsnamn börjar med **Exempel:**. Varje bokning
har en offentlig instruktion märkt **Exempeldata** i bokningshistoriken och en
intern anteckning som bara administratören ser. Förslagen och kundsvaren är
simulerade exempel. De öppna bokningarna kan fortsätta hanteras med de vanliga
knapparna.

| Exempel efter prefixet `Exempel:` | Utgångsläge | Prova eller kontrollera |
| --- | --- | --- |
| Registrera och förbered datumförslag | Registrerad | Öppna som administratör, fyll i datum, ort och lokal och välj **Granska förslaget** innan det skickas. |
| Acceptera datumförslag | Inväntar kundens svar | Välj **Acceptera förslaget** som kund. Bokningen blir accepterad och förslagets datum, ort och lokal fastställs. |
| Begär nytt datum | Inväntar kundens svar | Välj **Begär nytt datum** som kund. Administratören kan sedan skicka ett nytt förslag inom samma bokning. |
| Avbryt datumförslag | Inväntar kundens svar | Välj **Avbryt bokningen** som kund. Förslaget avaktiveras och bokningen finns kvar som avbruten. |
| Försök skicka e-post igen | Inväntar kundens svar, simulerat misslyckat utskick | Välj **Försök skicka e-post igen** som administratör. Kunden kan redan svara i portalen. |
| Nytt datum önskas | Nytt datum önskas | Se kundens tidigare svar och skicka ett nytt datumförslag som administratör. |
| Flera datumförslag | Inväntar kundens svar | Jämför det avböjda och det aktuella förslaget. Endast det aktuella kan besvaras. |
| Bekräftad utbildning | Accepterad | Jämför önskemålen med de fastställda uppgifterna. Administratören kan markera bokningen som slutförd, ombokad eller avbruten. |
| Ombokad utbildning | Ombokad | Kontrollera att de fastställda uppgifterna finns kvar. Administratören kan slutföra eller avbryta bokningen. |
| Slutförd utbildning | Slutförd | Se ett utbildningsdatum i det förflutna och den bevarade bokningshistoriken. |
| Avbruten av kunden | Avbruten | Se kundens avbrytande svar på datumförslaget. |
| Avbruten av administratören | Avbruten | Se att förslaget är avaktiverat utan att något kundsvar har registrerats. |

För att prova exemplen i webbläsaren:

1. Sätt `DEV_MODE=true` och `send_mail=0` i `.env` och starta appen med
   `python app.py`. Med avstängd e-post kan även nya förslag och omförsök provas
   utan riktiga utskick.
2. Logga in via `/login` med utvecklingsanvändaren från `.dev-accounts.json`.
   Öppna **Besvara datumförslag** eller **Visa bokning** och läs instruktionen i
   bokningshistoriken. De tre separata svarsexemplen låter dig prova alla
   kundsvaren på varsin bokning.
3. Logga ut och logga in med utvecklingsadministratören. Använd filtren i
   `/admin/`, exempelvis **Inväntar kundens svar**, **Nytt datum önskas** eller
   **Slutförd**, och välj **Öppna**. Där finns datumförslag, tillåtna
   statusändringar och formuläret för interna anteckningar.
4. Öppna **Händelselogg** i administrationen för att följa bokningshändelserna.
   Logga in som kund igen för att se resultatet av ändringarna; interna
   administratörsanteckningar visas bara för administratörer.

`DEBUG=true` styr endast loggningen. Det skapar inga exempelbokningar när
`DEV_MODE=false`.

De fyra ursprungliga exemplen använder riktiga organisationsnummer så att företagsnamnen kan hämtas
med adminsidans gratis API:

| Exempelutbildning | Företag | Organisationsnummer |
| --- | --- | --- |
| HLR | IKEA of Sweden AB | 556074-7551 |
| Anpassad säkerhetsutbildning | Aktiebolaget Volvo | 556012-5790 |
| Heta arbeten | Telefonaktiebolaget LM Ericsson | 556016-0680 |
| Första hjälpen | H & M Hennes & Mauritz AB | 556042-7220 |

Bokningarna, kontaktpersonerna och lokaluppgifterna är fortfarande fiktiva.
Alla exempel tillhör utvecklingsanvändaren och använder dennes e-postadress;
inga verkliga kontaktuppgifter för bolagen läggs till. Utvecklingskontots
organisationsnummer ändras inte. Uppstarten behöver inte kontakta API:et;
företagsnamnen hämtas när administratören öppnar sidan.

Exemplen skapas en gång per utvecklingsanvändare och databas. Tabellen
`dev_booking_seeds` håller reda på de fyra ursprungliga exemplen. Den nya
tabellen `dev_booking_scenario_seeds` håller reda på varje ytterligare scenario
per användare, så de tolv nya exemplen läggs även till i en befintlig
utvecklingsdatabas. Befintliga bokningar och konton bevaras. Omstarter ändrar
inte exempel som du har redigerat eller avbokat och återställer inte borttagna
exempel. En ny tillfällig SQLite-databas får alla exempel igen. Markörtabellerna
skapas automatiskt endast i utvecklingsläget; med `DEV_MODE=false` skapas
varken exempelbokningar eller dessa tabeller.

Vid uppstart med `DEV_MODE=true` får befintliga bokningar också tre historikhändelser
märkta **Exempeldata**: en offentlig granskning av utvecklingsadministratören,
en beskrivning som passar bokningens aktuella status och en intern anteckning.
Kunden ser administratörens namn i den offentliga historiken; den interna
anteckningen visas bara för administratörer. Bokningsuppgifter, datumförslag
och tidigare historik bevaras, och inga mejl skickas. Markörtabellen
`dev_booking_history_seeds` gör att varje bokning kompletteras en gång, även
i en befintlig utvecklingsdatabas. Nya manuellt skapade bokningar får
exempelhistorik vid nästa uppstart. De tolv nya scenarierna får i stället sin
sammanhängande exempelhistorik, instruktion och interna anteckning när de
skapas. `DEBUG=true` styr bara loggnivån och skapar ingen exempelhistorik när
`DEV_MODE=false`.

De riktiga organisationsnumren används för nya exempel. Tidigare skapade
exempel i en beständig databas skrivs inte om automatiskt, eftersom de kan ha
redigerats. En ny temporär utvecklingsdatabas får de uppdaterade exemplen.

Med `DEV_MODE=false` eller utan inställningen använder appen PostgreSQL via `db_path` och skapar inga utvecklingskonton. Den befintliga administratörsinställningen `test_email`/`test_password` används som tidigare.

## Företagsnamn i administrationen

Adminöversikten och administratörens bokningsdetaljer hämtar automatiskt
företagsnamnet från bokningens organisationsnummer. Uppslaget använder
[mackan.eu:s gratis JSON-API](https://mackan.eu/tools/bolagsverket/index.php),
en tredjepartstjänst som enligt leverantören hämtar data från Bolagsverket.
Ingen registrering, API-nyckel eller betald prenumeration krävs. Leverantören
anger rimlig användning utan en fast anropskvot; tillgänglighet och täckning
beror på tjänsten och dess datakälla.

Anropen görs från servern till
`https://mackan.eu/tools/bolagsverket/get_data.php?orgnr=<organisationsnummer>`.
Endast organisationsnumret skickas till tjänsten. Webbläsaren använder en
administratörsskyddad route och laddar namnet separat, så bokningssidan kan
användas även om uppslaget misslyckas. Det befintliga organisationsnumret
visas alltid. Numret kontrolleras före uppslagning; personnummer, inklusive
enskilda firmors personnummer, skickas inte till API:et.

Träffar mellanlagras i webbprocessens minne i 24 timmar och saknade företag i
en timme, med högst 256 nummer i cachen. Samtidiga uppslag serialiseras och
API-fel pausar nya anrop i en minut. API-anrop har tre sekunders nätverkstimeout.
Vid saknat företag, ogiltigt nummer eller anslutningsfel visas en förklarande
text. Företagsnamnet sparas inte i databasen och ändrar inga bokningsuppgifter.

Ingen databasmigrering eller ny miljövariabel behövs. Webbservern behöver
utgående HTTPS till `mackan.eu`. Tester använder mockade API-svar och skickar
inga kunduppgifter till den externa tjänsten.

## Svensk tid på hemsidan

Klockslag i bokningshistoriken, datumförslagen och administratörens händelselogg
visas i svensk tid (`Europe/Stockholm`). Sommar- och vintertid hanteras
automatiskt: `CEST` är sommartid och `CET` är vintertid. Även sidfotens årtal
följer svensk tid oavsett besökarens tidszon. Utbildningsdatum och datumintervall
är kalenderdatum och flyttas inte vid tidszonsomvandlingen.

Tidsstämplarna lagras fortsatt i UTC och omvandlas först när sidan visas.
Ingen databasmigrering eller ny miljövariabel behövs. Installera beroendena
från `requirements.txt`, där `tzdata` ger tidszonsdata även i Windows och
containrar utan systemets tidszonsdatabas.

## Loggning och loggar mellan omstarter

Appen skriver loggar både till konsolen och till `logs/kursplan.log` i projektets
rot. Loggfilen öppnas för tillägg vid nästa start, så tidigare händelser finns
kvar. Loggningen omfattar uppstart, HTTP-anrop, autentisering och nekad åtkomst,
bokningshändelser, databasoperationer, e-post och fel.

Varje rad har UTC-tid, nivå, logger, process-id och ett request-id när händelsen
hör till ett HTTP-anrop. Anropsloggen visar metod, route-mall, statuskod och
svarstid. Samma id returneras i `X-Request-ID`, så att ett fel i webbläsaren kan
kopplas till loggraden utan att spara kundens indata. Även `/health` loggas.

| Nivå | Användning |
| --- | --- |
| `DEBUG` | Detaljer för felsökning, exempelvis databasläsningar. Visas endast med `DEBUG=true`. |
| `INFO` | Vanliga händelser, exempelvis genomförda anrop och sparade bokningar. |
| `WARNING` | Avvisade formulär, nekad åtkomst och andra förväntade problem. |
| `ERROR` | Misslyckade operationer och oväntade fel under körning. |
| `CRITICAL` | Allvarliga fel som hindrar appen från att starta eller fortsätta. |

Inställningar i `.env`:

```dotenv
DEBUG=false
LOG_DIR=logs
LOG_MAX_BYTES=10485760
LOG_BACKUP_COUNT=5
```

Sätt `DEBUG=true` och starta om appen när mer detaljer behövs. `DEBUG` påverkar
loggningen oberoende av `DEV_MODE` och aktiverar inte Flasks interaktiva
felsökare. Övriga nivåer är aktiva även när `DEBUG=false`.

`LOG_DIR` kan vara en sökväg relativ projektroten eller en absolut sökväg.
Mappen skapas automatiskt och måste vara skrivbar för appens användare.
Standardgränsen `LOG_MAX_BYTES=10485760` är 10 MiB per fil. När gränsen nås
roteras filen till `kursplan.log.1`, därefter `.2` och så vidare. Med
`LOG_BACKUP_COUNT=5` sparas fem tidigare filer utöver den aktiva filen, omkring
60 MiB totalt. Äldre loggar raderas vid rotation; spara dem separat om en längre
historik behövs. Inställningarna ska vara positiva heltal.

Lokalt kan du följa den aktiva filen i PowerShell:

```powershell
Get-Content .\logs\kursplan.log -Tail 100 -Wait
```

Docker Compose monterar den beständiga volymen `app_logs` på `/app/logs` och
sätter alltid `LOG_DIR=/app/logs`, även om `.env` anger en annan lokal sökväg.
Loggfilerna finns därmed kvar vid omstart och när webbcontainern ersätts, så
länge samma Compose-projekt och dess volym används. Att ta bort volymen tar
också bort logghistoriken. Vid införandet av den här konfigurationsändringen
behöver webbcontainern återskapas genom den ordinarie granskade driftsättningen
så att volymen monteras. Ingen databasmigrering krävs för loggningen.

Följ konsolloggen eller läs de beständiga filerna:

```console
docker compose logs --tail=100 --follow web
docker compose exec web ls -lh /app/logs
docker compose exec web tail -n 100 /app/logs/kursplan.log
```

Konsolloggen hanteras separat av Docker; de beständiga appfilerna ligger i
`app_logs`. Loggarna innehåller händelser och teknisk diagnostik utan lösenord,
sessionsnycklar, SMTP-uppgifter, e-postinnehåll eller formulärinnehåll.
Fel loggas med undantagstyp, fil, funktion och radnummer. Rå exceptiontext,
källkod och lokala variabelvärden utelämnas eftersom de kan innehålla kunddata.
Processfel och ohanterade trådfel loggas som `CRITICAL`; normala avslut och
Docker-containerns stoppsignal loggas som `INFO`. Ett tvångsavslut eller
strömavbrott kan inte ge någon avslutningsrad.
Förvara även loggfilerna med begränsad åtkomst. Den lokala standardmappen och
roterade loggfiler ignoreras av Git och Dockerbygget.

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

Kunden får mejl vid **Inväntar ditt svar**, **Accepterad** och **Avbruten** när
e-post är aktiverad. Datumförslaget är mejlet för **Inväntar ditt svar** och
innehåller svarsalternativen. Efter kundens acceptans skickas en bekräftelse med
fastställt datum, ort och lokal. Vid avbrott skickas en avbrottsbekräftelse till
bokningens e-postadress, oavsett om kunden eller administratören avbryter.
Nekade eller upprepade statusändringar skickar inga ytterligare mejl.

Datumförslag och statusbekräftelser använder samma `smtp_email`, `smtp_password`,
`smtp_server_address` och `smtp_port` som tidigare, med STARTTLS. Länkarna använder
den fasta adressen `https://kursplan.se` och påverkas inte av HTTP-anropets
Host-header. `send_mail=0` stänger av utskick; detta visas uttryckligen i
administratörsvyn och räknas inte som skickad e-post.

Förslaget sparas först och kan besvaras i kundportalen även om SMTP misslyckas.
Ett misslyckat utskick markeras som misslyckat och kan försökas igen i
bokningsdetaljen utan att ett nytt datumförslag skapas. Endast ett samtidigt
anrop får försöka skicka ett visst förslag. SMTP-uppgifter och felinnehåll skrivs
inte till loggen. Testerna använder avstängd e-post eller mockad SMTP.

Acceptans och avbrott sparas innan statusmejlet skickas. Om utskicket misslyckas
visas ett meddelande för den som utförde åtgärden; den sparade bokningsstatusen
behålls. Statusbekräftelser har ingen automatisk omsändning eller separat
knapp för nytt leveransförsök.

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

CI skapar även `coverage.xml` med `pytest-cov` och sparar rapporten som
artefakt. Python 3.12-jobbet skickar rapporten till Codacy när GitHub-hemligheten
`CODACY_API_TOKEN` är konfigurerad med din account API-token. Se
[testguidens avsnitt om testtäckning](tests/README.md#testtäckning-och-codacy)
för inställning av token och kommandot för att skapa rapporten lokalt.

Om insamlingen av tester avbryts med `ModuleNotFoundError: No module named 'playwright'`
behöver testberoendena installeras i samma Python-miljö som kör pytest. Se
[testguiden](tests/README.md) för fullständiga installationskommandon för Windows
och Linux samt kontroll av testinsamlingen.

Om den inbyggda PostgreSQL-versionen inte finns för din plattform kan du
installera övriga testberoenden separat i samma virtuella miljö:

```sh
python -m pip install -r requirements.txt "pytest-playwright>=0.7,<1" "pytest-cov>=7,<8"
python -m playwright install chromium
```

Sätt sedan `TEST_DATABASE_URL` till en separat PostgreSQL-testserver.
Testanvändaren behöver rätt att skapa databaser. Testerna skapar och tar bort
sina egna databaser även i det läget.

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

Databastjänsten `db` måste redan vara igång och ha hälsostatus `healthy`.
`update.py` kontrollerar detta före bygg/test och igen innan webbcontainern
uppdateras. Om databasen är stoppad eller inte är frisk avbryts uppdateringen
med ett tydligt fel. Skriptet startar eller återskapar inte databastjänsten.
Kontrollera databasloggen och starta vid behov den befintliga containern med:

```sh
docker compose --env-file .env -f docker-compose.yml start --wait --wait-timeout 120 db
```

Det återanvänder den befintliga containern och dess datavolym. Kör sedan
`python3 update.py` igen för att genomföra uppdateringen.

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
