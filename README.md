# SolarEdge Monitoring API v2 dla Home Assistant

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/docs/faq/custom_repositories)
[![Test](https://github.com/kriskomp/ha-solaredge-v2/actions/workflows/test.yml/badge.svg)](https://github.com/kriskomp/ha-solaredge-v2/actions/workflows/test.yml)
![Wersja](https://img.shields.io/badge/wersja-0.2.1-blue)

Integracja Home Assistant, która czyta dane instalacji SolarEdge przez **Monitoring API v2**
(`monitoringapi.solaredge.com/v2`). Stare API V1 (`?api_key=`), z którego korzysta wbudowana
integracja `solaredge`, SolarEdge wyłącza na początku listopada 2026.

Integracja jest zaprojektowana pod **darmowy plan (2000 kredytów miesięcznie)**:

- **1 kredyt na odczyt.** Jedno zapytanie o telemetrię falownika (kroki 15-minutowe od północy)
  daje moc, energię dziś, szczyt dnia oraz napięcie, prąd i częstotliwość.
- **Odczyty tylko w dzień**, od wschodu do zachodu słońca, plus jeden odczyt końcowy po zachodzie.
- **Własny licznik kredytów**, bo API nie podaje zużycia miesięcznego. Cykl rozliczeniowy liczony
  jest od wybranego dnia miesiąca, a licznik można skorygować liczbą z portalu.
- **Restart HA nie kosztuje kredytów.** Stan zapisywany jest w `.storage`, sensory po restarcie
  od razu pokazują ostatnie wartości, a następny odczyt odbywa się o `ostatni odczyt + interwał`.
- **Następca wbudowanej integracji `solaredge`.** Kreator może przejąć jej sensory z tym samym
  `entity_id` i całą historią, więc wykresy, statystyki i panel Energia działają bez przerwy.
- **Energia w miesiącu, roku i całkowita** kosztuje 1 kredyt na dobę. Integracja liczy je jako
  bazę z API plus energię dziś.
- **Dwa tryby pracy:**
  - *stały*: jeden interwał przez cały rok, dobrany pod najdłuższy dzień;
  - *adaptacyjny*: pozostałe kredyty rozkładane na pozostałe godziny dnia w cyklu.

## Sensory

| Sensor | Koszt | Uwagi |
|---|---|---|
| Aktualna moc | w odczycie | średnia z 15 minut, ostatni przedział aktualizowany na bieżąco; **0 W**, gdy od ponad 60 min brak danych (noc, wyłączony falownik) |
| Energia dziś | w odczycie | suma 15-minutowych Wh z falownika; nadaje się do panelu Energia |
| Szczyt mocy dziś | w odczycie | atrybut `peak_time` |
| Napięcie / prąd / częstotliwość AC (na falownik) | w odczycie | |
| Energia w tym miesiącu / roku / całkowita | 1 kredyt/dobę | opcja (domyślnie włączona) |
| Otwarte alerty | 1 kredyt/dobę | opcja |
| Zużycie / import / eksport / autokonsumpcja dziś | +1 kredyt na odczyt | opcja, tylko instalacje z licznikiem |
| Magazyn energii | +1 kredyt na odczyt | opcja, eksperymentalne |
| Diagnostyka: wykorzystane, pozostałe i prognoza kredytów, interwał, następny odczyt, czas danych, wygaśnięcie zgody | 0 | |

Im więcej grup sensorów płatnych „na odczyt” włączysz, tym rzadziej integracja może odczytywać
dane. Przed zapisaniem opcji integracja pokazuje wyliczony interwał.

## Instalacja (HACS)

1. HACS → ⋮ → *Custom repositories* → `https://github.com/kriskomp/ha-solaredge-v2`, typ
   *Integration*.
2. Zainstaluj **SolarEdge Monitoring API v2** i zrestartuj Home Assistant.

Integracja używa domeny `solaredge_v2`, tak jak inne integracje oparte na API v2, więc
instaluje się tylko jedną z nich. Wbudowana integracja `solaredge` może działać obok.

## Konfiguracja

### 1. Aplikacja w konsoli deweloperskiej SolarEdge

Na <https://developer.solaredge.com> (logowanie kontem mySolarEdge):

- **Create Application**, usługa **Monitoring API**, typ dostępu **Site Access**;
- zakresy **SITE_DATA** i **DEVICE_DATA**;
- *Allowed Redirect URL*: `http://localhost/callback`. Ten adres nie musi istnieć. Konsola
  przyjmuje `http://` tylko dla `localhost`;
- zapisz **Client ID** i **Client Secret**. Secret pokazywany jest tylko raz.

Na stronie *Dashboard* sprawdź datę **Subscription start date**. Dzień tej daty to dzień
miesiąca, w którym resetują się kredyty (opcja *billing day*).

### 2. Dodanie integracji

*Ustawienia → Urządzenia i usługi → Dodaj integrację → SolarEdge Monitoring API v2*:

1. Wpisz Client ID, Client Secret i Redirect URL.
2. Kliknij link do strony zgody, zaloguj się i kliknij **Approve**.
3. Przeglądarka przejdzie na `http://localhost/callback?code=...&site_id=...` i pokaże błąd
   połączenia. Tak ma być: skopiuj **cały adres z paska** i wklej go w formularzu. Kod jest
   ważny krótko.

Dlaczego tak, a nie przez wbudowany mechanizm OAuth w HA? SolarEdge nie odsyła parametru
`state`, którego wymaga wbudowany przepływ OAuth w Home Assistant. Sprawdzone 16.10.2026.

### Przejście z wbudowanej integracji `solaredge`

Jeśli dla tej samej instalacji działa wbudowana integracja SolarEdge (API v1), kreator zaproponuje
**przejęcie jej sensorów**: aktualna moc, energia dziś, w tym miesiącu, w tym roku i całkowita.

Przejęte encje zachowują **to samo `entity_id`, nazwę, obszar, jednostkę i całą historię**
(wykresy, statystyki, panel Energia). Dashboardy i automatyzacje działają dalej bez zmian.

Wbudowana integracja zostaje **wyłączona**, nie usunięta. Usuń ją ręcznie, gdy wszystko działa.
Jeśli *Naprawy* zaproponują usunięcie statystyk starych sensorów, nie zgadzaj się.

### 3. Opcje

*Konfiguruj* na integracji: tryb pracy, grupy sensorów, limit miesięczny, dzień resetu cyklu,
rezerwa i minimalny interwał. Po zatwierdzeniu formularza integracja pokazuje, jak często
będzie odczytywać dane przy nowych ustawieniach.

## Pamięć podręczna i licznik kredytów (`.storage`)

Każda instalacja ma własny, prywatny plik `/config/.storage/solaredge_v2.<entry_id>`, w którym
zapisane są:

- **token**: aktualna para access/refresh token, zapisywana natychmiast po każdej rotacji;
- **licznik kredytów**: początek cyklu, wykorzystane kredyty i informacja, czy limit się wyczerpał;
- **odczyty**: dane instalacji i falowników, dzisiejsza moc, energia, szczyt i U/I/f, bazy
  miesiąca, roku i lat poprzednich, alerty oraz czas ostatniego odczytu.

Zasady (te same co w [kriskomp/ha_Pstryk](https://github.com/kriskomp/ha_Pstryk)):

- zapytanie jest **liczone przed wysłaniem**, a licznik trafia do pliku w ciągu sekundy, więc
  timeout ani awaria HA w trakcie odczytu nie gubią kredytów;
- licznik **zawyża zużycie raczej niż zaniża**: liczone są także zapytania zakończone błędem;
- uszkodzony plik albo jego część **nie blokuje startu**: każda sekcja wczytuje się osobno,
  a nieczytelna jest pomijana z ostrzeżeniem w logu;
- błąd zapisu trafia do logu i nie przerywa odczytów;
- plik jest usuwany razem z integracją.

## Akcje

- `solaredge_v2.set_credits_used`: ustawia lokalny licznik na liczbę z portalu
  (*Dashboard → Credit cycle*).
- `solaredge_v2.refresh`: natychmiastowy odczyt (zużywa kredyty).

## Tokeny i zgoda

- Access token ważny jest 2 h i odnawiany automatycznie.
- **Każde odświeżenie daje nowy refresh token, a stary od razu przestaje działać.** Integracja
  zapisuje nowy token natychmiast. Nie używaj tych samych danych jednocześnie w dwóch miejscach
  (np. w skrypcie testowym i w HA).
- Refresh token wygasa po 30 dniach nieużywania. Integracja odświeża go profilaktycznie,
  także wtedy, gdy kredyty się skończyły.
- Zgoda Site Access trwa maksymalnie 24 miesiące. Na 30 dni przed końcem pojawia się
  powiadomienie w *Naprawach*.

## Co ustalono testami na żywym API (16.10.2026)

- `from`/`to` muszą być typu *Instant* (`2026-10-16T05:53:19Z` albo z offsetem). Czas lokalny
  bez strefy daje HTTP 400.
- Dane 15-minutowe za cały dzień (ponad 12 h) są akceptowane dla `/power` i
  `/inverters/telemetry`.
- `resolution=MONTH` przyjmuje okno maksymalnie 3 lat, `YEAR` nie ma limitu.
- Nagłówki limitów: tylko `x-ratelimit-limit-minute` (10 na planie Free) i
  `x-ratelimit-remaining-minute`. Licznika miesięcznego nie ma.
- `GET /sites` z tokenem OAuth zwraca 403 (działa tylko z kluczem Fleet).
- API v2 nie udostępnia danych optymalizatorów ani bieżącego przepływu mocy
  (power flow to plan płatny).
- Przy wyłączonym falowniku albo w nocy przedziały 15-minutowe mają `"value": null` (sprawdzone
  18.10.2026). API v1 w takiej sytuacji pokazywało ostatnią znaną moc.

## Pochodzenie

Fork [trooperthorn/ha_int_solaredge](https://github.com/trooperthorn/ha_int_solaredge) (MIT).
Z oryginału pochodzą klient API i modele odpowiedzi. Logowanie, harmonogram, licznik kredytów,
pamięć podręczna i sensory zostały napisane od nowa.

---

## Historia zmian

Zobacz [CHANGELOG.md](CHANGELOG.md).

## English summary

Home Assistant integration for the SolarEdge Monitoring API v2 (Site Access / OAuth), built
for the free 2,000-credit plan:

- one credit per poll, with polling only in daylight;
- a local credit counter with a billing-cycle reset and a portal correction action;
- no API calls on restart or reload, because state is cached in `.storage`;
- fixed or adaptive polling interval;
- authorization by pasting the redirect URL, because SolarEdge does not return the OAuth
  `state` parameter;
- takeover of the built-in `solaredge` sensors with the same entity IDs and their full history.
