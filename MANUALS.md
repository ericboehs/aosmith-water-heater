# References: A.O. Smith manuals

The manufacturer's documents for the HPS10-50H45DV (part 100350432) and its
family. **Links only:** the PDFs are copyrighted, so no copies are kept in
this repository.

## Service Handbook, Hybrid Electric Heat Pump Water Heater

- **Number:** 2000620230, REV.C (December 2023), 86 pages.
- **Covers:** HPS10-50/66/80H45DV by name, plus the HPTS/HPTA/HPTV wholesale
  models and the Lochinvar, Reliance, State and American variants.
- **URL:** <https://assets.hotwater.com/damroot/Original/10009/2000620230_3000059035_500045079_SERVICE%20HNDBK,SMITH,208V-240V120V,NGHP.pdf>

| Page | Section | What it gave this project |
|---|---|---|
| 9–10 | Operating modes; Other operational modes (HPO/ICE) | mode behaviour and the heat pump's operating limits |
| **11** | **Accessing the Maintenance Display, Table 4** | **P1–P10 and H1–H4.** Hold Up+Down for 3 s; it reverts after ~15 s idle. P1/P2 tank, P3 coil, P4 discharge, P5 suction, **P6 ambient ("within heat pump component assembly")**, P7 EEV pulses, P8/P9 upper/lower element, P10 software version, H1 newest active fault, H2–H4 history. Matched to telemetry fields in research phase 51 |
| 14 | Limp Mode; Clearing Error Code | Limp modes 1–3. `CLR` (Up+Down until shown) *temporarily* clears an error code |
| **15–17** | **Control Assembly Diagnostic Code Chart** | **the fault codes and corrective actions** (001–086); the source of `FAULTS` in `custom_components/icomm_local/const.py` |
| 18–23 | Maintenance | elements, T&P valve, air filter, evaporator coil, ECO, anode |
| 25–60 | Replacement procedures | Smart Valve, control assembly, daughter board, thermistors, EEV solenoid, fan |
| 64–66 | Compressor and fan check flowcharts | |
| 67 | A.O. Smith app setup and connectivity | |
| **75–76** | **Daughter Board and Control Board overviews** | connector pinouts: leak detection (pin 24), bypass/shut-off valve, EEV, RS-485 between the boards |
| 78–85 | Repair parts; wire diagrams | |

## Installation Instructions and Use & Care Guide (Lowe's retail)

- **Number:** 100346825_2000604721, REV.B (August 2022), 40 pages.
- **URL:** <https://www.aosmithatlowes.com/media/1712/2000604721.pdf>

| Section | What it gave this project |
|---|---|
| Installation, step 9 | the automatic cold-water shut-off valve |
| Installation, step 15 | the leak detection sensor: it notifies only (code, light, beeper). In the 10-26 test the heater also closed the valve, stopped heating and dropped its active target to 120 °F (research phase 52) |
| Operation | **mode timers:** Electric 1–7 days (default 3); Vacation 1–99 days or "permanently ON". Heat pump operation is allowed from 37 to 120 °F ambient. EcoPort (CTA-2045) port |
| Troubleshooting (p. 25+) | the same error codes as the handbook, from the owner's side |

## Related

- **Service Handbook 100268628 (2016):** the previous-generation HPTU
  handbook. Older hardware; useful only for comparison.
  <https://assets.aosmith.com/damroot/Original/10004/100268628.pdf>
- **Addendum 100392298:** retires error 044 (anode depleted) for this family.
  Cited by [stumpylog/aosmith-ble](https://github.com/stumpylog/aosmith-ble/blob/main/docs/research/sources.md).
- **Local copies (owner's machine only):** `~/Documents/aosmith-water-heater/manuals/`.
  Text extracts `sh.txt` and `uc.txt` are made with `pdftotext -layout`.
