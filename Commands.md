# Driftkommandon

Kör från Kursplans projektkatalog på produktionsservern.

```sh
python3 update.py
```

När den nya versionen av `update.py` finns på `main`, hämta den en gång i
serverns checkout. Utgå från `main` med rena spårade filer och granska först
eventuella ändringar i `docker-compose.yml`.

```sh
git status --short --branch --untracked-files=no
git pull --ff-only origin main
```

Kör `python3 update.py` på nytt efter att ett rapporterat fel har åtgärdats.
Förutsättningar och kontroller finns i [README](README.md#uppdatera-produktionen-från-servern).
