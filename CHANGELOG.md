# Changelog

Versions: `YYYY.MM.DD.N` (as in the original repository). A GitHub release is published
automatically when the version in `manifest.json` changes on `main`; its notes are the matching
section of this file, in English first and Polish below.

Numeracja wersji: `RRRR.MM.DD.N`. Wydanie na GitHubie powstaje automatycznie po zmianie wersji
w `manifest.json` na gałęzi `main`, a jego opis to odpowiednia sekcja tego pliku: najpierw po
angielsku, niżej po polsku.

## 2026.10.19.1

- The diagnostic sensor "Data time" is now **"Data as of"**: it shows how far the readings reach
  (end of the newest 15-minute interval, or the poll time if that interval was still filling)
  instead of the interval's start. Attributes `interval_start` and `interval_end` show the
  interval itself.

### Polski

- Sensor diagnostyczny „Czas danych” to teraz **„Dane aktualne na”**: pokazuje, do kiedy sięgają
  dane (koniec najnowszego 15-minutowego przedziału albo moment odczytu, jeśli przedział się
  jeszcze zapełniał), a nie początek przedziału. Atrybuty `interval_start` i `interval_end`
  pokazują sam przedział.

## 2026.10.18.1

First release. A SolarEdge Monitoring API v2 (Site Access) integration built from tests against
the live API, as the successor of the built-in `solaredge` integration (API v1 is switched off
in November 2026).

- **One credit per poll** (inverter telemetry in 15-minute steps gives power, today's energy,
  peak and V/I/f); energy this month / year / lifetime for one credit a day.
- **Daylight-only polling**, fixed (sized for the longest day) or adaptive interval.
- **Local credit counter** with billing-cycle reset and portal correction
  (`solaredge_v2.set_credits_used`); requests are counted before they are sent.
- `homeassistant.update_entity` does not call the API; use `solaredge_v2.refresh`.
- **Home Assistant receives the SolarEdge consent itself** at
  `https://<HA-address>/api/solaredge_v2/callback`; `http://localhost/callback` with pasting
  stays as a fallback. **Reconfigure** and re-authentication with the HA address suggested.
- **Takeover of the built-in `solaredge` sensors** with the same `entity_id` and full history.
- **`.storage` cache**: restarts and reloads cost no credits; a damaged cache does not block
  start-up; rotated refresh tokens are saved immediately and kept alive.

### Polski

Pierwsze wydanie. Integracja SolarEdge Monitoring API v2 (Site Access) przygotowana na
podstawie testów na żywym API, jako następca wbudowanej integracji `solaredge` (API v1
wyłączane w listopadzie 2026).

#### Odczyty i kredyty
- **1 kredyt na odczyt.** Jedno zapytanie o telemetrię falownika (kroki 15-minutowe od
  północy) daje moc, energię dziś, szczyt dnia oraz napięcie, prąd i częstotliwość.
- **Energia w miesiącu, roku i całkowita** kosztuje 1 kredyt na dobę.
- **Odczyty tylko w dzień** (od wschodu do zachodu słońca, plus odczyt końcowy). Do wyboru
  tryb stały (interwał dobrany pod najdłuższy dzień roku) albo adaptacyjny.
- **Własny licznik kredytów** z resetem w dniu cyklu rozliczeniowego i korektą z portalu
  (`solaredge_v2.set_credits_used`). Zapytanie jest liczone przed wysłaniem.
- `homeassistant.update_entity` nie wywołuje API. Odczyt na żądanie: `solaredge_v2.refresh`.
- „Aktualna moc” pokazuje 0 W, gdy od ponad 60 minut nie ma danych (noc, wyłączony falownik).

#### Konfiguracja i autoryzacja
- **Home Assistant sam odbiera zgodę SolarEdge** pod adresem
  `https://<adres-HA>/api/solaredge_v2/callback`, bez przeklejania. `http://localhost/callback`
  z wklejaniem adresu zostaje jako wariant awaryjny.
- **Zmień konfigurację** (Client ID, Client Secret, Redirect URL) bez usuwania integracji.
- Ponowna autoryzacja z podpowiedzią adresu HA. Przypomnienie na 30 dni przed końcem
  24-miesięcznej zgody.
- Opcje z podglądem wyliczonego interwału przed zapisem.

#### Przejście z wbudowanej integracji
- Kreator proponuje **przejęcie sensorów** wbudowanej integracji `solaredge` (aktualna moc,
  energia dziś, w miesiącu, w roku i całkowita) z tym samym `entity_id` i całą historią.
  Stara integracja zostaje wyłączona, nie usunięta.

#### Niezawodność
- Pamięć podręczna w `.storage`: restart i przeładowanie nie zużywają kredytów.
- Uszkodzony plik `.storage` nie blokuje startu, a błąd zapisu nie przerywa odczytów.
- Rotowany refresh token zapisywany jest natychmiast. Gdy integracja nie odpytuje API (np. po
  wyczerpaniu kredytów), token jest odświeżany profilaktycznie, żeby nie wygasł.

Fork [trooperthorn/ha_int_solaredge](https://github.com/trooperthorn/ha_int_solaredge) (MIT):
z oryginału pochodzą klient API i modele odpowiedzi.
