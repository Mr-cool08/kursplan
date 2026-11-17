# Kursplan

## Kör appen i utvecklingsläge

Sätt `DEV_MODE=true` i `.env` och starta appen med `python app.py`. Det aktiverar debugloggar och skapar en tom SQLite-fil i systemets temporära katalog. Sökvägen visas i startloggen. Filen tas bort när appen avslutas normalt, så varje start börjar med en ny databas.

Ange `test_email` och `test_password` i `.env` om du vill att appen skapar en administratör vid start. Sätt `send_mail=0` om du vill testa bokningar utan att skicka bekräftelser.

Med `DEV_MODE=false` eller utan inställningen använder appen PostgreSQL via `db_path`.

## Kör databastesterna lokalt

```sh
python -m pip install -r requirements-test.txt
python -m pytest
```

Efter installationen startar pytest en tillfällig PostgreSQL 18-instans automatiskt. Det behövs varken Docker, en `.env`-fil eller en redan körande databas. Varje test får en egen tom databas, som tas bort efter testet. När testkörningen är klar stoppas instansen och dess data tas bort. Kör som vanlig användare.

Om den inbyggda PostgreSQL-versionen inte finns för din plattform kan du installera `requirements.txt` och sätta `TEST_DATABASE_URL` till en separat PostgreSQL-testserver. Testanvändaren behöver rätt att skapa databaser. Testerna skapar och tar bort sina egna databaser även i det läget.
