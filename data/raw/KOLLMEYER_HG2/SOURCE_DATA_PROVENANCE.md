# KOLLMEYER - LG INR18650HG2 (Kollmeyer 2020)

Download required. Place the `.mat` files directly in this directory.

## Summary

| | |
|---|---|
| Cell | LG Chem INR18650HG2, NMC (high-power "H-NMC" variant), graphite + SiO anode, 3.0 Ah |
| Form factor | cylindrical (18650) |
| Test temperatures | -20, -10, 0, 10, 25, 40 °C (six temperatures, anticipated) |
| Test types | HPPC (USABC-style 10s pulses), drive cycles, capacity checks |
| Raw format | MATLAB `.mat` |
| Equipment | Digatron LBT21024 + thermal chamber (McMaster University) |

## Where to get the data

    https://data.mendeley.com/datasets/cp3473x7xv/3

DOI: `10.17632/cp3473x7xv.3`. License: CC-BY-4.0.

The dataset includes example deep neural network xEV SOC estimator code.
celljar's ingester focuses on the raw test data; the example code is
ignored.

## Expected filenames

celljar scans for `.mat` files following Kollmeyer's naming convention.
The exact convention is not fully documented on the Mendeley landing
page; the patterns below are inferred from the same researcher's
HNEI Panasonic 18650PF dataset and may need adjustment after the user
downloads the actual files. See code comments in
`celljar/ingest/kollmeyer_hg2.py` for inferred-from-HNEI patterns.

Anticipated examples:

    {date} 25degC_HPPC_LG_HG2.mat
    {date} n20degC_HPPC_LG_HG2.mat       (n = negative)
    {date} 25degC_UDDS_LG_HG2.mat
    {date} 25degC_US06_LG_HG2.mat
    {date} 25degC_HWFET_LG_HG2.mat
    {date} 25degC_LA92_LG_HG2.mat
    {date} 25degC_NN_LG_HG2.mat

## Test protocol

Kollmeyer's USABC-style HPPC: at each 10% SOC step (after a 1-hour rest
to capture pseudo-OCV), a 10-second discharge pulse, 60-second rest,
10-second charge pulse sequence characterizes the cell's impedance.
Drive cycles include the standard EV characterization profiles (UDDS,
US06, HWFET, LA92) plus the Neural Network (NN) randomized training
profile.

Capacity tests provide the reference capacity at standard C-rates.

## License / citation

**CC BY 4.0** (per the Mendeley Data record). Attribution required;
commercial use permitted; no ShareAlike.

Cite as:

    Kollmeyer, P., Vidal, C., Naguib, M., Skells, M. (2020). LG 18650HG2
    Li-ion Battery Data and Example Deep Neural Network xEV SOC
    Estimator Script. Mendeley Data, v3.
    https://doi.org/10.17632/cp3473x7xv.3

License text: https://creativecommons.org/licenses/by/4.0/

## After downloading

    python examples/demo_end_to_end.py

The demo picks up KOLLMEYER_HG2 files automatically if present and
harmonizes them into the canonical schema alongside the other sources.
