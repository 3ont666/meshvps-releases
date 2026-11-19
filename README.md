# MeshVPS — серверный установщик

Установщик 0.1.21 предназначен для Debian и Ubuntu x86_64 с systemd. Если SSH до VPS недоступен, откройте консоль VPS в панели хостинга. Серверу нужен исходящий HTTPS до GitHub и возможность скачать системные зависимости.

Войдите в консоли под root или выполните `sudo -i`. Затем выполните блок:

```bash
workdir=$(mktemp -d) && cd "$workdir" &&
curl -fsSL 'https://raw.githubusercontent.com/3ont666/meshvps-releases/main/v0.1.21/meshvps-install.sh' -o meshvps-install.sh &&
printf '%s\n' '383f98ead3ffc2f3ee94a2353c06ce3c748b8eb5b9db44f3ee7bd74d35e581a8  meshvps-install.sh' | sha256sum -c - &&
bash meshvps-install.sh
```

После установки скрипт выведет приватный код `meshvps://…`. Вставьте его в окно **«Импорт кода»** на ПК и нажмите **«Подключить»**. Храните код как пароль.

Для проверки файла вручную используйте [контрольную сумму SHA‑256](v0.1.21/meshvps-install.sh.sha256).
