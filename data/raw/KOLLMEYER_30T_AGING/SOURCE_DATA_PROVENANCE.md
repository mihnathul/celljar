# KOLLMEYER 30T_AGING - Samsung INR21700-30T fast-charge aging (Duque 2023, updated 2025)

Download required. Place the deposit files directly in this directory.
celljar reads the protocol `.zip` archives in place (streams the `.mat`
members) - do NOT unzip them.

## Summary

| | |
|---|---|
| Cells | 6 x Samsung SDI INR21700-30T, NMC chemistry, 3.0 Ah |
| Form factor | cylindrical (21700) |
| Ambient | 25 degC (environmental chamber) |
| Protocols | 5 fast-charge profiles across 6 cells: CC, CC2 (CC replicate), BC, BCR, BCNP, BCNP_1s |
| Aging target | cycled until ~70% SOH capacity |
| Checkup | every ~30 fast-charge / drive-cycle cycles: 0.5C / 1C / 2C discharges + HPPC; OCV (C/20) every ~60 cycles |
| Raw format | MATLAB `.mat` inside per-protocol `.zip` archives, plus an `.xlsx` per-checkpoint summary |
| Equipment | Arbin battery cycler + thermal chamber (McMaster University) |

## Where to get the data

    https://borealisdata.ca/dataset.xhtml?persistentId=doi:10.5683/SP3/UYPYDJ

DOI: `10.5683/SP3/UYPYDJ`. License: CC-BY-4.0.

## Expected files

The deposit ships (filenames as published on Borealis):

    00-Readme.txt
    01-Test_Description.pdf
    02-Data_Summary_SIX_Protocols.xlsx      (per-checkpoint capacity + SOH for all 6 cells)
    03-CONSTANT_CURRENT_Cycles_0_to_1000.zip
    04-CONSTANT_CURRENT_Cycles_1000_to_1908.zip
    05-BOOST_CHARGING_Cycles_0_to_1000.zip
    06-BOOST_CHARGING_Cycles_1000_to_1908.zip
    07-BOOST_CHARGING_WITH_REST.zip
    08-BOOST_CHARGING_NEG_PULSES_Cycles_1_to_1000.zip
    09-BOOST_CHARGING_NEG_PULSES_Cycles_1000_to_1730.zip
    10-BOOST_CHARGING_NEG_PULSES_1s_PERIOD.zip
    11-CONSTANT_CURRENT_SECOND_CELL.zip

Each protocol `.zip` contains `{Protocol} protocol/Cycle XXXX/<test files>.mat`.
celljar's ingester (`celljar/ingest/kollmeyer_30t_aging.py`) streams the `.mat`
members directly from the archives - no manual extraction needed.

The `.xlsx` summary (served by the Borealis API as TAB-separated) is the
source-published per-checkpoint capacity / SOH table; it populates
`cycle_summary` for all six cells, including any cell whose raw `.zip` is
unavailable.

## Protocols (5 fast-charge strategies, per the Test Description PDF)

All charge from 10% to 80% SOC in ~15 minutes, followed by a drive-cycle
discharge. The research question: does pulse-shaping the charge current
reduce fast-charge aging vs plain constant current?

| Code | Name | Profile |
|---|---|---|
| CC | Constant Current | 2.8C CC/CV, baseline |
| CC2 | Constant Current TWO | second CC cell (added in the 2025 update) |
| BC | Boost Charging | 4C for 5 min + 2.2C for 10 min |
| BCR | Boost Charging with Rest | 1.9 s pulse + 0.1 s rest |
| BCNP | Boost Charging with Negative Pulse | 1.9 s 4.324C + 0.1 s -2.162C (~2 s period) |
| BCNP_1s | Boost Charging with 1 s Negative Pulse | as BCNP but 1 s pulse period |

Code/name pairs are the originator's literal labels from the
`02-Data_Summary_SIX_Protocols.xlsx` legend.

## License / citation

**CC BY 4.0** (per the Borealis Data record). Attribution required;
commercial use permitted; no ShareAlike.

Cite as:

    Duque, J., Kollmeyer, P. J., Naguib, M. (2023, updated 2025). Battery
    Aging Dataset for 15 Minute Fast Charging of Samsung 30T Cells. Borealis
    Data Repository. https://doi.org/10.5683/SP3/UYPYDJ

License text: https://creativecommons.org/licenses/by/4.0/

## After downloading

    python examples/demo_end_to_end.py

The demo picks up KOLLMEYER_30T_AGING automatically if the `.zip` archives +
summary `.xlsx` are present and harmonizes them alongside the other sources.
