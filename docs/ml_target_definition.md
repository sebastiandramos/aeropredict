# ML Target Definition — Flight Arrival Delay

Definition
----------

delay_target = actual_arrival - scheduled_arrival (minutes)

Where both timestamps are parsed as timezone-aware datetimes. The resulting value is a signed float in minutes; negative values indicate early arrivals.

Edge cases
----------

- Missing scheduled arrival: such rows are flagged with schedule_missing=True. Rows are preserved in the dataset to avoid data loss, but delay_target will be NaN for them.
- Missing actual arrival: rows without actual_arrival are removed because we cannot compute the target.
- Negative delays (early arrivals): kept as-is and considered valid training signal.

Statistics and interpretation
---------------------------

Typical behaviour observed in our mock dataset:

- Central tendency: approximately 0 ± 15 minutes
- Significant delays: > 60 minutes — operationally important

Use in modeling
---------------

Files
-----

- data/processed/target_dataset.parquet — final dataset with delay_target and schedule_missing
- reports/figures/delay_distribution.png — histogram of delay_target
- reports/figures/delay_by_hour.png — boxplot of delay by hour_of_day (if hour available)
