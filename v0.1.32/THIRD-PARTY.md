# Сторонние компоненты

MeshVPS запускает сетевые компоненты отдельными процессами. Тексты лицензий находятся в `licenses`, точные закреплённые версии и изменения — в `Sources/network-build.json` Windows-комплекта или `assets/network-build.json` исходников.

* **MeshVPS Engine 0.1.32**, производная от sing-box 1.14.4, GPLv3 или более поздняя версия с дополнительным условием автора о названии производных работ. Базовый исходный код — https://github.com/SagerNet/sing-box/tree/c005f35300ce76427dc285481ec5695f5b179cd2 . Изменены версии зависимостей в go.mod/go.sum, имя CLI и вывод версии. Сборка Go 1.27.4, `CGO_ENABLED=0`, теги `with_quic,with_gvisor`, Windows/Linux amd64. Название производной — MeshVPS Engine; приложение не связано с авторами исходного проекта. Лицензии `sing-box-notice.txt` и `GPL-3.0.txt` сохраняются.
* **Yggdrasil 0.5.15 с обновлёнными зависимостями MeshVPS 0.1.32**, LGPLv3 с исключением. Базовый код — https://github.com/yggdrasil-network/yggdrasil-go/tree/2c7f050fc5398ecd8ac5f77e9bb5bd91aa2e9e35 . Изменены go.mod/go.sum, сведения о сборке, используется Go 1.27.4. Протокол Yggdrasil и формат ключей сохранены. Лицензия с исключением включена как `Yggdrasil-LGPLv3.txt`.
* **Wintun 0.14.1**, неизменённая DLL из официального Windows-пакета Yggdrasil, побайтно совпадающая с amd64 DLL официального архива Wintun. Проект https://www.wintun.net/ . Лицензии готовой DLL и исходников — `Wintun-binary-LICENSE.txt` и `Wintun-LICENSE.txt`.
* **SSH.NET 2026.0.0**, MIT. Исходники https://github.com/sshnet/SSH.NET/tree/2026.0.0 . Также используются BouncyCastle.Cryptography 2.7.0 (MIT) и Microsoft.Extensions.Logging.Abstractions 8.0.3 (MIT).
* **.NET 10.0.15**, автономный Windows Desktop runtime Microsoft, MIT и сторонние уведомления. Версия runtime проверяется при упаковке. Проекты https://github.com/dotnet/runtime и https://github.com/dotnet/wpf .

## Соответствующие исходники сетевых компонентов

`Sources/network-sources.tar.xz` Windows-комплекта и `assets/network-sources.tar.xz` исходного комплекта содержат точные исходники обоих компонентов, изменённые go.mod/go.sum, исходники пакетов зависимостей, используемых шестью поддерживаемыми сборками, и их лицензии. Дополнительные upstream-функции и платформы могут потребовать загрузки остальных зависимостей по go.sum. Автономная пересборка поддерживаемых вариантов выполняется `rebuild-network-tools.py` с точной версией Go из манифеста.

Архив также публикуется в https://github.com/3ont666/meshvps-releases/tree/main/v0.1.32 . Его SHA-256 указан в `network-build.json`. Корневые лицензии и уведомления зависимостей сохранены внутри архива. Компилятор Go распространяется отдельно авторами: https://go.dev/dl/ .

Контрольные суммы готовых файлов зафиксированы в `tool-sha256.json`. Проверка известных уязвимостей выполняется отдельно через `check-network-security.ps1`; вывод сканера и хеши проверенных файлов входят в каталог `Security` Windows-архива.
