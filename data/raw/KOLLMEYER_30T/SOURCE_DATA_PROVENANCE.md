# KOLLMEYER - Samsung INR21700-30T (Kollmeyer 2022)

Download required. Place the `.mat` files directly in this directory.

## Summary

| | |
|---|---|
| Cell | Samsung SDI INR21700-30T, NMC chemistry, 3.0 Ah |
| Form factor | cylindrical (21700) |
| Test temperatures | -20, -10, 0, 10, 25, 40 °C (six temperatures) |
| Test types | HPPC (multi-rate), drive cycles, mixed cycles, capacity checks, C/20 OCV |
| Raw format | MATLAB `.mat` |
| Equipment | Digatron LBT21024 + thermal chamber (McMaster University) |

## Where to get the data

    https://data.mendeley.com/datasets/9xyvy2njj3/2

DOI: `10.17632/9xyvy2njj3.2`. License: CC-BY-4.0.

## Expected filenames

celljar scans for `.mat` files following Kollmeyer's naming convention
documented on the Mendeley page:

    {Date}_{Time}_{Descriptor}_{Temperature}_{CellType}.mat

Examples (anticipated, mirroring HNEI's Kollmeyer convention):

    {date} 25degC_HPPC_1C_Sam30T.mat
    {date} n20degC_HPPC_2C_Sam30T.mat       (n = negative)
    {date} 25degC_UDDS_Sam30T.mat
    {date} 25degC_US06_Sam30T.mat
    {date} 25degC_HWFET_Sam30T.mat
    {date} 25degC_LA92_Sam30T.mat
    {date} 25degC_Mix1_Sam30T.mat .. Mix8
    {date} 25degC_Cap_1C_Sam30T.mat .. Cap_2C, Cap_3C
    {date} C20 OCV Test_C20_25dC.mat

HPPC at four discharge C-rates (1, 2, 6, 12C) and four charge C-rates
(0.5, 1, 2, 4C), with reduced rates at low temperatures where the cell
cannot sustain high current.

## Test protocol

Kollmeyer's standard HPPC + drive-cycle protocol applied to the Samsung
INR21700-30T. Each HPPC sweep: 10% SOC steps; at each step a sequence of
charge and discharge pulses at multiple C-rates characterizes the cell's
impedance vs SOC vs temperature. Drive cycles include the standard EV
characterization profiles (UDDS, US06, HWFET, LA92) plus eight mixed
randomized cycles (Mix1..Mix8) for ML training/validation.

C/20 OCV characterization gives the equilibrium OCV-SOC curve.
Capacity checks at 1/2/3 C provide rate-dependent capacity.

## License / citation

**CC BY 4.0** (per the Mendeley Data record). Attribution required;
commercial use permitted; no ShareAlike.

Cite as:

    Kollmeyer, P., Skells, M. (2022). Samsung INR21700 30T 3Ah Li-ion
    Battery Data. Mendeley Data, v2. https://doi.org/10.17632/9xyvy2njj3.2

License text: https://creativecommons.org/licenses/by/4.0/

## After downloading

    python examples/demo_end_to_end.py

The demo picks up KOLLMEYER_30T files automatically if present and
harmonizes them into the canonical schema alongside the other sources.
