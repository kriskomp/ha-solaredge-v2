# Changelog

Numeracja wersji: `RRRR.MM.DD.N` (jak w oryginalnym repozytorium). Wydanie na GitHubie
powstaje automatycznie, gdy wersja w `manifest.json` zmieni się na gałęzi `main`, a jego opis
to odpowiednia sekcja tego pliku.

## 2026.10.18.1

Pierwsze wydanie. Integracja SolarEdge Monitoring API v2 (Site Access) przygotowana na
podstawie testów na żywym API, jako następca wbudowanej integracji `solaredge` (API v1
wyłączane w listopadzie 2026).

### Odczyty i kredyty
- **1 kredyt na odczyt.** Jedno zapytanie o telemetrię falownika (kroki 15-minutowe od
  północy) daje moc, energię dziś, szczyt dnia oraz napięcie, prąd i częstotliwość.
- **Energia w miesiącu, roku i całkowita** kosztuje 1 kredyt na dobę.
- **Odczyty tylko w dzień** (od wschodu do zachodu słońca, plus odczyt końcowy). Do wyboru
  tryb stały (interwał dobrany pod najdłuższy dzień roku) albo adaptacyjny.
- **Własny licznik kredytów** z resetem w dniu cyklu rozliczeniowego i korektą z portalu
  (`solaredge_v2.set_credits_used`). Zapytanie jest liczone przed wysłaniem.
- `homeassistant.update_entity` nie wywołuje API. Odczyt na żądanie: `solaredge_v2.refresh`.
- „Aktualna moc” pokazuje 0 W, gdy od ponad 60 minut nie ma danych (noc, wyłączony falownik).

### Konfiguracja i autoryzacja
- **Home Assistant sam odbiera zgodę SolarEdge** pod adresem
  `https://<adres-HA>/api/solaredge_v2/callback`, bez przeklejania. `http://localhost/callback`
  z wklejaniem adresu zostaje jako wariant awaryjny.
- **Zmień konfigurację** (Client ID, Client Secret, Redirect URL) bez usuwania integracji.
- Ponowna autoryzacja z podpowiedzią adresu HA. Przypomnienie na 30 dni przed końcem
  24-miesięcznej zgody.
- Opcje z podglądem wyliczonego interwału przed zapisem.

### Przejście z wbudowanej integracji
- Kreator proponuje **przejęcie sensorów** wbudowanej integracji `solaredge` (aktualna moc,
  energia dziś, w miesiącu, w roku i całkowita) z tym samym `entity_id` i całą historią.
  Stara integracja zostaje wyłączona, nie usunięta.

### Niezawodność
- Pamięć podręczna w `.storage`: restart i przeładowanie nie zużywają kredytów.
- Uszkodzony plik `.storage` nie blokuje startu, a błąd zapisu nie przerywa odczytów.
- Rotowany refresh token zapisywany jest natychmiast. Gdy integracja nie odpytuje API (np. po
  wyczerpaniu kredytów), token jest odświeżany profilaktycznie, żeby nie wygasł.

Fork [trooperthorn/ha_int_solaredge](https://github.com/trooperthorn/ha_int_solaredge) (MIT):
z oryginału pochodzą klient API i modele odpowiedzi.
