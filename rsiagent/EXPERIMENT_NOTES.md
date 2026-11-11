# Historical date-count reports

The historical day-difference experiment reports in this directory used an
incorrect oracle for dates exclusive of both endpoints. They also reused a
held-out task as a deep-training target. The original artifacts are preserved
for audit, but their baseline, evolved scores, and claimed improvements are
invalid and must not be used as evidence. The scripts and oracle were corrected;
a new independent run is required. No GDPevo results are claimed here.

The historical modular-exponentiation report reused a test task as a development target; it is not a valid held-out comparison. Its unused reference table was inaccurate; the container oracle itself computed powers directly. Original artifacts are preserved.
