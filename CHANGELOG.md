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
