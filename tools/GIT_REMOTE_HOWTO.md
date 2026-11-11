# Третья копия истории: приватный git-remote (GitHub) — ГОТОВО

История живёт в трёх независимых местах:

1. **Диск этой машины** — `C:\...\Таблица инвентаризации\.git`
2. **Флешка** — `backup/repo-20261111.bundle` (офлайн-копия, `git bundle verify`)
3. **GitHub (облако)** — https://github.com/alfazulu196/getac-inventory (**Private**)

Репозиторий создан 11.11.2026 через GitHub CLI (device-code авторизация),
аккаунт `alfazulu196`, токен в keyring (scopes: repo), helper настроен на
`%LOCALAPPDATA%\Programs\GitHub CLI\bin\gh.exe` (portable-копия gh v2.103.3,
добавлена в PATH пользователя). `git push` работает без ручных токенов.

## Повседневное использование

- После значимых правок: `git commit` → `git push` (одно слово, ~200 МБ
  уже в облаке, дальше уходит только дельта — секунды).
- Посмотреть состояние в браузере: `gh repo view --web`
- Проверка синхрона: `git status -sb` (показывает ↑/↓ против origin/main)

## Восстановление на новой машине

```bat
git clone https://github.com/alfazulu196/getac-inventory.git
python tools/build_exe.py    rem соберёт поставку с нуля
```

(при clone из-под чужого аккаунта — доступ по приглашению или через PAT)

## Если менять учётные данные

- Перелогиниться: `gh auth login -h github.com -p https --web`
- Перенастроить helper после переезда gh.exe: `gh auth setup-git`
- Токен живёт в keyring Windows; для clone на другой машине —
  Settings → Developer settings → Tokens (scope `repo`).

## Офлайн-страховка

Бандл на флешке обновляется после значимых коммитов:

```bat
git bundle create backup/repo-20261111.bundle --all
git bundle verify backup/repo-20261111.bundle
```

Диск + флешка + облако: любой один носитель можно потерять без ущерба.
