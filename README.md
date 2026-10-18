# SolarEdge Monitoring API v2 dla Home Assistant

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
- **Energia w miesiącu, roku i całkowita** kosztuje 1 kredyt na dobę. Integracja liczy je jako
  bazę z API plus energię dziś.
- **Dwa tryby pracy:**
  - *stały*: jeden interwał przez cały rok, dobrany pod najdłuższy dzień;
  - *adaptacyjny*: pozostałe kredyty rozkładane na pozostałe godziny dnia w cyklu.

## Sensory

| Sensor | Koszt | Uwagi |
|---|---|---|
| Aktualna moc | w odczycie | średnia z 15 minut, ostatni przedział aktualizowany na bieżąco |
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
- *Allowed Redirect URL(s)*, jeden albo oba adresy:
  - **`https://<adres-twojego-HA>/api/solaredge_v2/callback`**: Home Assistant sam odbierze
    przekierowanie (zalecane). Potrzebny jest adres HTTPS Home Assistanta, który otwiera Twoja
    przeglądarka, np. adres zewnętrzny. Konsola przyjmuje tylko HTTPS z zarejestrowaną domeną;
  - `http://localhost/callback`: wariant awaryjny, adres z przeglądarki wklejasz ręcznie;
- zapisz **Client ID** i **Client Secret**. Secret pokazywany jest tylko raz.

Na stronie *Dashboard* sprawdź datę **Subscription start date**. Dzień tej daty to dzień
miesiąca, w którym resetują się kredyty (opcja *billing day*).

### 2. Dodanie integracji

*Ustawienia → Urządzenia i usługi → Dodaj integrację → SolarEdge Monitoring API v2*:

1. Wpisz Client ID i Client Secret. Pole Redirect URL jest wypełnione adresem zewnętrznym HA
   (*Ustawienia → System → Sieć*) z końcówką `/api/solaredge_v2/callback`. Sprawdź, czy taki
   sam adres jest w aplikacji SolarEdge.
2. **Adres HTTPS HA:** otworzy się okno SolarEdge. Zaloguj się, kliknij **Approve**, a Home
   Assistant sam odbierze odpowiedź i dokończy konfigurację. Okno możesz zamknąć.
3. **`http://localhost/callback`:** po **Approve** przeglądarka pokaże błąd połączenia. Skopiuj
   **cały adres z paska** i wklej go w formularzu. Kod jest ważny krótko.

Dlaczego integracja ma własny adres zwrotny zamiast wbudowanego mechanizmu OAuth w HA?
SolarEdge nie odsyła parametru `state`, którego wymaga wbudowany przepływ OAuth (i
my.home-assistant.io). Sprawdzone 16.10.2026. Integracja przekazuje więc kod do kreatora,
który właśnie czeka na zgodę. Przekierowanie wykonuje przeglądarka, a nie serwery SolarEdge,
dlatego adres musi być osiągalny tylko z Twojej przeglądarki.

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

## Odświeżanie na żądanie

`homeassistant.update_entity` na sensorach tej integracji **nie wywołuje API**, więc
automatyzacje „odśwież co X minut” znane z wbudowanej integracji nie zużywają kredytów.
Odczyt poza harmonogramem: akcja `solaredge_v2.refresh`.

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

## Pochodzenie

Fork [trooperthorn/ha_int_solaredge](https://github.com/trooperthorn/ha_int_solaredge) (MIT).
Z oryginału pochodzą klient API i modele odpowiedzi. Logowanie, harmonogram, licznik kredytów,
pamięć podręczna i sensory zostały napisane od nowa.

---

## English summary

Home Assistant integration for the SolarEdge Monitoring API v2 (Site Access / OAuth), built
for the free 2,000-credit plan:

- one credit per poll, with polling only in daylight;
- a local credit counter with a billing-cycle reset and a portal correction action;
- no API calls on restart or reload, because state is cached in `.storage`;
- fixed or adaptive polling interval;
- authorization by pasting the redirect URL, because SolarEdge does not return the OAuth
  `state` parameter.
