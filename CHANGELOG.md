# Changelog

## 0.1.0 – 16.10.2026

- Pierwsza wersja oparta na testach na żywym API v2 (logowanie przez wklejenie adresu,
  1 kredyt na odczyt, licznik kredytów, odczyty tylko w dzień, pamięć podręczna w `.storage`).

## 0.2.0 – 16.10.2026

- Przejęcie sensorów wbudowanej integracji `solaredge` (ten sam `entity_id` i historia),
  proponowane w kreatorze; stara integracja zostaje wyłączona.

## 0.2.1 – 18.10.2026

- Zapytanie do API liczone jest przed wysłaniem i zapisywane w ciągu sekundy, więc zapytania
  przerwane przez timeout też trafiają do licznika, a awaria HA w trakcie odczytu nie gubi kredytów.
- Uszkodzony albo nieczytelny plik `.storage` nie blokuje startu integracji: każda część cache
  (token, licznik, odczyty) jest wczytywana osobno, a błędna zostaje pominięta.
- Błąd zapisu `.storage` nie przerywa odczytów, tylko trafia do logu.

## 0.3.0 – 18.10.2026

- Home Assistant sam odbiera przekierowanie z SolarEdge pod adresem
  `https://<adres-HA>/api/solaredge_v2/callback`, więc nie trzeba niczego przeklejać.
  Kreator podpowiada adres zewnętrzny HA. `http://localhost/callback` z wklejaniem zostaje jako
  wariant awaryjny. Dotyczy także ponownej autoryzacji.
- **Zmień konfigurację** (Reconfigure): zmiana Client ID, Client Secret i Redirect URL bez
  usuwania integracji. Przy ponownej autoryzacji kreator pyta o Redirect URL i podpowiada
  adres HA zamiast zapisanego `localhost`.
- `homeassistant.update_entity` nie wywołuje już API (wcześniej każde wywołanie kosztowało
  kredyty). Odczyt na żądanie: `solaredge_v2.refresh`.
