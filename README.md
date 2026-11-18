# Kursplan

## Kör appen i utvecklingsläge

Sätt `DEV_MODE=true` i `.env` och starta appen med `python app.py`. Det aktiverar debugloggar och skapar en tom SQLite-fil i systemets temporära katalog. Sökvägen visas i startloggen. Filen tas bort när appen avslutas normalt, så varje start börjar med en ny databas.

Ange `test_email` och `test_password` i `.env` om du vill att appen skapar en administratör vid start. Sätt `send_mail=0` om du vill testa bokningar utan att skicka bekräftelser.

Med `DEV_MODE=false` eller utan inställningen använder appen PostgreSQL via `db_path`.

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
python -m pytest
```

Efter installationen startar pytest en tillfällig PostgreSQL 18-instans automatiskt. Det behövs varken Docker, en `.env`-fil eller en redan körande databas. Varje test får en egen tom databas, som tas bort efter testet. När testkörningen är klar stoppas instansen och dess data tas bort. Kör som vanlig användare.

Om den inbyggda PostgreSQL-versionen inte finns för din plattform kan du installera `requirements.txt` och sätta `TEST_DATABASE_URL` till en separat PostgreSQL-testserver. Testanvändaren behöver rätt att skapa databaser. Testerna skapar och tar bort sina egna databaser även i det läget.
