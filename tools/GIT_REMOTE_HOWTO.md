# Третья копия истории: приватный git-remote (GitHub)

Сейчас история живёт в двух местах: диск этой машины и `backup/repo-20261111.bundle`
(флешка). Облако добавляет независимую копию — диск и флешка могут погибнуть
вместе, репозиторий на GitHub — нет.

Репозиторий ~200 МБ (модель Vosk 87 МБ внутри, крупнейший файл 31,5 МБ —
лимиты GitHub соблюдены, LFS не нужен). Репозиторий делаем **приватным**:
в коде есть специфика объекта и контрактов.

## Шаги (5 минут, нужен ваш аккаунт на github.com)

1. Создать пустой приватный репозиторий:
   github.com → «+» → **New repository** → имя `getac-inventory` →
   **Private** → БЕЗ README/.gitignore/license (репозиторий уже есть) → Create

2. На этой машине, в папке проекта:

   ```bat
   git remote add origin https://github.com/ВАШ_ЛОГИН/getac-inventory.git
   git push -u origin main
   ```

3. Проверка: страница репозитория в браузере показывает код, 5 коммитов,
   файлы `inventory/`, `tests/`, `tools/`, `ЧЕК_ЛИСТ_ВЫЕЗД.md`.

4. Windows может спросить логин/пароль — GitHub с августа 2021 принимает
   **Personal Access Token** вместо пароля: Settings → Developer settings →
   Tokens (classic) → Generate new token → scope `repo`. Вставить токен
   в поле пароля один раз — Windows запомнит (Credential Manager).

## Дальше

- После значимых правок: обычный `git commit` + `git push` (одно слово).
- Восстановление на новой машине:
  `git clone https://github.com/ВАШ_ЛОГИН/getac-inventory.git` — и
  `python tools/build_exe.py` соберёт поставку с нуля.
- Бандл на флешке остаётся офлайн-страховкой на случай отсутствия интернета.

## Альтернатива: GitLab (если нет GitHub)

То же самое: gitlab.com → New project → Private →
`git remote add origin https://gitlab.com/ВАШ_ЛОГИН/getac-inventory.git`.
