# Driftkommandon

Kör om det misslyckade jobbet i GitHub Actions-flödet **Deploy production** för
att försöka driftsätta samma godkända CI-image igen. Committen behöver ligga
överst på `main` och CI-artefakten behöver finnas kvar. Om artefakten har gått
ut, kör om **Kursplan CI** för den aktuella committen på `main`.

Driftsättaren sparar den föregående webbimagen under den lokala taggen
`kursplan:rollback`. Vid fel visar deployjobbet om återställningen och den
publika hälsokontrollen lyckades. Se [README.md](README.md) för hur
produktionsflödet konfigureras.

Installera programmets beroenden i den befintliga virtuella miljön vid behov.

```sh
venv/bin/python -m pip install -r requirements.txt
```
