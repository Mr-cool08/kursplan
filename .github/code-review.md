# Kodgranskning i GitHub

Öppna en pull request mot `main` och begär granskning av en annan person med skrivbehörighet. Granskaren går igenom **Files changed** och kontrollerar att testerna täcker det ändrade beteendet. Använd **Request changes** när något behöver åtgärdas och **Approve** när ändringen är redo. Hantera granskningskommentarerna och kontrollera **Release gate** under **Checks** före sammanslagning. [GitHubs granskningsguide](https://docs.github.com/en/pull-requests/how-tos/review-pull-requests/approving-a-pull-request-with-required-reviews)

## Krav för `main`

När `Kursplan CI` har rapporterat **Release gate** i GitHub kan en administratör skapa en aktiv regel för `main` via **Settings → Rules → Rulesets → New branch ruleset**. Lägg till följande regler. [GitHubs guide till regeluppsättningar](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/creating-rulesets-for-a-repository)

- Kräv en pull request, minst ett godkännande och att alla granskningsdiskussioner har lösts. Aktivera **Dismiss stale pull request approvals when new commits are pushed** så att en ny kodändring granskas på nytt.
- Kräv att **Release gate** kommer från **GitHub Actions** och är godkänd. Aktivera **Require branches to be up to date before merging** så att kontrollen omfattar den aktuella versionen av `main`.
- Blockera force push och radering av `main`. Lämna listan över undantag tom så att reglerna gäller även administratörer. [GitHubs beskrivning av reglerna](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/available-rules-for-rulesets)

Bekräfta att det finns en annan person med skrivbehörighet innan godkännandekravet aktiveras. PR-författaren behöver en separat granskare med den behörigheten.
