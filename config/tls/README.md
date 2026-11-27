# Сертификаты для рыночных данных

GitHub Actions дополняет стандартный набор CA сертификатами НУЦ Минцифры для заданий,
которые загружают данные MOEX ISS и Т-Инвест. Общесистемное хранилище runner не меняется.
Проверки имени сервера и цепочки остаются включёнными.

Файлы получены с CDN Госуслуг `https://gu-st.ru/content/lending/`. Имена на CDN оканчиваются
на `_pem.crt`. MOEX описывает переход на эту цепочку в [инструкции](https://www.moex.com/kak-sohranit-dostup-k-servisam-birzhi).

| Сертификат | SHA-256 отпечаток DER |
|---|---|
| russian_trusted_root_ca.pem | D26D2D0231B7C39F92CC738512BA54103519E4405D68B5BD703E9788CA8ECF31 |
| russian_trusted_sub_ca.pem | BBBDE2103E790B999EC62BD03CF625A5A2E7C316E10AFE6A490EEDEAD8B3FD9B |
| russian_trusted_sub_ca_2024.pem | 2155785036C900DBB5F1BB2A1569C80C55595BD6BF94867A29BBDDBC7D88A3F2 |

При обновлении сверяйте источник и отпечаток через `openssl x509 -noout -fingerprint -sha256`.
